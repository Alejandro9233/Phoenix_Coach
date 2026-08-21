import copy
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from datetime import date, datetime, time, timedelta
from backend.models.database import Base, Athlete, Activity, WeeklyPlan
from backend.agents.data_agent import DataAgent
from backend.agents.response_agent import _format_training_context, _format_workout_menu
from backend.main import _build_chat_context
from backend.services.periodization_engine import DISTANCE_PROFILES, PeriodizationEngine
from backend.utils.timezone import get_local_today
import json

@pytest.fixture
def temp_db_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()
    engine.dispose()

def test_data_agent_zones(temp_db_session):
    # Setup athlete with zones
    athlete = Athlete(
        name="Test Athlete",
        ftp_watts=200,
        hr_zones=[{"index": 0, "hr": 140}],
        pace_zones=[{"index": 0, "pace": 300}], # 5:00/km
        cycle_power_zones=[{"index": 0, "power": 150}]
    )
    temp_db_session.add(athlete)
    temp_db_session.commit()
    
    agent = DataAgent(temp_db_session)
    summary = agent.summarize()
    
    assert "Z1 <140bpm" in summary
    assert "Z1 5:00/km" in summary
    assert "Z1 <150W" in summary
    assert "Power Zones (FTP 200.0W)" in summary

def test_build_chat_context(temp_db_session):
    # Test that _build_chat_context injects the weekly plan correctly
    today = date.today()
    start_of_week = today - timedelta(days=today.weekday())
    
    # Create dummy weekly plan
    plan_json = {
        "days": {
            "Monday": {"summary": "Rest", "workouts": []},
            "Tuesday": {"summary": "Intervals", "workouts": [{"sport": "running", "title": "Track day", "steps": [], "total_time": "1h", "hr_target": "Z4"}]}
        }
    }
    
    plan = WeeklyPlan(
        week_start=start_of_week,
        athlete_id=1,
        plan_json=plan_json
    )
    temp_db_session.add(plan)
    temp_db_session.commit()
    
    summary = "SUMMARY_TEXT"
    rag_context = "RAG_TEXT"
    
    system_prompt = _build_chat_context(temp_db_session, summary, rag_context)
    
    assert "SUMMARY_TEXT" in system_prompt
    assert "RAG_TEXT" in system_prompt
    assert "FULL WEEK SCHEDULE" in system_prompt
    assert "- Monday: Rest" in system_prompt
    assert "- Tuesday: Intervals" in system_prompt
    assert "TODAY'S WORKOUT" in system_prompt
    # The athlete code-switches Spanish/English — the coach must follow.
    assert "language of the athlete's most recent message" in system_prompt


# ─── A10: availability-aware context (sport_sessions / workout_menu) ──────────

def _marathon_athlete(**overrides):
    # No race_date -> weeks_to_race 99 -> foundation phase.
    fields = dict(name="Test Athlete", race_distance="Marathon")
    fields.update(overrides)
    return Athlete(**fields)


def _last_monday():
    today = get_local_today()
    return today - timedelta(days=today.weekday() + 7)


def _last_week_activity(n, day_offset=0, sport="running"):
    return Activity(
        id=f"act-{n}",
        athlete_id=1,
        sport=sport,
        start_time=datetime.combine(_last_monday() + timedelta(days=day_offset), time(8, 0)),
        duration_sec=3600,
        training_load=50,
        distance_m=10000,
    )


def test_disabled_sport_dropped_from_context(temp_db_session):
    athlete = _marathon_athlete(swim_days="")  # "" = never
    temp_db_session.add(athlete)
    temp_db_session.commit()

    ctx = PeriodizationEngine().compute_context(temp_db_session)

    assert ctx["phase"] == "foundation"
    assert "swimming" not in ctx["volume_references"]["sport_sessions"]
    assert "swimming" not in ctx["workout_menu"]
    # Formatted prompt must not advertise swim sessions the enforcer would strip.
    assert "Swimming: 2x/week" not in _format_training_context(ctx)
    assert "Swimming" not in _format_workout_menu(ctx)


def test_legacy_null_availability_keeps_sport(temp_db_session):
    athlete = _marathon_athlete()
    temp_db_session.add(athlete)
    temp_db_session.commit()
    athlete.swim_days = None  # legacy row: no constraint recorded
    temp_db_session.commit()

    ctx = PeriodizationEngine().compute_context(temp_db_session)

    assert "swimming" in ctx["volume_references"]["sport_sessions"]
    assert "swimming" in ctx["workout_menu"]


def test_sessions_capped_at_available_days(temp_db_session):
    # Marathon foundation advertises strength 3x/week; one available day caps it.
    athlete = _marathon_athlete(strength_days="mon")
    temp_db_session.add(athlete)
    temp_db_session.commit()

    ctx = PeriodizationEngine().compute_context(temp_db_session)

    strength = ctx["volume_references"]["sport_sessions"]["strength"]
    assert strength["sessions"] == 1
    assert "capped" in strength["volume_note"]
    assert "only 1 day(s) available" in strength["volume_note"]


