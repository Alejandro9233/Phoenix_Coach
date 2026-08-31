"""The 2026-08-31 freshness/quality batch, one test group per fix:

- pace_enforcer reconciles impossible main-step durations (3 of 5 run days
  shipped e.g. "30:00" for "8 km at tempo" — 3:45/km, faster than threshold)
- volume gate: soft under-target and long-run-shortfall checks (40.0 vs 41.8
  and 80 vs 86 min shipped silently — both prompt-only before)
- weekly-plan generation waits for an in-flight smart-refresh (Monday's plan
  generated from Sunday-night data while the scrape was mid-flight)
- data_agent labels stale snapshots honestly instead of "Today"
- chat context reads the phase key the engine actually emits
"""
from datetime import timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import backend.main as main_mod
from backend.models.database import Athlete, Base, RecoverySnapshot, WeeklyPlan
from backend.services.pace_enforcer import enforce_paces
from backend.services import volume_gate
from backend.utils.timezone import get_local_today


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:",
                           connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)
    s = SessionLocal()
    s.add(Athlete(name="T", weight_kg=78.0,
                  race_date=get_local_today() + timedelta(weeks=10),
                  race_distance="Marathon", target_finish_time="3:10:00"))
    s.commit()
    yield s
    s.close()
    engine.dispose()


# --- Fix 5: main-step arithmetic -----------------------------------------

PACE_MODEL = {"bands": {
    "tempo": {"lo": 262.0, "hi": 270.0, "label": "4:22-4:30/km"},
    "easy": {"lo": 339.0, "hi": 376.0, "label": "5:39-6:16/km"},
}}


def _run_workout(title, main_duration, main_desc, total):
    return {"sport": "running", "title": title, "total_time": total,
            "distance_km": 8.0, "steps": [
                {"type": "warmup", "duration": "10:00", "zone": 1,
                 "description": "jog"},
                {"type": "main", "duration": main_duration, "zone": 4,
                 "description": main_desc},
                {"type": "cooldown", "duration": "10:00", "zone": 1,
                 "description": "jog"},
            ]}


def test_impossible_main_step_is_recomputed():
    plan = {"days": {"Tuesday": {"workouts": [
        _run_workout("Tempo Run", "30:00", "8 km at tempo pace", "50 min")]}}}
    plan, corrections = enforce_paces(plan, PACE_MODEL)
    w = plan["days"]["Tuesday"]["workouts"][0]
    main = next(s for s in w["steps"] if s["type"] == "main")
    # 8 km at 262-270 s/km midpoint 266 -> 2128s -> :30 rounding -> 35:30
    assert main["duration"] == "35:30"
    assert w["total_time"] == "56 min"  # 50 + 5.5 rounded
    assert any("step_from" in c for c in corrections)


def test_consistent_main_step_untouched():
    plan = {"days": {"Saturday": {"workouts": [
        _run_workout("Easy Run", "35:00", "6 km relaxed", "55 min")]}}}
    # 6 km easy needs 2034-2256s; 35:00=2100s is inside the band.
    plan, corrections = enforce_paces(plan, PACE_MODEL)
    main = next(s for s in plan["days"]["Saturday"]["workouts"][0]["steps"]
                if s["type"] == "main")
    assert main["duration"] == "35:00"
    assert not any("step_from" in c for c in corrections)


def test_step_without_km_claim_untouched():
    plan = {"days": {"Monday": {"workouts": [
        _run_workout("Easy Run", "30:00", "steady aerobic effort", "50 min")]}}}
    plan, corrections = enforce_paces(plan, PACE_MODEL)
    main = next(s for s in plan["days"]["Monday"]["workouts"][0]["steps"]
                if s["type"] == "main")
    assert main["duration"] == "30:00"


# --- Fix 6: under-target + long-run soft checks ---------------------------

def _gate_ctx():
    return {
        "volume_targets": {"run_km_target": 41.8, "run_km_hard_cap": 44.5,
                           "long_run_minutes": 86},
        "volume_references": {"phase_hours_range": "10-12",
                              "max_quality_sessions": 2,
                              "sport_sessions": {"running": {"sessions": 4}}},
    }


