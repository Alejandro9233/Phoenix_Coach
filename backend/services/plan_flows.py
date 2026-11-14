"""
The plan flows: everything that builds or rewrites this week's plan.

    get_or_generate_weekly_plan   GET /weekly-plan (first generation, locked)
    regenerate_weekly_plan        Monday: full rebuild. Mid-week: replan.
    replan_remaining_days         keep the days lived, rebuild today and on
    adapt_today_workout           today follows the recovery numbers
    use_original_today            the athlete overrides the adaptation
    reenforce_current_week        availability changed in Profile

Lived in main.py until 2026-11-14, tangled with the endpoint definitions.
main.py's endpoints are now thin wrappers around these. The functions still
raise HTTPException: the status codes are part of what the iOS app reads, and
re-mapping them was not worth the risk a month before the race.

Every write here goes through plan_meta (run_plan_write_pipeline, then
commit_plan_write). Callers in other modules reach these as
`plan_flows.<name>` — attribute access, so a test that patches the name here
patches it for every caller.
"""
import threading
import time

from fastapi import HTTPException

from backend.models.database import Activity, Athlete, RecoverySnapshot, WeeklyPlan, week_plan
from backend.services import coros_watch_sync
from backend.services.constraint_enforcer import sport_days
from backend.utils.timezone import get_local_now, get_local_today


def reenforce_current_week(db, athlete):
    """Re-enforce the stored week after availability changed in Profile.

    Deliberately pure Python: ProfileView autosaves on every toggle, so an
    LLM call here would fire per keystroke, cost money, and could 502
    mid-edit. The enforcer is idempotent and fails toward rest-with-reason;
    the athlete can hit replan-remaining afterward for a proper refit.

    Scoped to today-forward — past days are history and are never
    revalidated (same invariant as replan-remaining). Persists only when a
    session was actually stripped, so repeated autosaves don't churn the row.
    """
    from datetime import timedelta

    from backend.services.constraint_enforcer import (
        get_active_injuries,
        get_travel_day_names,
    )
    from backend.services.plan_meta import (
        capture_before, commit_plan_write, run_plan_write_pipeline,
    )
    from backend.services.plan_normalizer import VALID_DAYS, normalize_plan

    today = get_local_today()
    start_of_week = today - timedelta(days=today.weekday())
    plan_record = week_plan(db, start_of_week)
    if not plan_record:
        return None

    days = VALID_DAYS[today.weekday():]
    plan_json = normalize_plan(plan_record.plan_json)
    before = capture_before(plan_json, days=days)

    availability = {
        **sport_days(athlete),
        "travel_day_names": get_travel_day_names(db, athlete.id, start_of_week),
    }
    reason = "Availability changed in Profile"
    plan_json, violations = run_plan_write_pipeline(
        db, plan_json,
        source="profile_reenforce",
        availability=availability,
        active_injuries=get_active_injuries(db, athlete.id),
        days=days,
        reason=reason,
        before=before,
    )
    if violations:
        commit_plan_write(db, plan_record, plan_json, days=days,
                          source="profile_reenforce",
                          availability=availability, reason=reason)
    return {"stripped": len(violations), "details": violations}


def stored_week_plan(db, day):
    """The stored plan_json for the week holding `day`, or None. Read-only:
    nutrition never triggers a generation."""
    from datetime import timedelta

    week_start = day - timedelta(days=day.weekday())
    record = week_plan(db, week_start)
    return record.plan_json if record else None


# Serializes weekly-plan generation. Endpoints are sync (FastAPI runs them
# in a threadpool) and Render runs one process, so a module lock is the
# whole story.
_PLAN_GENERATION_LOCK = threading.Lock()


def watch_sync_this_week(db):
    """watch_sync_safe for writes that edit this week's stored plan in
    place (the triage applies): reload the row, push from today forward.
    Before this the watch caught up only at the next refresh's backstop, so
    a run cancelled for an injury stayed on the watch until then."""
    from datetime import timedelta
    today = get_local_today()
    week_start = today - timedelta(days=today.weekday())
    row = week_plan(db, week_start)
    if row:
        return watch_sync_safe(db, row.plan_json, week_start)
    return None


def watch_sync_safe(db, plan_json, week_start, days=None):
    """Push/update this week's courses on the COROS watch after a plan commit
    (docs/COROS_MCP.md, "Watch push"). Off unless COROS_WATCH_PUSH=1 and the
    MCP token exists. Never raises: the watch is downstream of the plan, and a
    failed push is a row in watch_workouts, not a failed request."""
    try:
        report = coros_watch_sync.sync_watch(db, plan_json, week_start, days=days)
        if report.get("status") not in ("off", "nothing"):
            print(f"⌚ watch sync {report.get('status')}: {len(report.get('writes') or [])} writes, "
                  f"{len(report.get('skipped') or [])} skipped" + (f" — {report['error']}" if report.get("error") else ""))
        return report
    except Exception as e:
        print(f"⚠️ watch sync failed: {e}")
        return {"status": "error", "error": str(e)[:200], "writes": [], "skipped": []}


