import os
import json
from contextlib import asynccontextmanager
from fastapi import FastAPI, Depends, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from backend.models.database import (
    Base, Athlete, Activity, RecoverySnapshot,
    AthleteFeedback, InjuryLog,
    ChatSession, ChatMessage
)
from backend.services.fit_importer import parse_fit_file
from backend.services import coros_watch_sync, plan_flows, refresh_job
from backend.utils.timezone import (
    get_local_now, get_local_today, get_timezone_name, is_valid_timezone, set_athlete_timezone,
)
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from dotenv import load_dotenv

load_dotenv(override=True)

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./phoenix_coach.db")
connect_args = {}
if DATABASE_URL.startswith("sqlite"):
    connect_args["check_same_thread"] = False
# pool_pre_ping: test pooled connections before handing them to a request.
# 2026-08-20: Render restarted the Postgres container and killed the pool's
# 9-hour-old connections; without the ping every request drew a dead socket
# ("SSL connection has been closed unexpectedly") until the service restarted.
# pool_recycle retires anything older than 5 min as a second layer.
engine = create_engine(
    DATABASE_URL,
    connect_args=connect_args,
    pool_pre_ping=True,
    pool_recycle=300,
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# Create tables
Base.metadata.create_all(bind=engine)


def _migrate_athletes(target_engine):
    """Add athletes columns introduced after the table was first created.

    Factored out of _ensure_columns so the lthr backfill can be tested against
    a throwaway engine.
    """
    from sqlalchemy import inspect, text
    inspector = inspect(target_engine)
    if "athletes" not in inspector.get_table_names():
        return
    existing = {c["name"] for c in inspector.get_columns("athletes")}

    if "timezone" not in existing:
        with target_engine.begin() as conn:
            conn.execute(text("ALTER TABLE athletes ADD COLUMN timezone VARCHAR"))
        print("✅ Migration: added athletes.timezone")

    # Columns added after the table existed. The tune_race_* trio the dict
    # once carried was removed 2026-09-09 (see CLAUDE.md); the columns stay in
    # prod, nullable and unread — there is no migration framework to drop them.
    added_columns = {
        "personal_model": "JSON",
    }
    for col, sql_type in added_columns.items():
        if col not in existing:
            with target_engine.begin() as conn:
                conn.execute(text(f"ALTER TABLE athletes ADD COLUMN {col} {sql_type}"))
            print(f"✅ Migration: added athletes.{col}")

    if "lthr" not in existing:
        # One-shot backfill: hr_max always held COROS LTHR (ingestion wrote it
        # there), so move the value and null the mislabeled column. All three
        # statements stay inside this guard — re-running them on a later boot
        # would wipe a future genuine hr_max.
        with target_engine.begin() as conn:
            conn.execute(text("ALTER TABLE athletes ADD COLUMN lthr INTEGER"))
            conn.execute(text("UPDATE athletes SET lthr = hr_max"))
            conn.execute(text("UPDATE athletes SET hr_max = NULL"))
        print("✅ Migration: added athletes.lthr, backfilled from hr_max, nulled hr_max")

    # Every boot, not one-shot: during the lthr deploy an old instance re-wrote
    # hr_max with the LTHR value after the backfill nulled it. hr_max == lthr is
    # always that stale copy (a real max HR sits well above LTHR), so clearing
    # the equal case is safe to repeat and heals any future overlap the same way.
    with target_engine.begin() as conn:
        conn.execute(text(
            "UPDATE athletes SET hr_max = NULL "
            "WHERE hr_max IS NOT NULL AND hr_max = lthr"
        ))


def _ensure_columns():
    """Add columns introduced after a table was first created.

    create_all() only creates missing *tables*, never missing columns, so an
    existing deployment would keep the old schema. Plain ADD COLUMN works on
    both SQLite and PostgreSQL.
    """
    from sqlalchemy import inspect, text
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())

    _migrate_athletes(engine)

    if "injury_logs" in tables:
        existing = {c["name"] for c in inspector.get_columns("injury_logs")}
        if "expected_recovery_date" not in existing:
            with engine.begin() as conn:
                conn.execute(text("ALTER TABLE injury_logs ADD COLUMN expected_recovery_date DATE"))
            print("✅ Migration: added injury_logs.expected_recovery_date")

    if "recovery_snapshots" in tables:
        existing = {c["name"] for c in inspector.get_columns("recovery_snapshots")}
        for col, sql_type in (("hrv_normal_low", "FLOAT"), ("hrv_normal_high", "FLOAT"),
                              ("sleep_hr_avg", "FLOAT"), ("sleep_hr_min", "FLOAT"),
                              ("pred_half_s", "INTEGER"), ("pred_marathon_s", "INTEGER")):
            if col not in existing:
                with engine.begin() as conn:
                    conn.execute(text(f"ALTER TABLE recovery_snapshots ADD COLUMN {col} {sql_type}"))
                print(f"✅ Migration: added recovery_snapshots.{col}")


_ensure_columns()

# --- Singleton instances ---
_kb_instance = None


def get_kb():
    """Get the singleton KnowledgeBase instance."""
    global _kb_instance
    if _kb_instance is None:
        from backend.core.knowledge_base import KnowledgeBase
        _kb_instance = KnowledgeBase.get_instance()
    return _kb_instance