def _week_plan(run_days_km, long_run_min, easy_min=55):
    # easy_min must keep pace inside workout_km's 2.5-10.0 min/km
    # plausibility band, or the declared km is rejected and re-estimated
    # from minutes — which silently inflates the week.
    days = {}
    names = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]
    for i, km in enumerate(run_days_km):
        mins = long_run_min if i == len(run_days_km) - 1 else easy_min
        days[names[i]] = {"workouts": [{
            "sport": "running", "title": "Easy Run",
            "total_time": f"{mins} min", "distance_km": km, "steps": []}]}
    return {"days": days}


AVAIL = {"run_days": "mon,tue,wed,thu,fri,sat,sun"}


def test_under_target_and_short_long_run_warn():
    plan = _week_plan([8.0, 8.0, 6.0, 6.0, 12.0], long_run_min=80)  # 40.0 km
    report = volume_gate.audit_plan(plan, _gate_ctx(), days=None,
                                    availability=AVAIL, active_injuries=[])
    kinds = {v["kind"] for v in report.soft}
    assert "run_km_under_target" in kinds
    assert "long_run_short" in kinds
    assert not report.hard


def test_on_target_plan_stays_quiet():
    plan = _week_plan([8.0, 8.0, 6.0, 7.8, 12.0], long_run_min=86)  # 41.8 km
    report = volume_gate.audit_plan(plan, _gate_ctx(), days=None,
                                    availability=AVAIL, active_injuries=[])
    kinds = {v["kind"] for v in report.soft}
    assert "run_km_under_target" not in kinds
    assert "long_run_short" not in kinds


def test_under_floor_fires_low_not_under_target():
    plan = _week_plan([5.0, 5.0, 5.0, 5.0, 10.0], long_run_min=86,
                      easy_min=30)  # 30.0 km, paces plausible
    report = volume_gate.audit_plan(plan, _gate_ctx(), days=None,
                                    availability=AVAIL, active_injuries=[])
    kinds = {v["kind"] for v in report.soft}
    assert "run_km_low" in kinds
    assert "run_km_under_target" not in kinds


# --- Fix 3: generation waits for the in-flight refresh --------------------
# No wall clocks: review proved the timing version passed on pre-fix code
# (cold KnowledgeBase init ate the window). A fake time.sleep records the
# wait and drives the job state instead.


def _gen_week():
    return {"week_summary": {}, "days": {d: {"summary": "s", "workouts": [
        {"sport": "running", "title": "Easy Run", "total_time": "30 min",
         "distance_km": 5.0, "steps": []}]} for d in
        ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday",
         "Saturday", "Sunday"]}}


def _wire_generation(monkeypatch, order):
    from backend.services.plan_normalizer import normalize_plan
    import backend.services.plan_meta as plan_meta

    def fake_pipeline(dbx, plan_json=None, **kw):
        order.append("pipeline")
        return normalize_plan(_gen_week()), []

    monkeypatch.setattr(plan_meta, "run_plan_write_pipeline", fake_pipeline)
    monkeypatch.setattr(
        "backend.agents.data_agent.DataAgent.summarize", lambda self: "m")


def test_generation_waits_for_running_refresh(db, monkeypatch):
    order = []
    _wire_generation(monkeypatch, order)

    def fake_sleep(sec):
        order.append("sleep")
        main_mod._refresh_job.update(state="done")

    monkeypatch.setattr(main_mod.time, "sleep", fake_sleep)
    monkeypatch.setitem(main_mod._refresh_job, "state", "running")

    result = main_mod._get_or_generate_weekly_plan(db)
    assert result["days"]
    assert "sleep" in order, "wait branch was never entered"
    assert order.index("sleep") < order.index("pipeline")


def test_generation_immediate_when_no_refresh(db, monkeypatch):
    order = []
    _wire_generation(monkeypatch, order)
    monkeypatch.setattr(main_mod.time, "sleep",
                        lambda sec: order.append("sleep"))
    assert main_mod._refresh_job["state"] != "running"
    main_mod._get_or_generate_weekly_plan(db)
    assert "sleep" not in order
    assert "pipeline" in order


def test_generation_proceeds_when_refresh_never_settles(db, monkeypatch):
    """The 90s cap: a wedged refresh job must delay generation, not block it."""
    order = []
    _wire_generation(monkeypatch, order)
    monkeypatch.setattr(main_mod.time, "sleep",
                        lambda sec: order.append("sleep"))
    monkeypatch.setitem(main_mod._refresh_job, "state", "running")

    result = main_mod._get_or_generate_weekly_plan(db)
    assert result["days"]
    assert order.count("sleep") == 45  # 90s cap at 2s ticks
    assert order[-1] == "pipeline"
    assert main_mod._refresh_job["state"] == "running"  # untouched