def get_or_generate_weekly_plan(db, carry_revisions=None, source="generate",
                                 reason="Weekly plan generated"):
    """The GET /weekly-plan body, callable with regenerate's extras.

    A plain function so regenerate can pass carry_revisions/source without
    FastAPI reading them as query params. `carry_revisions` seeds the raw
    plan's _revisions before the pipeline, so a regenerated week keeps its
    receipt history (finalize appends the new receipt on top and trims)."""
    from datetime import timedelta
    from backend.services.plan_normalizer import normalize_plan

    today = get_local_today()
    # Monday of the current week
    start_of_week = today - timedelta(days=today.weekday())

    # Fast path: the week already has a plan.
    plan_record = week_plan(db, start_of_week)
    if plan_record:
        return normalize_plan(plan_record.plan_json)

    # Freshness: when a smart-refresh is mid-flight, wait (bounded) for its
    # ingest before generating the week. 2026-08-31's plan generated from
    # Sunday-night data while Monday's scrape was still running, and nothing
    # ever reconciles a persisted week. Sync endpoint -> threadpool thread,
    # so sleeping here blocks nobody else. 90s + generation stays inside the
    # phone's 180s request timeout.
    # Regenerate must NOT wait: it arrives holding an uncommitted DELETE on
    # this week's plan row; sleeping here while the refresh job's adapt path
    # blocks on that row lock would freeze the whole event loop (review
    # 2026-08-31). Its delete/re-create window is seconds — acceptable.
    # Imported here: refresh_job imports this module (it adapts today).
    from backend.services import refresh_job
    if source == "generate" and refresh_job.job["state"] == "running":
        print("⏳ Weekly-plan generation waiting for the in-flight refresh...")
        waited = 0.0
        while refresh_job.job["state"] == "running" and waited < 90.0:
            time.sleep(2.0)
            waited += 2.0
        db.expire_all()  # drop this session's cache so the fresh rows are seen

    # One generation at a time. On Monday morning the app fires several
    # requests at once; without the lock each saw "no plan", spent ~7k Groq
    # tokens generating, and inserted its own row — a shadow plan beneath
    # the served one, plus a self-inflicted TPM 429 (both seen 2026-08-31).
    # Waiters block on their threadpool thread, then find the fresh row in
    # the re-check. (regenerate arrives here with its delete pending in the
    # same session, so its re-check still sees "no plan" — by design.)
    with _PLAN_GENERATION_LOCK:
        plan_record = week_plan(db, start_of_week)
        if plan_record:
            return normalize_plan(plan_record.plan_json)
        new_plan = _generate_weekly_plan(db, start_of_week, carry_revisions, source, reason)
    # The week goes to the watch once, here: after the generation commit and
    # outside the lock (a rolled-back plan must never leave courses behind).
    watch_sync_safe(db, new_plan, start_of_week)
    return new_plan