from backend.core.llm_client import check_llm_available
from backend.services.constraint_enforcer import sport_days
from backend.models.database import week_plan


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize singletons and check the LLM on startup."""
    # 1. Pre-load the KnowledgeBase (once, not per-request)
    get_kb()
    print("✅ KnowledgeBase singleton initialized.")

    # 2. Check LLM availability
    llm_status = check_llm_available()
    if llm_status.get("status") == "connected":
        print(f"✅ LLM ({llm_status.get('provider')}) connected. Model '{llm_status.get('model')}' is available.")
    else:
        print(f"⚠️  LLM unavailable: {llm_status.get('detail', 'disconnected')}")
    
    # 3. Set training_start_date on startup if not set
    from datetime import date, timedelta
    db = SessionLocal()
    try:
        athlete = db.query(Athlete).first()

        # Restore the athlete's last known timezone so dates are correct even
        # before the phone checks in after a restart.
        if athlete and athlete.timezone:
            if set_athlete_timezone(athlete.timezone):
                print(f"✅ Athlete timezone restored: {athlete.timezone}")
            else:
                print(f"⚠️ Stored timezone '{athlete.timezone}' is invalid — using default.")
        print(f"🕒 Active timezone: {get_timezone_name()} (today = {get_local_today()})")

        if athlete and athlete.training_start_date is None:
            # Query the earliest activity
            earliest_act = db.query(Activity).order_by(Activity.start_time.asc()).first()
            if earliest_act and earliest_act.start_time:
                athlete.training_start_date = earliest_act.start_time.date()
                db.commit()
                print(f"✅ Set athlete training_start_date to earliest activity date: {athlete.training_start_date}")
            else:
                # Fallback to 4 weeks ago
                athlete.training_start_date = get_local_today() - timedelta(weeks=4)
                db.commit()
                print(f"✅ Set athlete training_start_date to default (4 weeks ago): {athlete.training_start_date}")
    except Exception as e:
        print(f"⚠️ Failed to set training_start_date on startup: {e}")
    finally:
        db.close()
    
    yield  # App runs here


app = FastAPI(title="Phoenix Adaptive Coach Backend", lifespan=lifespan)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Dependency
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@app.get("/")
def read_root():
    return {"message": "Phoenix Coach Backend is running locally."}


@app.get("/health")
async def health_check():
    """Health check — includes LLM status."""
    llm_status = check_llm_available()
    return {
        "backend": "ok",
        "llm": llm_status,
    }


@app.get("/athlete")
def get_athlete(db: Session = Depends(get_db)):
    athlete = db.query(Athlete).first()
    if not athlete:
        # Create default athlete if none exists
        athlete = Athlete(name="New Athlete")
        db.add(athlete)
        db.commit()
        db.refresh(athlete)
    return athlete


def _prediction_block(db, athlete):
    """Riegel prediction for the goal race from the athlete's own race-effort
    activities. Read-only; the target stays whatever Alex set."""
    from backend.services.personal_model import get_model, race_prediction, _run_activities

    activities = _run_activities(db)
    model = get_model(db, athlete, activities)
    if not model or not model.get("lthr"):
        return None
    return race_prediction(activities, model["lthr"], athlete.race_distance,
                           athlete.target_finish_time, bests=model.get("bests"))


def _coros_prediction_block(db, athlete):
    """COROS's own race predictions (stored daily by the MCP sync since
    2026-10-08): today's half and marathon, the change since the oldest
    value in the last 90 days, and the gap to the athlete's goal when the goal
    distance is one of the two. None until a row carries them."""
    from datetime import timedelta
    from backend.services.pace_model import fmt_hms

    today = get_local_today()
    rows = db.query(RecoverySnapshot).filter(
        RecoverySnapshot.date >= today - timedelta(days=90),
        (RecoverySnapshot.pred_half_s.isnot(None)) | (RecoverySnapshot.pred_marathon_s.isnot(None)),
    ).order_by(RecoverySnapshot.date.desc()).all()
    if not rows:
        return None
    latest, oldest = rows[0], rows[-1]

    def _delta(attr):
        a, b = getattr(latest, attr), getattr(oldest, attr)
        return int(a - b) if a is not None and b is not None and latest.date != oldest.date else None

    def _parse_hms(text):
        try:
            parts = [int(x) for x in str(text).split(":")]
        except (TypeError, ValueError):
            return None
        return parts[0] * 3600 + parts[1] * 60 + (parts[2] if len(parts) > 2 else 0) if len(parts) in (2, 3) else None

    goal_attr = {"Marathon": "pred_marathon_s", "Half Marathon": "pred_half_s", "Half": "pred_half_s"}.get(
        athlete.race_distance or "")
    goal_s = _parse_hms(athlete.target_finish_time) if athlete.target_finish_time else None
    goal_pred = getattr(latest, goal_attr) if goal_attr else None
    gap_s = int(goal_pred - goal_s) if goal_pred is not None and goal_s else None
    return {
        "date": latest.date.isoformat(),
        "half": fmt_hms(latest.pred_half_s) if latest.pred_half_s else None,
        "half_sec": latest.pred_half_s,
        "marathon": fmt_hms(latest.pred_marathon_s) if latest.pred_marathon_s else None,
        "marathon_sec": latest.pred_marathon_s,
        "since_date": oldest.date.isoformat(),
        "days_tracked": (latest.date - oldest.date).days + 1,
        "half_delta_sec": _delta("pred_half_s"),
        "marathon_delta_sec": _delta("pred_marathon_s"),
        "goal_distance": athlete.race_distance if goal_attr else None,
        "goal_gap_sec": gap_s,
        "goal_gap_pct": round(gap_s / goal_s * 100, 1) if gap_s is not None and goal_s else None,
    }


def _personal_block(db, athlete):
    """The Recent tab's long-run ledger. None until an LTHR is known."""
    from backend.services.personal_model import get_model, long_run_ledger, _run_activities

    if athlete is None:
        return None
    activities = _run_activities(db)
    model = get_model(db, athlete, activities)
    if not model or not model.get("lthr"):
        return None
    return {"ledger": long_run_ledger(activities, model["lthr"]),
            "model_runs": (model.get("hr_model") or {}).get("n")}


@app.get("/athlete/profile")
def get_athlete_profile(db: Session = Depends(get_db)):
    """Return the athlete's profile including race objectives and schedule."""
    athlete = db.query(Athlete).first()
    if not athlete:
        athlete = Athlete(name="New Athlete")
        db.add(athlete)
        db.commit()
        db.refresh(athlete)

    return {
        "name": athlete.name,
        "age": athlete.age,
        "weight_kg": athlete.weight_kg,
        "race_name": athlete.race_name,
        "race_type": athlete.race_type or "Triathlon",
        "race_distance": athlete.race_distance,
        "race_date": str(athlete.race_date) if athlete.race_date else None,
        **sport_days(athlete),
        "target_finish_time": athlete.target_finish_time,
        "training_start_date": str(athlete.training_start_date) if athlete.training_start_date else None,
        "timezone": athlete.timezone,
        "active_timezone": get_timezone_name(),
        "prediction": _prediction_block(db, athlete),
        "coros_prediction": _coros_prediction_block(db, athlete),
    }


