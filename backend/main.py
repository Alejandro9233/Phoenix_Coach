import os
import json
import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI, Depends, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from backend.models.database import Base, Athlete, Activity, RecoverySnapshot
from backend.services.fit_importer import parse_fit_file
from backend.services.coros_scraper import CorosScraper
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from dotenv import load_dotenv

load_dotenv(override=True)

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./phoenix_coach.db")
connect_args = {}
if DATABASE_URL.startswith("sqlite"):
    connect_args["check_same_thread"] = False
engine = create_engine(DATABASE_URL, connect_args=connect_args)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# Create tables
Base.metadata.create_all(bind=engine)

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


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize singletons and warm up Ollama on startup."""
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
        if athlete and athlete.training_start_date is None:
            # Query the earliest activity
            earliest_act = db.query(Activity).order_by(Activity.start_time.asc()).first()
            if earliest_act and earliest_act.start_time:
                athlete.training_start_date = earliest_act.start_time.date()
                db.commit()
                print(f"✅ Set athlete training_start_date to earliest activity date: {athlete.training_start_date}")
            else:
                # Fallback to 4 weeks ago
                athlete.training_start_date = date.today() - timedelta(weeks=4)
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
        "swim_days": athlete.swim_days or "wed,sat,sun",
        "bike_days": athlete.bike_days or "mon,tue,wed,thu,fri,sat,sun",
        "run_days": athlete.run_days or "mon,tue,wed,thu,fri,sat,sun",
        "strength_days": athlete.strength_days or "mon,wed,fri",
        "target_finish_time": athlete.target_finish_time,
        "training_start_date": str(athlete.training_start_date) if athlete.training_start_date else None,
    }


@app.put("/athlete/profile")
def update_athlete_profile(body: dict, db: Session = Depends(get_db)):
    """Update the athlete's profile."""
    from datetime import date as date_type
    
    athlete = db.query(Athlete).first()
    if not athlete:
        athlete = Athlete(name="New Athlete")
        db.add(athlete)
    
    # Update only provided fields
    updatable = [
        "name", "age", "weight_kg", "race_name", "race_type", "race_distance",
        "swim_days", "bike_days", "run_days", "strength_days", "target_finish_time"
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
    
    db.commit()
    db.refresh(athlete)
    print(f"Profile updated: {athlete.name}, race={athlete.race_name} on {athlete.race_date}, start={athlete.training_start_date}")
    return {"status": "ok", "message": "Profile updated"}

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
    
    return {
        "athlete": athlete,
        "activities": activities,
        "recovery": recovery
    }


@app.get("/coaching")
def get_coaching_recommendation(db: Session = Depends(get_db)):
    """Generate a coaching recommendation using the 2-agent pipeline."""
    from backend.agents.data_agent import DataAgent
    from backend.agents.response_agent import ResponseAgent
    
    # 1. Data Agent: summarize athlete state
    data_agent = DataAgent(db)
    summary = data_agent.summarize()
    
    # 2. Response Agent: generate recommendation via Ollama + RAG
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


@app.get("/block-calendar")
def get_block_calendar(db: Session = Depends(get_db)):
    """Return the full training block calendar from start to race."""
    from backend.services.periodization_engine import PeriodizationEngine
    engine = PeriodizationEngine()
    return engine.compute_block_calendar(db)


@app.get("/weekly-plan")
def get_weekly_plan(db: Session = Depends(get_db)):
    """Retrieve the current week's training plan. Generates a new one if not found."""
    from datetime import date, timedelta, datetime
    from backend.models.database import WeeklyPlan, Athlete
    from backend.agents.data_agent import DataAgent
    from backend.agents.response_agent import ResponseAgent
    from backend.services.periodization_engine import PeriodizationEngine
    
    today = date.today()
    # Monday of the current week
    start_of_week = today - timedelta(days=today.weekday())
    
    # Check if a plan already exists
    plan_record = db.query(WeeklyPlan).filter(WeeklyPlan.week_start == start_of_week).order_by(WeeklyPlan.id.desc()).first()
    if plan_record:
        from backend.services.plan_normalizer import normalize_plan
        return normalize_plan(plan_record.plan_json)
        
    # If not, generate a new one
    data_agent = DataAgent(db)
    summary = data_agent.summarize()
    
    athlete = db.query(Athlete).first()
    profile = {
        "race_name": athlete.race_name,
        "race_distance": athlete.race_distance,
        "race_date": str(athlete.race_date) if athlete.race_date else None,
        "weekly_hours_target": athlete.weekly_hours_target or 8.0,
        "swim_days": athlete.swim_days or "wed,sat,sun",
        "bike_days": athlete.bike_days or "mon,tue,wed,thu,fri,sat,sun",
        "run_days": athlete.run_days or "mon,tue,wed,thu,fri,sat,sun",
        "strength_days": athlete.strength_days or "mon,wed,fri"
    } if athlete else {}
    
    # Compute Training Context using PeriodizationEngine
    engine = PeriodizationEngine()
    training_context = engine.compute_context(db)
    
    # Check if is_recovery_week changed from last week → log it
    try:
        last_monday = start_of_week - timedelta(days=7)
        last_plan = db.query(WeeklyPlan).filter(WeeklyPlan.week_start == last_monday).order_by(WeeklyPlan.id.desc()).first()
        if last_plan and last_plan.plan_json and "_context" in last_plan.plan_json:
            was_recovery_week = last_plan.plan_json["_context"].get("is_recovery_week", False)
            is_recovery_week = training_context.get("is_recovery_week", False)
            if was_recovery_week != is_recovery_week:
                print(f"🔄 Recovery week status changed! Was recovery week: {was_recovery_week}, Is recovery week: {is_recovery_week}")
    except Exception as e:
        print(f"⚠️ Error checking previous week's recovery status: {e}")
        
    response_agent = ResponseAgent()
    new_plan_json = response_agent.generate_weekly_plan(summary, profile, training_context=training_context)
    
    # Store training_context in new_plan_json under "_context" key for auditability
    new_plan_json["_context"] = training_context
    
    # Generate a weekly review looking back at the past week's results
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
            
    # Normalize the generated plan
    from backend.services.plan_normalizer import normalize_plan
    new_plan_json = normalize_plan(new_plan_json)
            
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


@app.post("/weekly-plan/regenerate")
def regenerate_weekly_plan(db: Session = Depends(get_db)):
    """Delete current week's plan and generate a fresh one."""
    from datetime import date, timedelta
    from backend.models.database import WeeklyPlan
    
    today = date.today()
    start_of_week = today - timedelta(days=today.weekday())
    
    # Delete existing plan for this week (overwrite)
    db.query(WeeklyPlan).filter(
        WeeklyPlan.week_start == start_of_week
    ).delete()
    db.commit()
    
    # Reuse the GET /weekly-plan logic to generate a new plan
    return get_weekly_plan(db)


@app.post("/weekly-plan/adapt-today")
def adapt_today_workout(body: dict = None, db: Session = Depends(get_db)):
    """Adapt today's workout in the weekly plan based on today's fresh recovery metrics."""
    from datetime import date, timedelta, datetime
    from backend.models.database import WeeklyPlan, Activity
    from backend.agents.data_agent import DataAgent
    from backend.agents.response_agent import ResponseAgent
    from backend.services.periodization_engine import PeriodizationEngine
    
    today = date.today()
    start_of_week = today - timedelta(days=today.weekday())
    today_day_name = datetime.now().strftime("%A")  # Monday, Tuesday, etc.
    
    plan_record = db.query(WeeklyPlan).filter(WeeklyPlan.week_start == start_of_week).order_by(WeeklyPlan.id.desc()).first()
    if not plan_record:
        raise HTTPException(status_code=404, detail="Weekly plan not found for this week. Please fetch /weekly-plan first.")
        
    from backend.services.plan_normalizer import normalize_plan
    plan_json = normalize_plan(plan_record.plan_json)
    days_dict = plan_json.get("days", {})
    if today_day_name not in days_dict:
        raise HTTPException(status_code=400, detail=f"Today's day name ({today_day_name}) not found in the weekly plan.")
        
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
    adapted_day = response_agent.adapt_daily(planned_workout_day, today_metrics, training_context=training_context)
    
    # Update weekly plan
    if "original_workouts" not in planned_workout_day:
        adapted_day["original_workouts"] = planned_workout_day.get("workouts", [])
    else:
        adapted_day["original_workouts"] = planned_workout_day["original_workouts"]
    plan_json["days"][today_day_name] = adapted_day
    
    # We must explicitly flag the JSON as modified for SQLAlchemy to update it
    from sqlalchemy.orm.attributes import flag_modified
    plan_record.plan_json = plan_json
    plan_record.last_adapted = datetime.now()
    flag_modified(plan_record, "plan_json")
    
    db.commit()
    
    return adapted_day


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
    
    # 1. Try running the COROS scraper
    try:
        scraper = CorosScraper()
        data = await scraper.scrape_all()
        
        # Save scraped data
        with open("coros_scraped_data.json", "w") as f:
            json.dump(data, f, indent=2)
        
        # 2. Ingest into database
        service = IngestionService()
        service.ingest_coros_data("coros_scraped_data.json")
        sync_message = "COROS data synced successfully."
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


def _load_ratio_label(ratio):
    """Convert load ratio to a human-readable label."""
    if ratio is None:
        return "Unknown"
    if ratio < 0.5:
        return "Detraining"
    elif ratio < 0.8:
        return "Low"
    elif ratio <= 1.0:
        return "Optimal"
    elif ratio <= 1.3:
        return "Productive"
    elif ratio <= 1.5:
        return "High"
    else:
        return "Danger"


@app.post("/smart-refresh")
async def smart_refresh(db: Session = Depends(get_db)):
    """
    Single morning action: scrape → ingest → evaluate recovery → auto-adapt if needed.
    Returns recovery status and adaptation info.
    """
    from backend.services.ingestion_service import IngestionService

    sync_status = "ok"
    sync_message = ""

    # 1. Scrape COROS
    try:
        scraper = CorosScraper()
        data = await scraper.scrape_all()
        with open("coros_scraped_data.json", "w") as f:
            json.dump(data, f, indent=2)
        service = IngestionService()
        service.ingest_coros_data("coros_scraped_data.json")
        sync_message = "Biometrics synced."
    except Exception as e:
        sync_status = "partial"
        sync_message = f"Scraper error: {str(e)}. Using cached data."

    # 2. Get latest recovery snapshot
    latest = db.query(RecoverySnapshot).order_by(RecoverySnapshot.date.desc()).first()

    # 3. Deterministic recovery evaluation
    needs_adaptation = False
    adaptation_reasons = []

    if latest:
        athlete = db.query(Athlete).first()
        # HRV check
        if latest.hrv_ms and athlete and athlete.hrv_baseline:
            hrv_drop_pct = (latest.hrv_ms - athlete.hrv_baseline) / athlete.hrv_baseline * 100
            if hrv_drop_pct < -15:
                needs_adaptation = True
                adaptation_reasons.append(f"HRV {hrv_drop_pct:.0f}% below baseline")
        # RHR check
        rhr_snapshots = db.query(RecoverySnapshot).order_by(RecoverySnapshot.date.desc()).limit(7).all()
        rhr_values = [s.resting_hr for s in rhr_snapshots if s.resting_hr]
        if rhr_values and latest.resting_hr:
            avg_rhr = sum(rhr_values) / len(rhr_values)
            if latest.resting_hr > avg_rhr + 5:
                needs_adaptation = True
                adaptation_reasons.append(f"RHR elevated: {latest.resting_hr} vs avg {avg_rhr:.0f}")
        # Fatigue state check
        if latest.fatigue_state and latest.fatigue_state >= 4:
            needs_adaptation = True
            fatigue_labels = {4: "Fatigued", 5: "Overreaching"}
            adaptation_reasons.append(f"Fatigue zone: {fatigue_labels.get(latest.fatigue_state, 'High')}")
        # Load ratio check
        if latest.load_ratio and latest.load_ratio > 1.4:
            needs_adaptation = True
            adaptation_reasons.append(f"Load ratio {latest.load_ratio:.2f} — high injury risk")
        # TIB check
        if latest.tib is not None and latest.tib < -20:
            needs_adaptation = True
            adaptation_reasons.append(f"Form (TIB) at {latest.tib:.0f} — deep fatigue")

    # 4. Auto-adapt if needed
    adapted = False
    if needs_adaptation:
        try:
            adapt_today_workout(body=None, db=db)
            adapted = True
            print(f"🔄 Auto-adapted today's workout. Reasons: {adaptation_reasons}")
        except Exception as e:
            print(f"⚠️ Auto-adaptation failed: {e}")
            adapted = False

    # 5. Build response
    recovery_summary = {
        "hrv_ms": latest.hrv_ms if latest else None,
        "resting_hr": latest.resting_hr if latest else None,
        "load_ratio": latest.load_ratio if latest else None,
        "load_ratio_label": _load_ratio_label(latest.load_ratio) if latest else None,
        "cti": latest.cti if latest else None,
        "ati": latest.ati if latest else None,
        "tib": latest.tib if latest else None,
        "fatigue_state": latest.fatigue_state if latest else None,
        "stamina_level": latest.performance_index if latest else None,
    }

    return {
        "sync_status": sync_status,
        "sync_message": sync_message,
        "recovery": recovery_summary,
        "adaptation": {
            "needed": needs_adaptation,
            "adapted": adapted,
            "reasons": adaptation_reasons
        }
    }

def _strip_think_tags(text: str) -> str:
    """Remove <think>...</think> blocks from Qwen3 output."""
    import re
    # Remove complete think blocks
    cleaned = re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL)
    # Remove any unclosed <think> tag at the start (streaming edge case)
    cleaned = re.sub(r'^<think>.*', '', cleaned, flags=re.DOTALL)
    return cleaned.strip()


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
    if not message:
        raise HTTPException(status_code=400, detail="Message is required")
    
    # Build athlete context
    data_agent = DataAgent(db)
    summary = data_agent.summarize()
    
    # Get relevant knowledge from singleton KB
    kb = get_kb()
    rag_chunks = kb.query(message, n_results=3)
    rag_context = "\n\n".join(rag_chunks) if rag_chunks else ""
    
    system_prompt = f"""You are Phoenix, an elite triathlon coach. You talk like a real coach — direct, confident, no fluff.

ATHLETE DATA:
{summary}

COACHING KNOWLEDGE:
{rag_context}

RULES:
- Lead with the answer, then give 1-2 key reasons using the athlete's actual numbers.
- Maximum 4-6 lines total. No headers, no essays, no bullet-point lists longer than 3 items.
- If the athlete asks a yes/no question, start with yes or no.
- Reference specific data points (HR zones, load ratio, TIB, etc.) to justify your advice.
- Sound like a coach in person, not a textbook."""

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": message}
    ]

    async def event_stream():
        """Generator that yields SSE events with streamed tokens."""
        try:
            from backend.core.llm_client import chat_completion_stream
            async for token in chat_completion_stream(messages):
                yield f"data: {json.dumps({'token': token})}\n\n"
            yield "data: [DONE]\n\n"
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
    if not message:
        raise HTTPException(status_code=400, detail="Message is required")
    
    # Build athlete context
    data_agent = DataAgent(db)
    summary = data_agent.summarize()
    
    # Get relevant knowledge from singleton KB
    kb = get_kb()
    rag_chunks = kb.query(message, n_results=3)
    rag_context = "\n\n".join(rag_chunks) if rag_chunks else ""
    
    system_prompt = f"""You are Phoenix, an elite triathlon coach. You talk like a real coach — direct, confident, no fluff.

ATHLETE DATA:
{summary}

COACHING KNOWLEDGE:
{rag_context}

RULES:
- Lead with the answer, then give 1-2 key reasons using the athlete's actual numbers.
- Maximum 4-6 lines total. No headers, no essays, no bullet-point lists longer than 3 items.
- If the athlete asks a yes/no question, start with yes or no.
- Reference specific data points (HR zones, load ratio, TIB, etc.) to justify your advice.
- Sound like a coach in person, not a textbook."""

    try:
        from backend.core.llm_client import chat_completion
        content = chat_completion(messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": message}
        ])
        return {"response": content}
    except Exception as e:
        print(f"LLM ERROR in /chat-sync: {e}")
        return {"response": f"I'm currently in offline mode. Error: {str(e)[:100]}"}


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
    from backend.models.database import Activity, AthleteFeedback, WeeklyPlan
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
        plan_record = db.query(WeeklyPlan).filter(WeeklyPlan.week_start == start_of_week).order_by(WeeklyPlan.id.desc()).first()
        
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
    
    agent = ResponseAgent()
    analysis = agent.analyze_activity(
        activity_data,
        planned_workout=planned_workout,
        compliance=compliance,
        training_context=training_context
    )
    return analysis

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001)