def _generate_weekly_plan(db, start_of_week, carry_revisions, source, reason):
    """The generation body. Call only while holding _PLAN_GENERATION_LOCK —
    it trusts that no plan row exists for start_of_week and inserts one."""
    from datetime import date, timedelta, datetime
    from backend.models.database import WeeklyPlan, Athlete
    from backend.agents.data_agent import DataAgent
    from backend.agents.response_agent import ResponseAgent
    from backend.services.periodization_engine import PeriodizationEngine

    # No race, no plan. Without race_date + race_distance the periodization
    # engine invents weeks_to_race=99 and defaults to "Marathon", so an
    # auto-created "New Athlete" got a full default-profile week on first app
    # open. Stored plans (above) still serve; only generation is gated.
    athlete = db.query(Athlete).first()
    if athlete is None:
        missing = ["athlete"]
    else:
        missing = [f for f in ("race_date", "race_distance") if not getattr(athlete, f)]
    if missing:
        raise HTTPException(
            status_code=409,
            detail={
                "reason": "profile_incomplete",
                "missing": missing,
                "message": "Set your race in Profile before a plan can be generated.",
            },
        )

    # If not, generate a new one
    data_agent = DataAgent(db)
    summary = data_agent.summarize()

    profile = {
        "race_name": athlete.race_name,
        "race_distance": athlete.race_distance,
        "race_date": str(athlete.race_date) if athlete.race_date else None,
        **sport_days(athlete),
    } if athlete else {}
    
    # Compute Training Context using PeriodizationEngine
    engine = PeriodizationEngine()
    training_context = engine.compute_context(db)
    
    # Check if is_recovery_week changed from last week → log it
    try:
        last_monday = start_of_week - timedelta(days=7)
        last_plan = week_plan(db, last_monday)
        if last_plan and last_plan.plan_json and "_context" in last_plan.plan_json:
            was_recovery_week = last_plan.plan_json["_context"].get("is_recovery_week", False)
            is_recovery_week = training_context.get("is_recovery_week", False)
            if was_recovery_week != is_recovery_week:
                print(f"🔄 Recovery week status changed! Was recovery week: {was_recovery_week}, Is recovery week: {is_recovery_week}")
    except Exception as e:
        print(f"⚠️ Error checking previous week's recovery status: {e}")
        
    # Generation runs INSIDE the pipeline so the volume gate can retry it
    # once with numeric feedback. A failed generation still fails the whole
    # request — a fabricated plan persisted here becomes the athlete's actual
    # week (2026-08-17). Same rule as /weekly-plan/replan-remaining.
    response_agent = ResponseAgent()

    def _generate(feedback=None):
        raw = response_agent.generate_weekly_plan(
            summary, profile, training_context=training_context,
            feedback=feedback,
        )
        if carry_revisions:
            raw["_revisions"] = list(carry_revisions)
        return raw

    from backend.services.constraint_enforcer import get_active_injuries
    from backend.services.hand_set_weeks import hand_set_week
    from backend.services.plan_meta import run_plan_write_pipeline
    # A hand-set week (hand_set_weeks.py) replaces the LLM and skips the
    # volume gate, but still runs the ungated pipeline: enforcer and stamps.
    hand_set = hand_set_week(start_of_week)
    if hand_set and carry_revisions:
        hand_set["_revisions"] = list(carry_revisions)
    try:
        if hand_set:
            new_plan_json, violations = run_plan_write_pipeline(
                db, hand_set,
                source=source,
                availability=training_context.get("availability", {}),
                active_injuries=get_active_injuries(db, athlete.id) if athlete else [],
                reason=reason,
            )
        else:
            new_plan_json, violations = run_plan_write_pipeline(
                db,
                generate=_generate,
                gate_ctx=training_context,
                source=source,
                availability=training_context.get("availability", {}),
                active_injuries=get_active_injuries(db, athlete.id) if athlete else [],
                reason=reason,
                fill_run_km=True,
            )
    except Exception as e:
        raise HTTPException(
            status_code=502,
            detail=f"Plan generation failed; no plan persisted: {e}"
        )

    # Weekly review looks back at LAST week — independent of the new plan,
    # so it attaches after the pipeline (one LLM call, never per attempt).
    if training_context.get("last_week") and training_context["last_week"].get("sessions_completed", 0) > 0:
        try:
            weekly_review = response_agent.generate_weekly_review(
                compliance_data=training_context["last_week"],
                training_context=training_context
            )
            new_plan_json["weekly_review"] = weekly_review
            print("📝 Weekly review generated successfully for the new plan.")
        except Exception as e:
            print(f"⚠️ Error generating weekly review: {e}")

    # Save to database
    new_record = WeeklyPlan(
        week_start=start_of_week,
        athlete_id=athlete.id if athlete else 1,
        plan_json=new_plan_json,
        created_at=datetime.now()
    )
    db.add(new_record)
    db.commit()
    
    return new_plan_json


def regenerate_weekly_plan(db):
    """Delete current week's plan and generate a fresh one."""
    from datetime import date, timedelta
    from backend.models.database import WeeklyPlan
    
    today = get_local_today()
    start_of_week = today - timedelta(days=today.weekday())

    # Receipt history must survive a regenerate — grab it before the delete.
    old_record = week_plan(db, start_of_week)
    carry = list((old_record.plan_json or {}).get("_revisions") or []) if old_record else None

    # Mid-week, the days already lived stay as they were — the iOS alert
    # promises "past completed activities are preserved". The full-week
    # path replaced them with workouts that never happened and budgeted the
    # whole week's km as if none of it had been run. replan-remaining locks
    # them and credits what the watch recorded. Same response shape as here.
    if old_record and today.weekday() > 0:
        return replan_remaining_days(db)["plan"]

    # Delete without committing: if generation fails, the rollback below
    # brings the old plan back. Committing the delete first meant a failed
    # regenerate left the athlete with no week at all.
    db.query(WeeklyPlan).filter(
        WeeklyPlan.week_start == start_of_week
    ).delete()

    # Reuse the GET /weekly-plan logic; it commits the delete + new plan together.
    try:
        return get_or_generate_weekly_plan(
            db, carry_revisions=carry, source="regenerate",
            reason="Plan regenerated from scratch",
        )
    except Exception:
        db.rollback()
        raise