@app.put("/athlete/profile")
def update_athlete_profile(body: dict, db: Session = Depends(get_db)):
    """Update the athlete's profile."""
    from datetime import date as date_type

    athlete = db.query(Athlete).first()
    if not athlete:
        athlete = Athlete(name="New Athlete")
        db.add(athlete)

    # A disabled sport must not leave ghost sessions in the stored week, so
    # availability changes re-enforce the plan below. Snapshot to detect them.
    availability_fields = ("swim_days", "bike_days", "run_days", "strength_days")
    old_availability = {f: getattr(athlete, f, None) for f in availability_fields}

    # Update only provided fields
    updatable = [
        "name", "age", "weight_kg", "race_name", "race_type", "race_distance",
        "swim_days", "bike_days", "run_days", "strength_days", "target_finish_time",
    ]
    for field in updatable:
        if field in body:
            setattr(athlete, field, body[field])

    # Handle race_date specially (string → date)
    if "race_date" in body:
        rd = body["race_date"]
        if rd:
            athlete.race_date = date_type.fromisoformat(rd)
        else:
            athlete.race_date = None
            
    # Handle training_start_date specially (string → date)
    if "training_start_date" in body:
        tsd = body["training_start_date"]
        if tsd:
            athlete.training_start_date = date_type.fromisoformat(tsd)
        else:
            athlete.training_start_date = None

    # The phone reports its current timezone, so travelling updates this on its
    # own. Ignore garbage rather than 400-ing — this rides along with unrelated
    # profile saves and must never block them.
    if "timezone" in body:
        tz = body["timezone"]
        if tz and is_valid_timezone(tz):
            if athlete.timezone != tz:
                print(f"🕒 Timezone changed: {athlete.timezone} → {tz}")
            athlete.timezone = tz
            set_athlete_timezone(tz)
        elif tz:
            print(f"⚠️ Ignoring invalid timezone from client: {tz!r}")

    db.commit()
    db.refresh(athlete)
    print(f"Profile updated: {athlete.name}, race={athlete.race_name} on {athlete.race_date}, start={athlete.training_start_date}")

    # A prompt-only constraint is a suggestion; the stored week must obey the
    # new availability too. iOS never decodes this body, so the extra key is
    # safe — a future nudge ("Replan remaining week?") can read it.
    response = {"status": "ok", "message": "Profile updated"}
    if any(getattr(athlete, f) != old_availability[f] for f in availability_fields):
        enforcement = plan_flows.reenforce_current_week(db, athlete)
        if enforcement is not None:
            response["plan_enforcement"] = enforcement
    return response

@app.get("/athlete/injuries")
def get_athlete_injuries(db: Session = Depends(get_db)):
    from backend.models.database import InjuryLog
    athlete = db.query(Athlete).first()
    if not athlete:
        return []
    injuries = db.query(InjuryLog).filter(InjuryLog.athlete_id == athlete.id).order_by(InjuryLog.date_reported.desc()).all()
    return [
        {
            "id": inj.id,
            "date_reported": str(inj.date_reported) if inj.date_reported else None,
            "body_part": inj.body_part,
            "status": inj.status,
            "severity": inj.severity,
            "notes": inj.notes,
            "affected_sports": inj.affected_sports,
            "expected_recovery_date": str(inj.expected_recovery_date) if inj.expected_recovery_date else None,
        } for inj in injuries
    ]

@app.post("/athlete/injuries")
def create_athlete_injury(body: dict, db: Session = Depends(get_db)):
    from backend.models.database import InjuryLog
    from datetime import date
    
    athlete = db.query(Athlete).first()
    if not athlete:
        raise HTTPException(status_code=404, detail="Athlete not found")
        
    injury = InjuryLog(
        athlete_id=athlete.id,
        date_reported=date.fromisoformat(body["date_reported"]) if "date_reported" in body and body["date_reported"] else get_local_today(),
        body_part=body.get("body_part"),
        status=body.get("status", "Active"),
        severity=body.get("severity"),
        notes=body.get("notes"),
        affected_sports=body.get("affected_sports"),
        # Optional. NULL means the injury blocks training until resolved by hand.
        expected_recovery_date=(
            date.fromisoformat(body["expected_recovery_date"])
            if body.get("expected_recovery_date") else None
        ),
    )
    db.add(injury)
    db.commit()
    db.refresh(injury)
    return {"status": "ok", "message": "Injury logged", "id": injury.id}

@app.put("/athlete/injuries/{injury_id}")
def update_athlete_injury(injury_id: int, body: dict, db: Session = Depends(get_db)):
    from backend.models.database import InjuryLog
    injury = db.query(InjuryLog).filter(InjuryLog.id == injury_id).first()
    if not injury:
        raise HTTPException(status_code=404, detail="Injury not found")
        
    for field in ["status", "severity", "notes", "affected_sports", "body_part"]:
        if field in body:
            setattr(injury, field, body[field])

    if "expected_recovery_date" in body:
        from datetime import date as date_type
        erd = body["expected_recovery_date"]
        injury.expected_recovery_date = date_type.fromisoformat(erd) if erd else None

    db.commit()
    return {"status": "ok", "message": "Injury updated"}


@app.delete("/athlete/injuries/{injury_id}")
def delete_athlete_injury(injury_id: int, db: Session = Depends(get_db)):
    """Remove an injury row outright.

    Distinct from resolving: Resolved keeps the history `data_agent` reads back,
    delete is for a row logged by mistake. iOS offered a Delete swipe from the
    start and only ever removed it from the local array, so the row returned on
    the next fetch — and, if it was Active, kept stripping sessions.
    """
    from backend.models.database import InjuryLog
    injury = db.query(InjuryLog).filter(InjuryLog.id == injury_id).first()
    if not injury:
        raise HTTPException(status_code=404, detail="Injury not found")
    db.delete(injury)
    db.commit()
    return {"status": "ok", "message": "Injury deleted"}


