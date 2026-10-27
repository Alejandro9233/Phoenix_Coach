"""A week planned under its run-km target is lifted to it, in Python.

2026-10-27: the replan of Tue-Sun came back at 45.8 km against a 50 km
target (Tue easy 40 min, Wed gym, Thu cruise 44 min, Fri easy 69 min, Sat
gym, Sun long run 128 min) and nothing lifted it. Alex: "there is a
difference between 45k and 50k".

Rules locked in here:
- Easy runs lengthen first (shortest first, to 75 min max); then an Easy Run
  is added on an open day: rest before strength, the long run's eve last,
  the long run's day never, only run_days.
- Minutes convert at the easy band (the athlete's CDMX pace).
- No fill in the goal race week, with running injury-blocked, or when the
  week already reaches its target.
"""
import copy
from types import SimpleNamespace

import pytest

from backend.services.pace_enforcer import enforce_distances
from backend.services.pace_model import apply_training_pace, compute_pace_model
from backend.services.plan_normalizer import map_sport
from backend.services.volume_fill import fill_run_km
from backend.services.volume_gate import parse_minutes, workout_km

PM = apply_training_pace(compute_pace_model(4.45, "3:20:00", "Marathon"), 416.4)
WINDOW = ["Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
AVAIL = {"run_days": "mon,tue,wed,thu,fri,sat,sun", "strength_days": "mon,tue,wed,thu,fri,sat"}


def _run(title, minutes):
    return {"sport": "running", "title": title, "total_time": f"{minutes} min",
            "steps": [{"type": "warmup", "duration": "5:00", "zone": 1, "description": "jog"},
                      {"type": "main", "duration": f"{minutes - 10}:00", "zone": 2, "description": "steady"},
                      {"type": "cooldown", "duration": "5:00", "zone": 1, "description": "jog"}],
            "hr_target": "140-155 bpm", "muscle_groups": []}


def _gym():
    return {"sport": "strength", "title": "Maintenance", "total_time": "40 min",
            "steps": [], "muscle_groups": ["legs"]}


def _week():
    rest = {"summary": "Rest", "workouts": [{"sport": "rest", "title": "Rest", "steps": []}]}
    plan = {"days": {
        "Monday": copy.deepcopy(rest),
        "Tuesday": {"workouts": [_run("Easy Run", 40)]},
        "Wednesday": {"workouts": [_gym()]},
        "Thursday": {"workouts": [_run("Cruise Intervals", 44)]},
        "Friday": {"workouts": [_run("Easy Run", 69)]},
        "Saturday": {"workouts": [_gym()]},
        "Sunday": {"workouts": [_run("Long Run", 128)]},
    }}
    plan, _ = enforce_distances(plan, PM)
    return plan


def _ctx(target=50.0, race_week=False):
    ctx = {"volume_targets": {"run_km_target": target}, "pace_model": PM,
           "long_run_day": "Sunday"}
    if race_week:
        ctx.update(race_week=True, race_day_name="Sunday")
    return ctx


def _run_km(plan, days=WINDOW):
    return sum(workout_km(w)[0] for d in days for w in plan["days"][d]["workouts"]
               if map_sport(w.get("sport") or "") == "running")


def test_this_weeks_replan_reaches_50km():
    plan = _week()
    assert _run_km(plan) < 46
    plan, fills = fill_run_km(plan, _ctx(), days=WINDOW, availability=AVAIL)
    plan, _ = enforce_distances(plan, PM, days=WINDOW)
    assert 50.0 - 1.0 <= _run_km(plan) <= 50.0 + 1.0
    # ~5 km short: the shortest easy run grows; no extra session needed.
    assert [(f["day"], f["action"]) for f in fills] == [("Tuesday", "lengthened")]
    tue = plan["days"]["Tuesday"]["workouts"][0]
    assert tue["volume_fill"].startswith("Lengthened")
    # The main block grows; warm-up and cool-down stay at 5 min each.
    assert parse_minutes(tue["steps"][1]["duration"]) == parse_minutes(tue["total_time"]) - 10


def test_a_bigger_gap_adds_a_run_on_a_gym_day_not_the_long_runs_eve():
    plan, fills = fill_run_km(_week(), _ctx(target=58.0), days=WINDOW, availability=AVAIL)
    plan, _ = enforce_distances(plan, PM, days=WINDOW)
    assert 58.0 - 3.0 <= _run_km(plan) <= 58.0 + 1.0
    assert [(f["day"], f["action"]) for f in fills] == [
        ("Tuesday", "lengthened"), ("Friday", "lengthened"), ("Wednesday", "added")]
    assert plan["days"]["Friday"]["workouts"][0]["total_time"] == "1:15:00"
    wed = plan["days"]["Wednesday"]["workouts"]
    assert [w["sport"] for w in wed] == ["running", "strength"]
    assert wed[0]["hr_target"] == "140-155 bpm"             # copied from an easy run


def test_quality_and_the_long_run_are_never_lengthened():
    plan, _ = fill_run_km(_week(), _ctx(), days=WINDOW, availability=AVAIL)
    assert plan["days"]["Thursday"]["workouts"][0]["total_time"] == "44 min"
    assert plan["days"]["Sunday"]["workouts"][0]["total_time"] == "128 min"


def test_minutes_convert_at_the_cdmx_easy_pace():
    plan, _ = fill_run_km(_week(), _ctx(target=58.0), days=WINDOW, availability=AVAIL)
    plan, _ = enforce_distances(plan, PM, days=WINDOW)
    easy_mid = (PM["bands"]["easy"]["lo"] + PM["bands"]["easy"]["hi"]) / 2
    assert plan["days"]["Friday"]["workouts"][0]["distance_km"] == round(75 * 60 / easy_mid, 1)


@pytest.mark.parametrize("ctx, kw", [
    (_ctx(target=40.0), {}),                                     # already there
    (_ctx(race_week=True), {}),                                   # the race is the week
    (_ctx(), {"active_injuries": [SimpleNamespace(affected_sports="running", body_part="calf")]}),
])
def test_no_fill(ctx, kw):
    _plan, fills = fill_run_km(_week(), ctx, days=WINDOW, availability=AVAIL, **kw)
    assert fills == []


def test_runs_go_only_on_run_days():
    avail = {"run_days": "tue,thu,fri,sun"}
    plan, fills = fill_run_km(_week(), _ctx(), days=WINDOW, availability=avail)
    assert all(f["action"] == "lengthened" for f in fills)