def replan_remaining_days(db):
    """
    Smart mid-week replan: lock completed days and only regenerate remaining days.
    
    Gathers actual activities from completed days, sends them as context to the AI coach,
    and generates fresh plans only for today and future days.
    """
    from datetime import date, timedelta, datetime
    from backend.models.database import Activity, Athlete
    from backend.agents.data_agent import DataAgent
    from backend.agents.response_agent import ResponseAgent
    from backend.services.periodization_engine import PeriodizationEngine
    from backend.services.plan_normalizer import normalize_plan
    
    today = get_local_today()
    start_of_week = today - timedelta(days=today.weekday())
    end_of_week = start_of_week + timedelta(days=6)
    
    day_names_ordered = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    today_name = today.strftime("%A")
    today_idx = day_names_ordered.index(today_name) if today_name in day_names_ordered else 0
    
    # Get the current plan
    plan_record = week_plan(db, start_of_week)
    
    if not plan_record:
        raise HTTPException(status_code=404, detail="No weekly plan found. Fetch /weekly-plan first.")
    
    plan_json = normalize_plan(plan_record.plan_json)
    days_dict = plan_json.get("days", {})

    # Determine which days to lock and which to replan
    # Lock everything before today; replan today + future
    locked_days = day_names_ordered[:today_idx]
    days_to_replan = day_names_ordered[today_idx:]

    # Receipt snapshot — taken now, before the merge below rewrites the days.
    from backend.services.plan_meta import capture_before
    before = capture_before(plan_json, days=days_to_replan)
    
    # Get all activities this week for the completed days summary
    week_activities = db.query(Activity).filter(
        Activity.start_time >= datetime.combine(start_of_week, datetime.min.time()),
        Activity.start_time <= datetime.combine(end_of_week, datetime.max.time()),
    ).order_by(Activity.start_time).all()
    
    # Build the "what's been done" summary for the AI
    summary_lines = []
    total_minutes = 0
    total_load = 0
    
    for day_name in locked_days:
        day_date = start_of_week + timedelta(days=day_names_ordered.index(day_name))
        day_acts = [a for a in week_activities if a.start_time and a.start_time.date() == day_date]
        
        if day_acts:
            parts = []
            for a in day_acts:
                dur = round((a.duration_sec or 0) / 60)
                dist = round((a.distance_m or 0) / 1000, 2) if a.distance_m else None
                load = a.training_load or 0
                total_minutes += dur
                total_load += load
                dist_str = f", {dist}km" if dist else ""
                parts.append(f"{a.sport} {dur}min{dist_str} (load {load})")
            summary_lines.append(f"{day_name}: {' + '.join(parts)}")
        else:
            planned = days_dict.get(day_name, {})
            ws = planned.get("workouts", [])
            if ws and ws[0].get("sport", "").lower() == "rest":
                summary_lines.append(f"{day_name}: Rest day (as planned)")
            else:
                summary_lines.append(f"{day_name}: No activity recorded")
    
    # Also include today's activities if any
    today_acts = [a for a in week_activities if a.start_time and a.start_time.date() == today]
    if today_acts:
        parts = []
        for a in today_acts:
            dur = round((a.duration_sec or 0) / 60)
            dist = round((a.distance_m or 0) / 1000, 2) if a.distance_m else None
            load = a.training_load or 0
            total_minutes += dur
            total_load += load
            dist_str = f", {dist}km" if dist else ""
            parts.append(f"{a.sport} {dur}min{dist_str} (load {load})")
        summary_lines.append(f"{today_name} (today, partially completed): {' + '.join(parts)}")
    
    total_hours = round(total_minutes / 60, 1)
    summary_lines.append(f"\nTotal so far: ~{total_hours} hours, total load {total_load}")
    
    completed_days_summary = "\n".join(summary_lines)
    print(f"📋 Replanning remaining days: {days_to_replan}")
    print(f"📊 Completed summary:\n{completed_days_summary}")
    
    # Get athlete profile
    athlete = db.query(Athlete).first()
    profile = {
        "race_name": athlete.race_name,
        "race_distance": athlete.race_distance,
        "race_date": str(athlete.race_date) if athlete.race_date else None,
        **sport_days(athlete),
    } if athlete else {}
    
    # Get training context and athlete summary
    engine = PeriodizationEngine()
    training_context = engine.compute_context(db)
    
    data_agent = DataAgent(db)
    athlete_summary = data_agent.summarize()
    
    # Generate only the remaining days, INSIDE the pipeline so the volume
    # gate can retry with numeric feedback. Each attempt merges into a fresh
    # copy of the week so a rejected attempt's days can't leak into the next.
    # A failed generation fails the request — the existing plan stays.
    import copy as _copy
    from backend.services.volume_gate import completed_week_actuals
    completed_run_km, completed_hours = completed_week_actuals(db, start_of_week, today)

    response_agent = ResponseAgent()

    # Hand-set week: the remaining days come from hand_set_weeks.py, not the
    # LLM (same as regenerate), through the ungated pipeline.
    from backend.services.hand_set_guard import overlay_hand_set_days
    hand_set_plan = overlay_hand_set_days(plan_json, start_of_week, days_to_replan)

    def _generate(feedback=None):
        result = response_agent.generate_remaining_days(
            athlete_summary=athlete_summary,
            profile=profile,
            training_context=training_context,
            completed_days_summary=completed_days_summary,
            days_to_plan=days_to_replan,
            reason="The athlete requested a mid-week replan.",
            feedback=feedback,
        )
        merged = _copy.deepcopy(plan_json)
        new_days = result.get("days", {})
        for day_name in days_to_replan:
            if day_name in new_days:
                # Clear any stale adaptation data from the replanned days
                new_day = new_days[day_name]
                new_day.pop("adaptation", None)
                new_day.pop("original_workouts", None)
                merged["days"][day_name] = new_day
        # The summary's prose describes the week; the days are most of it.
        # Left alone, it kept describing the week the first generation
        # planned (2026-09-29: "running blocked, bike carries load" over
        # four easy runs). The sums are restamped by finalize regardless.
        new_summary = result.get("week_summary") or {}
        for key in ("focus", "rationale"):
            text = new_summary.get(key)
            if isinstance(text, str) and text.strip():
                merged.setdefault("week_summary", {})[key] = text.strip()
        return merged

    # Single persist pipeline, scoped to the replanned days only — enforcing
    # the locked days would rewrite history the athlete already trained.
    #
    # The gate stage is the fix for: change strength_days in Profile, hit
    # replan, and the strength session stays put. The prompt carried the new
    # availability, the LLM ignored it, and nothing checked.
    from backend.services.constraint_enforcer import get_active_injuries
    from backend.services.plan_meta import run_plan_write_pipeline
    try:
        if hand_set_plan is not None:
            plan_json, violations = run_plan_write_pipeline(
                db, hand_set_plan,
                source="replan_remaining",
                availability=training_context.get("availability", {}),
                active_injuries=get_active_injuries(db, athlete.id) if athlete else [],
                days=days_to_replan,
                reason="The athlete requested a mid-week replan.",
                before=before,
            )
        else:
            plan_json, violations = run_plan_write_pipeline(
                db,
                generate=_generate,
                gate_ctx=training_context,
                completed_run_km=completed_run_km,
                completed_hours=completed_hours,
                source="replan_remaining",
                availability=training_context.get("availability", {}),
                active_injuries=get_active_injuries(db, athlete.id) if athlete else [],
                days=days_to_replan,
                reason="The athlete requested a mid-week replan.",
                before=before,
                fill_run_km=True,
            )
    except Exception as e:
        raise HTTPException(
            status_code=502,
            detail=f"Plan generation failed; existing plan left untouched: {e}"
        )

    from backend.services.plan_meta import commit_plan_write
    plan_json, rebase_violations = commit_plan_write(
        db, plan_record, plan_json,
        days=days_to_replan,
        source="replan_remaining",
        availability=training_context.get("availability", {}),
        reason="The athlete requested a mid-week replan.",
        gate_ctx=training_context,
        carry_summary=True,
        columns={"last_adapted": None},  # Reset adaptation flag since we replanned
    )
    violations = list(violations) + rebase_violations

    print(f"✅ Replanned {len(days_to_replan)} days: {days_to_replan}")
    watch_sync_safe(db, plan_json, start_of_week, days=list(days_to_replan))

    return {
        "status": "replanned",
        "locked_days": locked_days,
        "replanned_days": days_to_replan,
        "completed_summary": completed_days_summary,
        "violations": violations,
        "plan": plan_json
    }