@app.post("/coach/issue/preview")
def preview_issue(body: dict, db: Session = Depends(get_db)):
    """
    Turn free text ("my right calf is shot") into a proposed plan change.

    Read-only — nothing is written until POST /coach/issue/apply. This exists as
    its own endpoint so the iOS app can re-run a proposal after the athlete edits
    the severity or duration on the confirmation card, without re-sending the
    whole conversation through chat.
    """
    from backend.services.issue_triage import build_proposal, extract_issue

    message = (body or {}).get("message", "")
    if not message:
        raise HTTPException(status_code=400, detail="Message is required")

    # An explicitly supplied issue skips extraction — that's the "athlete edited
    # the card" path, where the structured values are already known.
    issue = (body or {}).get("issue") or extract_issue(message)
    if not issue:
        return {"detected": False}

    proposal = build_proposal(db, issue)
    if not proposal:
        return {"detected": False, "reason": "Nothing in this week's remaining plan is affected."}

    return {"detected": True, "proposal": proposal}


@app.post("/coach/issue/apply")
def apply_issue_endpoint(body: dict, db: Session = Depends(get_db)):
    """
    Commit a confirmed issue: log the injury and rebuild the affected days.

    Body: `{"issue": {...}, "choices": {"Thursday": "swap", "Saturday": "rest"}}`
    """
    from backend.services.issue_triage import apply_issue

    issue = (body or {}).get("issue")
    if not issue:
        raise HTTPException(status_code=400, detail="issue is required")

    choices = (body or {}).get("choices") or {}
    if not isinstance(choices, dict):
        raise HTTPException(status_code=400, detail="choices must be a map of day → 'swap'|'rest'")

    try:
        result = apply_issue(db, issue, choices)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    plan_flows.watch_sync_this_week(db)
    return result


@app.post("/coach/recovery/apply")
def apply_recovery_endpoint(body: dict, db: Session = Depends(get_db)):
    """
    Commit a confirmed recovery: resolve the injury and rebuild the remaining
    days it turned into rest.

    Body: `{"injury_id": 3, "rebuild": true}`. Resolve always succeeds on an
    active injury; a failed rebuild reports `rebuild_error` without resurrecting
    the injury or touching the plan.
    """
    from backend.services.issue_triage import apply_recovery

    injury_id = (body or {}).get("injury_id")
    if not isinstance(injury_id, int):
        raise HTTPException(status_code=400, detail="injury_id is required")

    try:
        result = apply_recovery(db, injury_id, rebuild=bool((body or {}).get("rebuild", True)))
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    plan_flows.watch_sync_this_week(db)
    return result


@app.post("/coach/travel/apply")
def apply_travel_endpoint(body: dict, db: Session = Depends(get_db)):
    """
    Commit confirmed travel days: write TravelDay rows, rest the blocked days,
    rebuild the open remainder of the week around them.

    Body: `{"dates": ["2026-08-21", "2026-08-22"], "note": "work trip"}`.
    Dates outside today→Sunday are ignored; all-ignored is a 400, not a 404 —
    there is nothing to look up, the request just says nothing actionable.
    """
    from backend.services.issue_triage import apply_travel

    dates = (body or {}).get("dates")
    if not isinstance(dates, list) or not dates:
        raise HTTPException(status_code=400, detail="dates is required")

    try:
        result = apply_travel(db, dates, note=str((body or {}).get("note") or ""))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    plan_flows.watch_sync_this_week(db)
    return result