# --- Fix 4a: honest data age in the coach's context -----------------------

def test_stale_snapshot_is_labeled(db):
    from backend.agents.data_agent import DataAgent
    db.add(RecoverySnapshot(date=get_local_today() - timedelta(days=1),
                            athlete_id=1, resting_hr=51, hrv_ms=86.0))
    db.commit()
    text = DataAgent(db).summarize()
    assert "not yet synced today" in text
    assert "Today RHR" not in text


def test_fresh_snapshot_says_today(db):
    from backend.agents.data_agent import DataAgent
    db.add(RecoverySnapshot(date=get_local_today(), athlete_id=1,
                            resting_hr=51, hrv_ms=86.0))
    db.commit()
    text = DataAgent(db).summarize()
    assert "Today RHR: 51 bpm" in text
    assert "not yet synced" not in text


# --- Fix 4b: chat knows the phase -----------------------------------------

def test_chat_context_carries_real_phase(db):
    text = main_mod._build_chat_context(db, "summary", "")
    line = next(l for l in text.splitlines() if l.startswith("TRAINING PHASE:"))
    assert "Unknown" not in line


def test_interval_reps_never_reconciled():
    plan = {"days": {"Wednesday": {"workouts": [
        _run_workout("VO2 Intervals", "24:00", "5 x 1 km at 5K pace", "45 min")]}}}
    pm = {"bands": {"interval": {"lo": 232.0, "hi": 245.0, "label": "3:52-4:05/km"},
                    **PACE_MODEL["bands"]}}
    plan, corrections = enforce_paces(plan, pm)
    main = next(s for s in plan["days"]["Wednesday"]["workouts"][0]["steps"]
                if s["type"] == "main")
    assert main["duration"] == "24:00"  # untouched — "1 km" is one rep
    assert not any("step_from" in c for c in corrections)


def test_inside_slack_stays_quiet():
    """41.0 km (0.8 under target) + 82-min long run (4 under): both inside
    their slacks — pins that the slack constants exist at all."""
    plan = _week_plan([8.0, 8.0, 6.0, 7.0, 12.0], long_run_min=82)
    report = volume_gate.audit_plan(plan, _gate_ctx(), days=None,
                                    availability=AVAIL, active_injuries=[])
    kinds = {v["kind"] for v in report.soft}
    assert "run_km_under_target" not in kinds
    assert "long_run_short" not in kinds


def test_progressive_run_never_reconciled():
    plan = {"days": {"Thursday": {"workouts": [
        _run_workout("Progressive Run", "50:00",
                     "10 km building to marathon pace", "70 min")]}}}
    pm = {"bands": {"marathon": {"lo": 285.0, "hi": 295.0,
                                 "label": "4:45-4:55/km"},
                    **PACE_MODEL["bands"]}}
    plan, corrections = enforce_paces(plan, pm)
    main = next(s for s in plan["days"]["Thursday"]["workouts"][0]["steps"]
                if s["type"] == "main")
    assert main["duration"] == "50:00"  # progressive spans bands — never touched
    assert not any("step_from" in c for c in corrections)


def test_segment_mention_never_shrinks_long_run():
    """Review blocker: 'last 3 km at marathon pace' in a 90-min long run
    must not reconcile against the 3 km segment."""
    w = {"sport": "running", "title": "Long Run", "total_time": "90 min",
         "distance_km": 16.0, "steps": [
             {"type": "main", "duration": "90:00", "zone": 2,
              "description": "steady, last 3 km at marathon pace"}]}
    plan = {"days": {"Sunday": {"workouts": [w]}}}
    pm = {"bands": {"long_run": {"lo": 320.0, "hi": 350.0,
                                 "label": "5:20-5:50/km"},
                    **PACE_MODEL["bands"]}}
    plan, corrections = enforce_paces(plan, pm)
    main = plan["days"]["Sunday"]["workouts"][0]["steps"][0]
    assert main["duration"] == "90:00"
    assert plan["days"]["Sunday"]["workouts"][0]["total_time"] == "90 min"