def test_distance_profiles_never_mutated(temp_db_session):
    snapshot = copy.deepcopy(DISTANCE_PROFILES)
    athlete = _marathon_athlete(swim_days="", strength_days="mon")
    temp_db_session.add(athlete)
    temp_db_session.commit()

    engine = PeriodizationEngine()
    engine.compute_context(temp_db_session)

    # Second pass with different availability — same process, same shared dicts.
    athlete.swim_days = "wed"
    athlete.strength_days = ""
    temp_db_session.commit()
    engine.compute_context(temp_db_session)

    assert DISTANCE_PROFILES == snapshot


def test_marathon_foundation_priorities_mention_no_swim(temp_db_session):
    athlete = _marathon_athlete()
    temp_db_session.add(athlete)
    temp_db_session.commit()

    ctx = PeriodizationEngine().compute_context(temp_db_session)

    assert ctx["phase"] == "foundation"
    assert "swim" not in ctx["phase_priorities"].lower()


# ─── A7: compliance_pct is None when there was no plan to comply with ─────────

def test_compliance_none_when_no_plan_row(temp_db_session):
    athlete = _marathon_athlete()
    temp_db_session.add(athlete)
    temp_db_session.add(_last_week_activity(1, day_offset=0))
    temp_db_session.add(_last_week_activity(2, day_offset=2))
    temp_db_session.commit()

    ctx = PeriodizationEngine().compute_context(temp_db_session)
    lw = ctx["last_week"]

    assert lw["compliance_pct"] is None
    assert "No plan on record" in lw["note"]
    text = _format_training_context(ctx)
    assert "Compliance: n/a" in text
    assert "Compliance: 0%" not in text


def test_compliance_pct_with_plan(temp_db_session):
    athlete = _marathon_athlete()
    temp_db_session.add(athlete)
    workout = {"sport": "running", "title": "Easy Run", "steps": [],
               "total_time": "1h", "hr_target": "Z2"}
    plan_json = {"days": {
        "Monday": {"summary": "Run", "workouts": [dict(workout)]},
        "Tuesday": {"summary": "Run", "workouts": [dict(workout)]},
        "Wednesday": {"summary": "Run", "workouts": [dict(workout)]},
        "Thursday": {"summary": "Run", "workouts": [dict(workout)]},
    }}
    temp_db_session.add(WeeklyPlan(week_start=_last_monday(), athlete_id=1,
                                   plan_json=plan_json))
    temp_db_session.add(_last_week_activity(1, day_offset=0))
    temp_db_session.add(_last_week_activity(2, day_offset=1))
    temp_db_session.commit()

    ctx = PeriodizationEngine().compute_context(temp_db_session)
    lw = ctx["last_week"]

    assert lw["sessions_planned"] == 4
    assert lw["compliance_pct"] == 50
    assert "Compliance: 50%" in _format_training_context(ctx)


def test_no_activities_and_no_plan_is_not_measurable(temp_db_session):
    athlete = _marathon_athlete()
    temp_db_session.add(athlete)
    temp_db_session.commit()

    lw = PeriodizationEngine().compute_context(temp_db_session)["last_week"]

    assert lw["sessions_completed"] == 0
    assert lw["compliance_pct"] is None
    assert "No plan on record" in lw["note"]


def test_fully_skipped_week_reads_as_zero_compliance(temp_db_session):
    """A plan existed but zero activities synced — that is real non-compliance
    (0%), never "no plan on record"."""
    athlete = _marathon_athlete()
    temp_db_session.add(athlete)
    workout = {"sport": "running", "title": "Easy Run", "steps": [],
               "total_time": "1h", "hr_target": "Z2"}
    plan_json = {"days": {
        "Monday": {"summary": "Run", "workouts": [dict(workout)]},
        "Wednesday": {"summary": "Run", "workouts": [dict(workout)]},
        "Friday": {"summary": "Run", "workouts": [dict(workout)]},
    }}
    temp_db_session.add(WeeklyPlan(week_start=_last_monday(), athlete_id=1,
                                   plan_json=plan_json))
    temp_db_session.commit()

    ctx = PeriodizationEngine().compute_context(temp_db_session)
    lw = ctx["last_week"]

    assert lw["sessions_completed"] == 0
    assert lw["sessions_planned"] == 3
    assert lw["compliance_pct"] == 0
    assert lw["missed"] == ["Monday running", "Wednesday running", "Friday running"]
    assert lw["note"] == "No activities recorded last week."
    # The false detraining disclaimer must not reach the prompt for this week.
    assert "Compliance: n/a" not in _format_training_context(ctx)