@app.post("/sync")
async def sync_data(background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    """
    Triggers a full sync:
    1. Scan fit_examples for new files
    2. Trigger COROS scraper for recovery data
    """
    # 1. FIT Import
    fit_dir = "fit_examples"
    if os.path.exists(fit_dir):
        for filename in os.listdir(fit_dir):
            if filename.endswith(".fit"):
                # Check if already in DB
                if not db.query(Activity).filter(Activity.id == filename).first():
                    print(f"Importing {filename}...")
                    activity, records = parse_fit_file(os.path.join(fit_dir, filename))
                    db.add(activity)
                    db.add_all(records)
        db.commit()

    # 2. Scraper (in background)
    # background_tasks.add_task(run_coros_sync)
    
    return {"status": "Sync initiated", "message": "FIT files processed. Scraper starting in background."}


@app.get("/dashboard")
def get_dashboard_data(db: Session = Depends(get_db)):
    # Return full history for dashboard analysis
    activities = db.query(Activity).order_by(Activity.start_time.desc()).all()
    recovery = db.query(RecoverySnapshot).order_by(RecoverySnapshot.date.desc()).all()
    athlete = db.query(Athlete).first()
    
    from backend.services.volume_ledger import weekly_volume

    return {
        "athlete": athlete,
        "activities": activities,
        "recovery": recovery,
        "personal": _personal_block(db, athlete),
        # Recent tab's volume ledger: hours per sport per week, 8 weeks,
        # with the sports that went to zero (docs/COROS_MCP.md, reader 2).
        "volume": weekly_volume(db),
    }


@app.get("/coaching")
def get_coaching_recommendation(db: Session = Depends(get_db)):
    """Generate a coaching recommendation using the 2-agent pipeline."""
    from backend.agents.data_agent import DataAgent
    from backend.agents.response_agent import ResponseAgent
    
    # 1. Data Agent: summarize athlete state
    data_agent = DataAgent(db)
    summary = data_agent.summarize()
    
    # 2. Response Agent: generate recommendation via the LLM + RAG
    response_agent = ResponseAgent()
    recommendation = response_agent.generate_recommendation(summary)
    
    # 3. Include the raw summary for transparency
    recommendation["athlete_summary"] = summary
    
    return recommendation


@app.get("/training-context")
def get_training_context(db: Session = Depends(get_db)):
    """Return the raw computed training context for the athlete (phase, cycle, volumes, etc.)"""
    from backend.services.periodization_engine import PeriodizationEngine
    engine = PeriodizationEngine()
    return engine.compute_context(db)


@app.get("/nutrition/today")
def get_nutrition_today(db: Session = Depends(get_db)):
    """Today's menu from the nutrition package (services/nutrition.py). Reads
    the stored week only — never generates one — so a missing or failed plan
    falls back to the calendar instead of failing. A bad package is a 503 for
    this card, not a backend that won't boot."""
    from backend.services import nutrition

    now = get_local_now()
    try:
        return nutrition.today_menu(now.date(), now, plan_flows.stored_week_plan(db, now.date()))
    except nutrition.NutritionPackageError as e:
        raise HTTPException(status_code=503, detail=f"Nutrition package: {e}")


@app.get("/nutrition/days")
def get_nutrition_days(count: int = 3, db: Session = Depends(get_db)):
    """Today plus the next days' menus, for the phone's 11 AM breakfast
    reminders — one non-repeating notification per date, rescheduled on every
    app open (the repeating ones deleted on 2026-08-16 outlived their code and
    kept announcing a bike ride). A day whose week has no stored plan yet is
    the calendar's estimate, labelled in `source`/`reason`."""
    from datetime import timedelta
    from backend.services import nutrition

    now = get_local_now()
    days = [now.date() + timedelta(days=i) for i in range(max(1, min(count, 7)))]
    try:
        return [nutrition.today_menu(d, now, plan_flows.stored_week_plan(db, d)) for d in days]
    except nutrition.NutritionPackageError as e:
        raise HTTPException(status_code=503, detail=f"Nutrition package: {e}")


@app.get("/block-calendar")
def get_block_calendar(db: Session = Depends(get_db)):
    """Return the full training block calendar from start to race."""
    from backend.services.periodization_engine import PeriodizationEngine
    engine = PeriodizationEngine()
    return engine.compute_block_calendar(db)


@app.get("/weekly-plan")
def get_weekly_plan(db: Session = Depends(get_db)):
    """Retrieve the current week's training plan. Generates a new one if not found."""
    return plan_flows.get_or_generate_weekly_plan(db)


@app.post("/weekly-plan/regenerate")
def regenerate_weekly_plan_endpoint(db: Session = Depends(get_db)):
    """Delete current week's plan and generate a fresh one."""
    return plan_flows.regenerate_weekly_plan(db)


@app.post("/weekly-plan/replan-remaining")
def replan_remaining_days_endpoint(db: Session = Depends(get_db)):
    """Mid-week replan: keep the days already lived, regenerate today and on."""
    return plan_flows.replan_remaining_days(db)


@app.post("/weekly-plan/adapt-today")
def adapt_today_workout_endpoint(body: dict = None, db: Session = Depends(get_db)):
    """Adapt today's workout to today's recovery metrics."""
    return plan_flows.adapt_today_workout(db, body)


@app.post("/weekly-plan/use-original-today")
def use_original_today_endpoint(db: Session = Depends(get_db)):
    """Restore today's pre-adaptation workouts."""
    return plan_flows.use_original_today(db)


@app.get("/history")
def get_history(limit: int = 30, before: str = None, db: Session = Depends(get_db)):
    """The History tab's feed: refresh events + plan-change receipts, merged
    newest-first. One request paints the tab; `before` pages older."""
    from backend.services.history_feed import build_history_feed

    return build_history_feed(db, limit=limit, before=before)


@app.get("/watch/status")
def watch_status(db: Session = Depends(get_db)):
    """This week's watch pushes and the newest failure (docs/COROS_MCP.md)."""
    return coros_watch_sync.watch_status(db)


@app.get("/weekly-plan/status")
def get_weekly_plan_status(db: Session = Depends(get_db)):
    """Return the current weekly plan enriched with actual completion/compliance data."""
    from backend.services.compliance import get_weekly_plan_status as compute_status
    
    result = compute_status(db)
    if not result:
        raise HTTPException(status_code=404, detail="No weekly plan found. Fetch /weekly-plan first.")
    return result



@app.post("/pull-to-refresh")
async def pull_to_refresh(db: Session = Depends(get_db)):
    """Full sync: scrape COROS → ingest → generate coaching recommendation."""
    from backend.services.ingestion_service import IngestionService
    
    sync_status = "ok"
    sync_message = ""
    
    # 1. Pull COROS (MCP first, scraper fallback — see refresh_job.pull_coros)
    coros_source = {}
    try:
        data = await refresh_job.pull_coros(db, lambda stage: None, 0, coros_source)

        # 2. Ingest into database
        service = IngestionService()
        service.ingest_coros_data(data)
        missing = data.get("missing") or []
        if missing:
            sync_status = "partial"
            sync_message = f"Synced, but missing: {', '.join(missing)}."
        else:
            sync_message = f"COROS data synced successfully (via {coros_source['source']})."
    except Exception as e:
        sync_status = "partial"
        sync_message = f"Scraper error: {str(e)}. Using cached data."
    
    # 3. Generate fresh coaching recommendation
    from backend.agents.data_agent import DataAgent
    from backend.agents.response_agent import ResponseAgent
    
    data_agent = DataAgent(db)
    summary = data_agent.summarize()
    
    response_agent = ResponseAgent()
    recommendation = response_agent.generate_recommendation(summary)
    recommendation["athlete_summary"] = summary
    
    return {
        "sync_status": sync_status,
        "sync_message": sync_message,
        "coaching": recommendation
    }


@app.post("/smart-refresh")
async def smart_refresh(db: Session = Depends(get_db)):
    """
    Single morning action: scrape → ingest → evaluate recovery → auto-adapt if needed.
    Synchronous form — the caller holds the connection for the whole scrape.
    The iOS deep pull uses /smart-refresh/start + /smart-refresh/status instead.
    """
    return await refresh_job.run_smart_refresh(db)


@app.post("/smart-refresh/start")
async def smart_refresh_start():
    """Kick off a deep refresh; joins the running job if one exists."""
    return refresh_job.start(SessionLocal)


@app.get("/smart-refresh/status")
async def smart_refresh_status():
    return refresh_job.status()


CHAT_HISTORY_MESSAGES = 20   # newest messages sent to the model


def _build_chat_context(db: Session, summary: str, rag_context: str, extras: str = "") -> str:
    from backend.services.periodization_engine import PeriodizationEngine
    from backend.services.plan_normalizer import normalize_plan
    from datetime import timedelta
    import json

    engine = PeriodizationEngine()
    training_context = engine.compute_context(db)
    
    today = get_local_today()
    start_of_week = today - timedelta(days=today.weekday())
    plan_record = week_plan(db, start_of_week)
    plan_json = {}
    if plan_record:
        plan_json = normalize_plan(plan_record.plan_json)
        
    # The engine emits 'phase' — reading 'current_phase' meant chat said
    # "TRAINING PHASE: Unknown" since forever (found 2026-08-31).
    context_str = f"TRAINING PHASE: {training_context.get('phase', 'Unknown')}\n"
    if training_context.get("race_done"):
        context_str += "GOAL RACE: already run — no race on the calendar\n"
    else:
        context_str += f"WEEKS TO RACE: {training_context.get('weeks_to_race', 'N/A')}\n"
    
    today_day_name = get_local_now().strftime("%A")
    if plan_json and "days" in plan_json:
        today_plan = plan_json["days"].get(today_day_name)
        context_str += f"\nTODAY'S WORKOUT ({today_day_name}):\n"
        if today_plan:
            context_str += json.dumps(today_plan, indent=2)
        else:
            context_str += "Rest day / No workout planned."
            
        context_str += "\n\nFULL WEEK SCHEDULE:\n"
        for day, info in plan_json["days"].items():
            context_str += f"- {day}: {info.get('summary', info.get('focus', 'Rest'))}\n"
            
    system_prompt = f"""You are Phoenix, an elite personal coach. You know this athlete's exact schedule, zones, and goals. You talk like a real coach — direct, confident, no fluff.

ATHLETE DATA & ZONES:
{summary}

RECENT DETAIL (COROS, Python-computed):
{extras or "  (none yet)"}

{context_str}

COACHING KNOWLEDGE:
{rag_context}

RULES:
- Reply in the language of the athlete's most recent message — he code-switches between Spanish and English.
- Lead with the answer, then give 1-2 key reasons using the athlete's actual numbers or their scheduled workouts.
- Maximum 4-6 lines total. No headers, no essays, no bullet-point lists longer than 3 items.
- If the athlete asks a yes/no question, start with yes or no.
- Reference specific data points (Pace Zones, HR Zones, Today's Workout, etc.) to justify your advice.
- Sound like a coach in person, not a textbook.
- If they ask what to do today, refer directly to TODAY'S WORKOUT."""
    return system_prompt


@app.get("/chat/sessions")
def get_chat_sessions(db: Session = Depends(get_db)):
    """Return all chat sessions for the athlete."""
    athlete = db.query(Athlete).first()
    if not athlete:
        return []
    sessions = db.query(ChatSession).filter(ChatSession.athlete_id == athlete.id).order_by(ChatSession.updated_at.desc()).all()
    return [
        {
            "id": s.id,
            "title": s.title,
            "updated_at": str(s.updated_at)
        } for s in sessions
    ]

@app.post("/chat/sessions")
def create_chat_session(db: Session = Depends(get_db)):
    """Create a new empty chat session."""
    athlete = db.query(Athlete).first()
    if not athlete:
        raise HTTPException(status_code=404, detail="Athlete not found")
    new_session = ChatSession(athlete_id=athlete.id)
    db.add(new_session)
    db.commit()
    db.refresh(new_session)
    return {"id": new_session.id, "title": new_session.title}

@app.get("/chat/sessions/{session_id}")
def get_chat_session_history(session_id: int, db: Session = Depends(get_db)):
    """Return the full message history for a session."""
    session = db.query(ChatSession).filter(ChatSession.id == session_id).first()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    messages = db.query(ChatMessage).filter(ChatMessage.session_id == session_id).order_by(ChatMessage.created_at.asc()).all()
    return [
        {
            "id": m.id,
            "role": m.role,
            "content": m.content,
            "created_at": str(m.created_at)
        } for m in messages
    ]

@app.delete("/chat/sessions/{session_id}")
def delete_chat_session(session_id: int, db: Session = Depends(get_db)):
    session = db.query(ChatSession).filter(ChatSession.id == session_id).first()
    if session:
        db.delete(session)
        db.commit()
    return {"status": "ok"}


@app.post("/chat")
async def chat_with_coach_stream(body: dict, db: Session = Depends(get_db)):
    """
    Chat with the AI coach — streams tokens via Server-Sent Events (SSE).
    
    The response is a stream of SSE events:
      data: {"token": "Hello"}
      data: {"token": " world"}
      ...
      data: [DONE]
    """
    from backend.agents.data_agent import DataAgent
    
    message = body.get("message", "")
    session_id = body.get("session_id")
    
    if not message:
        raise HTTPException(status_code=400, detail="Message is required")
        
    athlete = db.query(Athlete).first()
    if not athlete:
        raise HTTPException(status_code=404, detail="Athlete not found")
        
    # Get or create session
    chat_session = None
    if session_id:
        chat_session = db.query(ChatSession).filter(ChatSession.id == session_id).first()
    if not chat_session:
        # Create a new session and auto-generate a title based on the first message
        title = message[:30] + "..." if len(message) > 30 else message
        chat_session = ChatSession(athlete_id=athlete.id, title=title)
        db.add(chat_session)
        db.commit()
        db.refresh(chat_session)
        session_id = chat_session.id
    else:
        # Update timestamp
        from datetime import datetime
        chat_session.updated_at = datetime.utcnow()
        db.commit()
        
    # Save user message
    user_msg = ChatMessage(session_id=session_id, role="user", content=message)
    db.add(user_msg)
    db.commit()
    
    # Build athlete context
    data_agent = DataAgent(db)
    summary = data_agent.summarize()
    
    # Get relevant knowledge from singleton KB
    kb = get_kb()
    rag_chunks = kb.query(message, n_results=3)
    rag_context = "\n\n".join(rag_chunks) if rag_chunks else ""
    
    system_prompt = _build_chat_context(db, summary, rag_context, data_agent.chat_extras())

    messages = [{"role": "system", "content": system_prompt}]

    # Fetch history
    # Bounded history: Groq admits a request at prompt + completion cap against
    # 8000 TPM, so an unbounded session would eventually 429 the stream.
    history = db.query(ChatMessage).filter(ChatMessage.session_id == session_id)\
        .order_by(ChatMessage.created_at.desc(), ChatMessage.id.desc()).limit(CHAT_HISTORY_MESSAGES).all()[::-1]
    for h in history:
        messages.append({"role": h.role, "content": h.content})

    # Issue triage: does this message report soreness or an injury worth a plan
    # change? The keyword gate is pure Python so ordinary chat costs nothing
    # extra; only a likely hit pays for the extraction call. The proposal rides
    # out on the same stream — no second round trip on a cold-start-prone tier.
    proposal = None
    try:
        from backend.services.issue_triage import build_proposal, extract_issue, looks_like_issue
        if looks_like_issue(message):
            issue = extract_issue(message)
            if issue:
                proposal = build_proposal(db, issue)
    except Exception as e:
        # Triage is an enhancement. Chat must still work if it breaks.
        print(f"⚠️ Issue triage failed (continuing with normal chat): {e}")

    # Recovery triage — the same door swinging out. Only when issue triage
    # found nothing ("calf hurts again" must win over "calf is fine"), only
    # when the message sounds like good news, and only when something is
    # actually active to resolve — then it's worth one extraction call.
    recovery_proposal = None
    if not proposal:
        try:
            from backend.services.issue_triage import (
                build_recovery_proposal, extract_recovery,
                get_open_injuries, looks_like_recovery,
            )
            athlete = db.query(Athlete).first()
            if athlete and looks_like_recovery(message):
                # Open = Active + Recovering. An auto-expired window parks the
                # injury in Recovering, which is exactly the state waiting for
                # the athlete to say "it's fine now".
                open_injuries = get_open_injuries(db, athlete.id)
                if open_injuries:
                    matched = extract_recovery(message, open_injuries)
                    if matched:
                        recovery_proposal = build_recovery_proposal(db, matched)
        except Exception as e:
            print(f"⚠️ Recovery triage failed (continuing with normal chat): {e}")

    # Travel triage — third in line, and last on purpose: "hurt my knee on a
    # trip" is an issue, "calf's fine, flying out tomorrow" is a recovery, and
    # only a message that is neither gets read as pure logistics.
    travel_proposal = None
    if not proposal and not recovery_proposal:
        try:
            from backend.services.issue_triage import (
                build_travel_proposal, extract_travel, looks_like_travel,
            )
            if looks_like_travel(message):
                travel_days = extract_travel(message)
                if travel_days:
                    travel_proposal = build_travel_proposal(db, travel_days)
        except Exception as e:
            print(f"⚠️ Travel triage failed (continuing with normal chat): {e}")

    if travel_proposal:
        messages.append({
            "role": "system",
            "content": (
                "The athlete just said they will be traveling on "
                f"{', '.join(travel_proposal['days'])}. A confirmation card is "
                "already being shown to them offering to rest those days and "
                "rebuild the open days around the trip (run volume protected), "
                "so do NOT propose specific rearrangements yourself. Reply in "
                "one or two sentences: acknowledge the trip and tell them to "
                "confirm below."
            ),
        })

    if recovery_proposal:
        rebuild = recovery_proposal.get("rebuild_days") or []
        messages.append({
            "role": "system",
            "content": (
                "The athlete just said their "
                f"{recovery_proposal['injury']['body_part']} has recovered. "
                "A confirmation card is already being shown to them"
                + (f" offering to rebuild {', '.join(rebuild)}" if rebuild else "")
                + ", so do NOT propose specific sessions yourself. Reply in one "
                "or two sentences: celebrate briefly, remind them to ease back "
                "in, and tell them to confirm below."
            ),
        })

    if proposal:
        # Keep the prose and the card from contradicting each other. The card
        # owns the specifics; the reply is the human part around it.
        affected = ", ".join(d["day"] for d in proposal["affected_days"])
        messages.append({
            "role": "system",
            "content": (
                "The athlete just reported a physical issue "
                f"({proposal['issue']['body_part']}, severity "
                f"{proposal['issue']['severity']}/10). A confirmation card is already "
                f"being shown to them listing the affected days ({affected}) with "
                "swap-or-rest options for each, so do NOT list those days or propose "
                "specific replacement sessions yourself. Reply in two or three "
                "sentences: acknowledge it, say briefly what you'd do about it and "
                "why, and tell them to confirm the changes below."
            ),
        })

    async def event_stream():
        """Generator that yields SSE events with streamed tokens."""
        full_response = ""
        try:
            # First frame: which session this exchange was filed under. A
            # client that opened with no session_id can only continue the
            # conversation if this comes back — without it every message
            # minted its own one-message session. Unknown keys are ignored
            # by clients that don't parse them.
            yield f"data: {json.dumps({'session_id': session_id})}\n\n"
            from backend.core.llm_client import chat_completion_stream
            async for token in chat_completion_stream(messages):
                full_response += token
                yield f"data: {json.dumps({'token': token})}\n\n"
            # Emitted before [DONE] so the client can attach the confirmation
            # card to this message. Clients that don't know the key ignore it.
            if proposal:
                yield f"data: {json.dumps({'proposal': proposal})}\n\n"
            if recovery_proposal:
                yield f"data: {json.dumps({'recovery': recovery_proposal})}\n\n"
            if travel_proposal:
                yield f"data: {json.dumps({'travel': travel_proposal})}\n\n"
            yield "data: [DONE]\n\n"

            # Save assistant response
            db_save = SessionLocal()
            try:
                ai_msg = ChatMessage(session_id=session_id, role="assistant", content=full_response)
                db_save.add(ai_msg)
                db_save.commit()
            finally:
                db_save.close()
        except Exception as e:
            print(f"LLM STREAMING ERROR: {e}")
            fallback = f"Coach is temporarily unavailable. Error: {str(e)[:100]}"
            yield f"data: {json.dumps({'token': fallback})}\n\n"
            yield "data: [DONE]\n\n"
    
    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # Disable nginx buffering if ever behind proxy
        }
    )


