"""Shoulder guard (shoulder_guard.py): the gym with an injured shoulder.

Rules locked in here:
- No active shoulder injury -> strength sessions are the LLM's, untouched.
- With one, every strength session being written is the Python session:
  no press, a shoulder routine, the same length (30-minute floor). Runs and
  days outside the window are untouched, and a second pass changes nothing.
- Logging the injury fixes the stored week at once, today forward, without
  the LLM: swims stripped by `affected_sports`, gym rewritten.
"""
import copy
import json
from types import SimpleNamespace

from backend.models.database import WeeklyPlan
from backend.services.plan_normalizer import normalize_plan
from backend.services.shoulder_guard import MAIN, ROUTINE, enforce_shoulder
from backend.tests.test_profile_reenforce import WEEK_START, client, test_db_session  # noqa: F401

SHOULDER = SimpleNamespace(body_part="Right shoulder", severity=4, affected_sports="swim")
CALF = SimpleNamespace(body_part="Right calf", severity=4, affected_sports="run")


def _gym(minutes="40 min"):
    return {"sport": "strength", "title": "Maintenance", "total_time": minutes,
            "hr_target": "150-165 bpm", "muscle_groups": ["shoulders", "back", "legs"],
            "steps": [{"type": "main", "duration": "30:00", "zone": 3,
                       "description": "2 sets: overhead press, rows, lunges"}]}


def _week():
    run = {"sport": "running", "title": "Easy Run", "total_time": "45 min",
           "steps": [{"type": "main", "duration": "45:00", "zone": 2, "description": "Easy"}]}
    swim = {"sport": "swimming", "title": "Technique Session", "total_time": "30 min", "steps": []}
    return normalize_plan({"days": {
        "Monday": {"summary": "", "workouts": [copy.deepcopy(run)]},
        "Tuesday": {"summary": "", "workouts": [copy.deepcopy(run), _gym()]},
        "Wednesday": {"summary": "", "workouts": [copy.deepcopy(run)]},
        "Thursday": {"summary": "", "workouts": [copy.deepcopy(run), _gym("20 min")]},
        "Friday": {"summary": "", "workouts": [{"sport": "rest", "title": "Rest", "steps": []}]},
        "Saturday": {"summary": "", "workouts": [swim, _gym("30 min")]},
        "Sunday": {"summary": "", "workouts": [copy.deepcopy(run)]},
    }})


def test_no_shoulder_injury_leaves_the_gym_alone():
    for injuries in ([], None, [CALF]):
        plan = _week()
        before = copy.deepcopy(plan)
        assert enforce_shoulder(plan, injuries) == (before, [])


def test_a_shoulder_injury_rewrites_every_gym_session_in_the_window():
    plan = _week()
    before = copy.deepcopy(plan)
    plan, changes = enforce_shoulder(plan, [CALF, SHOULDER], days=["Wednesday", "Thursday", "Saturday"])
    assert [c["day"] for c in changes] == ["Thursday", "Saturday"]
    assert plan["days"]["Tuesday"] == before["days"]["Tuesday"]      # outside the window
    assert plan["days"]["Wednesday"] == before["days"]["Wednesday"]  # a run

    short = plan["days"]["Thursday"]["workouts"][1]
    assert short["total_time"] == "30 min"
    assert [s["duration"] for s in short["steps"]] == ["05:00", "10:00", "10:00", "05:00"]
    assert [s["description"] for s in short["steps"][1:3]] == [MAIN, ROUTINE]
    assert "press" not in json.dumps(short["steps"]).replace("No press", "").lower()
    assert short["muscle_groups"] == ["legs"] and "Right shoulder" in short["enforced_note"]

    again = copy.deepcopy(plan)
    assert enforce_shoulder(plan, [SHOULDER], days=["Thursday", "Saturday"]) == (again, [])


def test_hombro_counts_as_a_shoulder():
    plan, changes = enforce_shoulder(_week(), [SimpleNamespace(body_part="Hombro derecho")])
    assert [c["day"] for c in changes] == ["Tuesday", "Thursday", "Saturday"]
    assert plan["days"]["Tuesday"]["workouts"][1]["total_time"] == "40 min"


def test_logging_the_injury_fixes_the_stored_week(client, test_db_session):  # noqa: F811
    seeded = _week()
    test_db_session.add(WeeklyPlan(week_start=WEEK_START, athlete_id=1,
                                   plan_json=json.loads(json.dumps(seeded))))
    test_db_session.commit()

    resp = client.post("/athlete/injuries", json={
        "body_part": "Right shoulder", "severity": 4, "affected_sports": "swim"})
    assert resp.status_code == 200
    assert resp.json()["plan_enforcement"]["stripped"] == 1   # Saturday's swim

    stored = test_db_session.query(WeeklyPlan).order_by(WeeklyPlan.id.desc()).first().plan_json
    assert stored["days"]["Tuesday"] == seeded["days"]["Tuesday"]   # Tuesday is the past (frozen Wednesday)
    saturday = stored["days"]["Saturday"]["workouts"]
    assert [w["sport"] for w in saturday] == ["strength"]
    assert saturday[0]["steps"][2]["description"] == ROUTINE
    assert stored["days"]["Thursday"]["workouts"][1]["steps"][1]["description"] == MAIN
    assert stored["_revisions"][-1]["reason"] == "Injury logged: Right shoulder"


def test_an_injury_that_changes_nothing_does_not_churn_the_row(client, test_db_session):  # noqa: F811
    seeded = _week()
    test_db_session.add(WeeklyPlan(week_start=WEEK_START, athlete_id=1,
                                   plan_json=json.loads(json.dumps(seeded))))
    test_db_session.commit()
    resp = client.post("/athlete/injuries", json={"body_part": "Left toe", "severity": 2})
    assert resp.status_code == 200
    stored = test_db_session.query(WeeklyPlan).order_by(WeeklyPlan.id.desc()).first().plan_json
    assert not stored.get("_revisions")