# Same-day re-adaptation ("second opinion") thresholds: how far a recovery
# number must move, after today's earlier adaptation already consumed it,
# before a repeat LLM call is justified. Coarser on purpose than the
# smart-refresh triggers — those gate the FIRST adaptation of the day.
ADAPT_SUPERSEDE_THRESHOLDS = {
    "hrv_ms": ("HRV", 5.0),                  # ms
    "resting_hr": ("RHR", 3.0),              # bpm
    "fatigue_state": ("fatigue zone", 0.0),  # any zone change
    "load_ratio": ("load ratio", 0.15),
    "tib": ("TIB", 5.0),
}
MAX_ADAPTS_PER_DAY = 2  # the first adaptation + one second opinion


def _recovery_inputs(db):
    """The scalar recovery picture an adaptation is based on — recorded on
    its receipt so a later same-day call can see whether the data moved."""
    from backend.models.database import RecoverySnapshot
    snap = db.query(RecoverySnapshot).order_by(RecoverySnapshot.date.desc()).first()
    if not snap:
        return None
    return {
        "date": snap.date.isoformat() if hasattr(snap.date, "isoformat") else str(snap.date),
        "hrv_ms": snap.hrv_ms,
        "resting_hr": snap.resting_hr,
        "fatigue_state": snap.fatigue_state,
        "load_ratio": snap.load_ratio,
        "tib": snap.tib,
    }


def _recovery_changed_since_adapt(db, plan_json, today):
    """What materially moved since today's earlier adaptation, as a short
    human-readable string — or None, meaning the idempotency guard holds.

    Conservative by construction: no adapt receipt today, a legacy receipt
    without recorded inputs, or the daily cap already reached all return
    None. A same-day sync rewrites today's RecoverySnapshot row in place
    (date is its primary key), so comparing values is the only change
    detector there is.
    """
    revisions = (plan_json or {}).get("_revisions") or []
    today_iso = today.isoformat()
    todays_adapts = [
        r for r in revisions
        if r.get("source") == "adapt_today"
        and str(r.get("at", "")).startswith(today_iso)
    ]
    if len(todays_adapts) >= MAX_ADAPTS_PER_DAY:
        return None
    prev = todays_adapts[-1].get("inputs") if todays_adapts else None
    if not prev:
        return None
    now = _recovery_inputs(db)
    if not now:
        return None
    moved = []
    for field, (label, threshold) in ADAPT_SUPERSEDE_THRESHOLDS.items():
        prev_v, now_v = prev.get(field), now.get(field)
        if prev_v is None or now_v is None:
            continue
        if abs(now_v - prev_v) > threshold:
            moved.append(f"{label} {prev_v:g}→{now_v:g}")
    return ", ".join(moved) if moved else None