@app.post("/chat-sync")
async def chat_with_coach_sync(body: dict, db: Session = Depends(get_db)):
    """Non-streaming fallback chat endpoint. Returns the full response at once."""
    from backend.agents.data_agent import DataAgent
    
    message = body.get("message", "")
    session_id = body.get("session_id")
    
    if not message:
        raise HTTPException(status_code=400, detail="Message is required")
        
    athlete = db.query(Athlete).first()
    if not athlete:
        raise HTTPException(status_code=404, detail="Athlete not found")
        
    # Get or create session
    chat_session = None
    if session_id:
        chat_session = db.query(ChatSession).filter(ChatSession.id == session_id).first()
    if not chat_session:
        title = message[:30] + "..." if len(message) > 30 else message
        chat_session = ChatSession(athlete_id=athlete.id, title=title)
        db.add(chat_session)
        db.commit()
        db.refresh(chat_session)
        session_id = chat_session.id
    else:
        from datetime import datetime
        chat_session.updated_at = datetime.utcnow()
        db.commit()
        
    # Save user message
    user_msg = ChatMessage(session_id=session_id, role="user", content=message)
    db.add(user_msg)
    db.commit()
    
    # Build athlete context
    data_agent = DataAgent(db)
    summary = data_agent.summarize()
    
    # Get relevant knowledge from singleton KB
    kb = get_kb()
    rag_chunks = kb.query(message, n_results=3)
    rag_context = "\n\n".join(rag_chunks) if rag_chunks else ""
    
    system_prompt = _build_chat_context(db, summary, rag_context, data_agent.chat_extras())

    messages = [{"role": "system", "content": system_prompt}]
    
    # Fetch history
    # Bounded history: Groq admits a request at prompt + completion cap against
    # 8000 TPM, so an unbounded session would eventually 429 the stream.
    history = db.query(ChatMessage).filter(ChatMessage.session_id == session_id)\
        .order_by(ChatMessage.created_at.desc(), ChatMessage.id.desc()).limit(CHAT_HISTORY_MESSAGES).all()[::-1]
    for h in history:
        messages.append({"role": h.role, "content": h.content})

    try:
        from backend.core.llm_client import chat_completion
        content = chat_completion(messages=messages)
        
        # Save assistant message
        ai_msg = ChatMessage(session_id=session_id, role="assistant", content=content)
        db.add(ai_msg)
        db.commit()
        
        return {"response": content, "session_id": session_id}
    except Exception as e:
        print(f"LLM ERROR in /chat-sync: {e}")
        return {"response": f"I'm currently in offline mode. Error: {str(e)[:100]}",
                "session_id": session_id}