def todays_training_done(db) -> bool:
    """True when every planned non-rest workout today already has a matching
    activity (compliance.day_training_done). No plan, or no entry for today,
    counts as not-done — there is nothing to guard."""
    from datetime import datetime, timedelta

    from backend.models.database import Activity
    from backend.services.compliance import day_training_done
    from backend.services.plan_normalizer import normalize_plan

    today = get_local_today()
    week_start = today - timedelta(days=today.weekday())
    plan_row = week_plan(db, week_start)
    if not plan_row:
        return False
    day = normalize_plan(plan_row.plan_json).get("days", {}).get(
        get_local_now().strftime("%A"))
    if day is None:
        return False
    acts = db.query(Activity).filter(
        Activity.start_time >= datetime.combine(today, datetime.min.time()),
        Activity.start_time <= datetime.combine(today, datetime.max.time()),
    ).all()
    return day_training_done(day, acts)


def _preserve_adapted_distances(adapted_day: dict, original_day: dict) -> None:
    """adapt_daily's output format has no distance_km, so an adaptation
    silently dropped it and downstream planned-km accounting counted the day
    from step text or minutes/6 (2026-09-05: an adapted Wednesday read 2 km
    against ~5 km of steps). Recompute from the adapted steps when they name
    every distance; otherwise carry the original's figure, scaled down when
    the session was trimmed. Never invent one the original didn't have."""
    from backend.services.plan_normalizer import map_sport
    from backend.services.volume_gate import parse_minutes, steps_sum_km

    originals = [w for w in (original_day.get("workouts") or [])
                 if isinstance(w, dict)]
    for w in adapted_day.get("workouts") or []:
        if not isinstance(w, dict) or w.get("distance_km") is not None:
            continue
        sport = map_sport(w.get("sport") or "")
        if sport in ("rest", "strength"):
            continue
        total, complete = steps_sum_km(w)
        if total and complete:
            w["distance_km"] = round(total, 1)
            continue
        match = next(
            (o for o in originals
             if map_sport(o.get("sport") or "") == sport
             and isinstance(o.get("distance_km"), (int, float))),
            None,
        )
        if match is None:
            continue
        # Consume the match: two same-sport sessions must not both inherit
        # the first original's distance.
        originals.remove(match)
        orig_min = parse_minutes(match.get("total_time"))
        new_min = parse_minutes(w.get("total_time"))
        if orig_min and new_min and new_min < orig_min:
            # Trimmed session: the distance shrinks with the time.
            w["distance_km"] = round(
                float(match["distance_km"]) * new_min / orig_min, 1)
        else:
            w["distance_km"] = float(match["distance_km"])


def hand_set_today() -> bool:
    """True when this week is hand-set (hand_set_weeks.py)."""
    from datetime import timedelta
    from backend.services.hand_set_guard import is_hand_set
    today = get_local_today()
    return is_hand_set(today - timedelta(days=today.weekday()))