@app.post("/feedback")
def submit_feedback(body: dict, db: Session = Depends(get_db)):
    """Save post-workout feedback from the iOS app to the database."""
    from backend.models.database import AthleteFeedback, Activity
    from datetime import datetime
    
    # 1. Create feedback record
    new_feedback = AthleteFeedback(
        date=datetime.now(),
        athlete_id=1,  # Default for MVP
        rpe=body.get("rpe"),
        motivation=body.get("motivation"),
        soreness=body.get("soreness"),
        general_notes=body.get("notes", ""),
        strength_exercises=body.get("strength_exercises") # Will be used soon
    )
    
    # 2. Try to link to the most recent activity (within last 4 hours)
    recent_activity = db.query(Activity).order_by(Activity.start_time.desc()).first()
    if recent_activity:
        time_diff = datetime.now() - recent_activity.start_time
        if time_diff.total_seconds() < 14400: # 4 hours
            new_feedback.activity_id = recent_activity.id
            print(f"Linking feedback to activity: {recent_activity.sport}")

    db.add(new_feedback)
    db.commit()
    
    print(f"Feedback saved to DB: RPE={new_feedback.rpe}, Notes={len(new_feedback.general_notes)} chars")
    return {"status": "ok", "message": "Feedback recorded in database"}

@app.get("/activity/{activity_id}/analysis")
def get_activity_analysis(activity_id: str, db: Session = Depends(get_db)):
    """Analyze a specific activity and return AI coach feedback."""
    from datetime import timedelta
    from backend.models.database import Activity, AthleteFeedback
    from backend.agents.response_agent import ResponseAgent
    from backend.services.periodization_engine import PeriodizationEngine
    from backend.services.compliance import _compute_workout_compliance, _normalize_sport
    
    activity = db.query(Activity).filter(Activity.id == activity_id).first()
    if not activity:
        raise HTTPException(status_code=404, detail="Activity not found")
    
    # Get associated feedback if any
    feedback = db.query(AthleteFeedback).filter(AthleteFeedback.activity_id == activity_id).first()
    
    # Look up planned workout from WeeklyPlan
    planned_workout = None
    compliance = None
    if activity.start_time:
        activity_date = activity.start_time.date()
        start_of_week = activity_date - timedelta(days=activity_date.weekday())
        plan_record = week_plan(db, start_of_week)
        
        if plan_record and plan_record.plan_json:
            day_name = activity.start_time.strftime("%A")
            from backend.services.plan_normalizer import normalize_plan
            normalized_plan = normalize_plan(plan_record.plan_json)
            day_plan = normalized_plan.get("days", {}).get(day_name, {})
            workouts = day_plan.get("workouts", [])
            act_sport = _normalize_sport(activity.sport or "")
            for w in workouts:
                if _normalize_sport(w.get("sport", "")) == act_sport:
                    planned_workout = w
                    break
                    
        if planned_workout:
            try:
                compliance = _compute_workout_compliance(planned_workout, {
                    "duration_sec": activity.duration_sec,
                    "avg_hr": activity.avg_hr,
                    "distance_m": activity.distance_m
                })
            except Exception as e:
                print(f"⚠️ Error computing compliance for analysis: {e}")
    
    # Compute TrainingContext using PeriodizationEngine
    engine = PeriodizationEngine()
    training_context = engine.compute_context(db)
    
    # Prepare data for AI
    activity_data = {
        "sport": activity.sport,
        "duration_min": (activity.duration_sec or 0) / 60,
        "distance_km": (activity.distance_m or 0) / 1000,
        "avg_hr": activity.avg_hr,
        "max_hr": activity.max_hr,
        "training_load": activity.training_load,
        "user_notes": feedback.general_notes if feedback else "None"
    }
    
    # Personal-model numbers ride both ways: into the prompt so the coach can
    # cite them, and out to iOS, which used to render hardcoded placeholders
    # ("Zone 2 Aerobic", "Fully Recovered") in their place.
    from backend.services.personal_model import activity_insight
    athlete = db.query(Athlete).first()
    insight = activity_insight(db, athlete, activity)
    if insight["hr_residual_bpm"] is not None:
        activity_data["hr_vs_personal_baseline_bpm"] = insight["hr_residual_bpm"]
        activity_data["hr_baseline_verdict"] = insight["hr_residual_label"]
    if insight["intensity"]:
        activity_data["intensity_zone"] = insight["intensity"]

    agent = ResponseAgent()
    analysis = agent.analyze_activity(
        activity_data,
        planned_workout=planned_workout,
        compliance=compliance,
        training_context=training_context
    )
    analysis.update(insight)
    return analysis

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001)