def adapt_today_workout(db, body: dict = None):
    """Adapt today's workout in the weekly plan based on today's fresh recovery metrics."""
    from datetime import date, timedelta, datetime
    from backend.models.database import Activity
    from backend.agents.data_agent import DataAgent
    from backend.agents.response_agent import ResponseAgent
    from backend.services.periodization_engine import PeriodizationEngine
    
    today = get_local_today()
    start_of_week = today - timedelta(days=today.weekday())
    today_day_name = get_local_now().strftime("%A")  # Monday, Tuesday, etc.
    
    plan_record = week_plan(db, start_of_week)
    if not plan_record:
        raise HTTPException(status_code=404, detail="Weekly plan not found for this week. Please fetch /weekly-plan first.")
    
    # --- Idempotency guard: prevent double adaptation on the same day ---
    # One exception, the "second opinion": if a later sync materially moved
    # the recovery numbers the earlier adaptation was based on, one
    # superseding re-adaptation is allowed (MAX_ADAPTS_PER_DAY total).
    is_simulated = bool(body and any(body.get(k) for k in ("hrv", "rhr", "soreness")))

    # --- Athlete override wins the day ---
    # "Use original plan" is a decision, not a data point. After it, no
    # automatic re-adaptation today — however the numbers move. Simulated
    # calls (the visualizer's what-if tester) stay exempt.
    if not is_simulated:
        today_iso = today.isoformat()
        overrides = [
            r for r in ((plan_record.plan_json or {}).get("_revisions") or [])
            if r.get("source") == "use_original"
            and str(r.get("at", "")).startswith(today_iso)
        ]
        if overrides:
            from backend.services.plan_normalizer import normalize_plan
            existing_plan = normalize_plan(plan_record.plan_json)
            print("⛔ Athlete override active — not re-adapting today.")
            return existing_plan.get("days", {}).get(today_day_name, {})

    supersede_reason = None
    if plan_record.last_adapted:
        last_adapted_date = plan_record.last_adapted.date() if hasattr(plan_record.last_adapted, 'date') else plan_record.last_adapted
        if last_adapted_date == today:
            if not is_simulated:
                supersede_reason = _recovery_changed_since_adapt(db, plan_record.plan_json, today)
            if not supersede_reason:
                # Already adapted today — return the existing adapted workout instead of re-adapting
                from backend.services.plan_normalizer import normalize_plan
                existing_plan = normalize_plan(plan_record.plan_json)
                existing_day = existing_plan.get("days", {}).get(today_day_name, {})
                print(f"⚠️ Idempotency guard: already adapted today ({today}). Returning existing adaptation.")
                return existing_day
            print(f"🔁 Second opinion: {supersede_reason} — superseding today's adaptation.")

    # --- Staleness guard: block adaptation if recovery data is not from today ---
    from backend.models.database import RecoverySnapshot
    if not is_simulated:
        latest_snap = db.query(RecoverySnapshot).order_by(RecoverySnapshot.date.desc()).first()
        if not latest_snap:
            raise HTTPException(status_code=409, detail="Cannot adapt: no recovery data available. Run a sync first.")
        snap_date = latest_snap.date if isinstance(latest_snap.date, date) else latest_snap.date.date() if hasattr(latest_snap.date, 'date') else latest_snap.date
        if snap_date != today:
            raise HTTPException(
                status_code=409,
                detail=f"Cannot adapt: recovery data is stale (last snapshot: {snap_date}, today: {today}). Run a sync first."
            )
        
    from backend.services.plan_normalizer import normalize_plan
    plan_json = normalize_plan(plan_record.plan_json)
    days_dict = plan_json.get("days", {})
    if today_day_name not in days_dict:
        raise HTTPException(status_code=400, detail=f"Today's day name ({today_day_name}) not found in the weekly plan.")

    # --- Completion gate: today's training already happened ---
    # Adapting a finished day is circular — the workout's own load reads as
    # fresh fatigue on the next sync and rewrites the session it came from
    # (2026-09-02: a 9pm refresh re-adapted the day the athlete had already
    # trained). Real calls only; the simulator may keep testing what-ifs.
    if not is_simulated and todays_training_done(db):
        print(f"⛔ Completion gate: today's training is done — not adapting {today_day_name}.")
        return days_dict[today_day_name]

    # Receipt snapshot — before the adaptation rewrites today.
    from backend.services.plan_meta import capture_before
    before = capture_before(plan_json, days=[today_day_name])
        
    planned_workout_day = days_dict[today_day_name]
    
    # Compute TrainingContext using PeriodizationEngine
    engine = PeriodizationEngine()
    training_context = engine.compute_context(db)
    
    # Find yesterday's last activity
    yesterday_date = today - timedelta(days=1)
    yesterday_start = datetime.combine(yesterday_date, datetime.min.time())
    yesterday_end = datetime.combine(yesterday_date, datetime.max.time())
    yesterday_activity = db.query(Activity).filter(
        Activity.start_time >= yesterday_start,
        Activity.start_time <= yesterday_end
    ).order_by(Activity.start_time.desc()).first()
    
    yesterday_info = None
    if yesterday_activity:
        yesterday_info = {
            "sport": yesterday_activity.sport,
            "duration_min": round((yesterday_activity.duration_sec or 0) / 60),
            "distance_km": round((yesterday_activity.distance_m or 0) / 1000, 2) if yesterday_activity.distance_m else None,
            "training_load": yesterday_activity.training_load,
            "avg_hr": yesterday_activity.avg_hr
        }
        
    # Tomorrow's planned workout
    tomorrow_day_name = (today + timedelta(days=1)).strftime("%A")
    tomorrow_workout = plan_json.get("days", {}).get(tomorrow_day_name)
    
    # Add to training_context for the LLM
    training_context["yesterday_activity"] = yesterday_info
    training_context["tomorrow_workout"] = tomorrow_workout
    
    # Get fresh recovery metrics
    data_agent = DataAgent(db)
    today_metrics = data_agent.summarize()
    
    # Apply simulated recovery override for the web visualizer's adaptation tester
    if body:
        mock_parts = []
        if body.get("hrv") == "low":
            mock_parts.append("  Today HRV: 68 ms (Low, fatigued)")
            mock_parts.append("  ⚠️ HRV 17% below baseline - mild fatigue signal")
        elif body.get("hrv") == "crushed":
            mock_parts.append("  Today HRV: 45 ms (CRUSHED, extremely fatigued)")
            mock_parts.append("  ⚠️ HRV 45% below baseline - severe fatigue signal!")
        elif body.get("hrv") == "high":
            mock_parts.append("  Today HRV: 95 ms (High, very fresh)")
            
        if body.get("rhr") == "elevated":
            mock_parts.append("  Today RHR: 56 bpm (Elevated +5)")
            mock_parts.append("  ⚠️ RHR slightly elevated (+5 bpm above average)")
        elif body.get("rhr") == "spiked":
            mock_parts.append("  Today RHR: 61 bpm (SPIKED +10)")
            mock_parts.append("  ⚠️ RHR spiked (+10 bpm above average) - severe stress signal!")
            
        if body.get("soreness") == "mild":
            mock_parts.append("  ⚠️ Muscle soreness: Mild (calf stiffness)")
        elif body.get("soreness") == "sore":
            mock_parts.append("  ⚠️ Muscle soreness: High (quadriceps fatigue) - legs are highly sore!")
            
        if mock_parts:
            # Overwrite/append to the recovery metrics section
            today_metrics += "\n\n[SIMULATED RECOVERY MARKERS OVERRIDE - COACH MUST USE THESE INSTEAD OF REAL DATA]:\n" + "\n".join(mock_parts)
            print(f"🔬 Simulated adaptation override active: hrv={body.get('hrv')}, rhr={body.get('rhr')}, soreness={body.get('soreness')}")
    
    # Adapt
    response_agent = ResponseAgent()
    # A failed LLM call persists nothing (no receipt, last_adapted untouched),
    # like weekly generation. The auto path catches this and records adapt_error.
    try:
        adapted_day = response_agent.adapt_daily(planned_workout_day, today_metrics, training_context=training_context)
    except Exception as e:
        raise HTTPException(
            status_code=502,
            detail=f"Adaptation failed; existing plan left untouched: {e}"
        )
    _preserve_adapted_distances(adapted_day, planned_workout_day)

    # Update weekly plan
    if "original_workouts" not in planned_workout_day:
        adapted_day["original_workouts"] = planned_workout_day.get("workouts", [])
    else:
        adapted_day["original_workouts"] = planned_workout_day["original_workouts"]
    plan_json["days"][today_day_name] = adapted_day

    # Single persist pipeline, today only. Adaptation can swap the sport
    # entirely (a hard run downgraded to an easy spin), so it can invent a
    # violation the original plan didn't have.
    from backend.services.constraint_enforcer import get_active_injuries
    from backend.services.plan_meta import run_plan_write_pipeline
    from backend.services.plan_meta import commit_plan_write
    athlete_row = db.query(Athlete).first()
    reason = (f"Second opinion — {supersede_reason} since today's earlier adaptation."
              if supersede_reason
              else "Today's workout adapted to recovery metrics.")
    inputs = None if is_simulated else _recovery_inputs(db)
    plan_json, violations = run_plan_write_pipeline(
        db, plan_json,
        source="adapt_today",
        availability=training_context.get("availability", {}),
        active_injuries=get_active_injuries(db, athlete_row.id) if athlete_row else [],
        days=[today_day_name],
        reason=reason,
        before=before,
        inputs=inputs,
    )

    plan_json, _ = commit_plan_write(
        db, plan_record, plan_json,
        days=[today_day_name],
        source="adapt_today",
        availability=training_context.get("availability", {}),
        reason=reason,
        inputs=inputs,
        # Athlete-local clock, naive (the column is tz-naive). datetime.now() was
        # UTC on Render: an evening adapt stamped tomorrow's date, so the guard
        # missed the rest of the evening and wrongly blocked the next morning.
        columns={"last_adapted": get_local_now().replace(tzinfo=None)},
    )
    adapted_day = plan_json["days"][today_day_name]

    # Today's course on the watch follows the adapted day (update in place).
    watch_sync_safe(db, plan_json, start_of_week, days=[today_day_name])

    return adapted_day


def use_original_today(db):
    """Restore today's pre-adaptation workouts — the sheet's "Override & Use
    Original Plan". The override is receipted (source "use_original") and
    blocks further automatic re-adaptation today; the athlete outranks the
    trigger math."""
    from datetime import timedelta

    from backend.services.constraint_enforcer import get_active_injuries
    from backend.services.periodization_engine import PeriodizationEngine
    from backend.services.plan_meta import (
        capture_before, commit_plan_write, run_plan_write_pipeline,
    )
    from backend.services.plan_normalizer import normalize_plan

    today = get_local_today()
    start_of_week = today - timedelta(days=today.weekday())
    today_day_name = get_local_now().strftime("%A")

    plan_record = week_plan(db, start_of_week)
    if not plan_record:
        raise HTTPException(status_code=404, detail="Weekly plan not found for this week.")

    plan_json = normalize_plan(plan_record.plan_json)
    day = plan_json.get("days", {}).get(today_day_name)
    if not day or not day.get("original_workouts"):
        raise HTTPException(status_code=409, detail="Nothing to restore: today has no adaptation.")

    before = capture_before(plan_json, days=[today_day_name])
    restored = dict(day)
    restored["workouts"] = day["original_workouts"]
    restored.pop("original_workouts", None)
    restored.pop("adaptation", None)
    # The original summary died with the adaptation (only workouts were
    # preserved) — say what happened instead of showing the adapted text.
    restored["summary"] = "Original plan restored — athlete override."
    plan_json["days"][today_day_name] = restored

    # Constraints may have changed since the original was written (a new
    # injury logged today) — the restored day passes the same gate as any
    # other write.
    training_context = PeriodizationEngine().compute_context(db)
    athlete_row = db.query(Athlete).first()
    reason = "Athlete override — original workout restored over today's adaptation."
    plan_json, violations = run_plan_write_pipeline(
        db, plan_json,
        source="use_original",
        availability=training_context.get("availability", {}),
        active_injuries=get_active_injuries(db, athlete_row.id) if athlete_row else [],
        days=[today_day_name],
        reason=reason,
        before=before,
    )
    plan_json, _ = commit_plan_write(
        db, plan_record, plan_json,
        days=[today_day_name],
        source="use_original",
        availability=training_context.get("availability", {}),
        reason=reason,
    )
    # The watch follows the athlete's override too: today's course goes back
    # to the original in place (same slot), unless COROS has locked it.
    watch_sync_safe(db, plan_json, start_of_week, days=[today_day_name])
    return plan_json["days"][today_day_name]
