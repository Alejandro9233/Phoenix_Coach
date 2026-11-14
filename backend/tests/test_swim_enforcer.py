"""On-top swims: a marathon goal gets two easy 30-minute technique swims a
week, none in race week, and the week keeps a rest day (Alex, 2026-11-14).
The enforcer holds the LLM to that; the engine budgets it and makes room in
the hours window without touching the run plan."""
from backend.services.periodization_engine import DISTANCE_PROFILES, PeriodizationEngine
from backend.services.plan_normalizer import VALID_DAYS
from backend.services.swim_enforcer import enforce_swims, swim_rule

RULE = {"max": 2, "minutes": 30}


def _swim(title="Technique Session", minutes=30, zone=2):
    return {"sport": "swimming", "title": title, "total_time": f"{minutes} min",
            "distance_km": 1.0,
            "steps": [{"type": "main", "duration": f"{minutes}:00", "zone": zone}]}


RUN = {"sport": "running", "title": "Easy Run", "total_time": "45 min", "steps": []}
REST = {"sport": "rest", "title": "Rest", "steps": [], "total_time": "00:00"}


def _week(**by_day):
    return {"days": {d: {"summary": "s", "workouts": list(by_day.get(d, [REST]))}
                     for d in VALID_DAYS}}


def _sports(plan, day):
    return [w["sport"] for w in plan["days"][day]["workouts"]]


def test_no_rule_leaves_the_week_alone():
    plan = _week(Monday=[_swim("CSS Threshold", 60, 4)])
    plan, cuts = enforce_swims(plan, None)
    assert cuts == [] and plan["days"]["Monday"]["workouts"][0]["title"] == "CSS Threshold"


def test_a_third_swim_is_removed_and_the_earliest_two_stay():
    plan = _week(Monday=[_swim()], Wednesday=[RUN, _swim()], Friday=[_swim()])
    plan, cuts = enforce_swims(plan, RULE)
    assert [c["day"] for c in cuts] == ["Friday"]
    assert _sports(plan, "Friday") == ["rest"]
    assert _sports(plan, "Wednesday") == ["running", "swimming"]


def test_a_long_or_hard_swim_becomes_the_easy_session():
    plan = _week(Monday=[_swim("CSS Threshold", 60, 4)], Tuesday=[_swim("Endurance Swim", 30, 4)])
    plan, cuts = enforce_swims(plan, RULE)
    assert cuts == []
    for day in ("Monday", "Tuesday"):
        w = plan["days"][day]["workouts"][0]
        assert w["title"] == "Technique Session" and w["total_time"] == "30 min"
        assert max(s["zone"] for s in w["steps"]) <= 2
    assert plan["days"]["Monday"]["workouts"][0]["distance_km"] == 0.5


def test_race_week_has_no_swim():
    plan = _week(Monday=[_swim()], Tuesday=[RUN, _swim()])
    plan, cuts = enforce_swims(plan, {"max": 0, "minutes": 30})
    assert len(cuts) == 2 and "race week" in cuts[0]["reason"]
    assert _sports(plan, "Monday") == ["rest"] and _sports(plan, "Tuesday") == ["running"]


def test_a_swim_does_not_take_the_last_rest_day():
    full = {d: [RUN] for d in VALID_DAYS}
    full["Saturday"] = [_swim()]
    plan, cuts = enforce_swims(_week(**full), RULE)
    assert [c["day"] for c in cuts] == ["Saturday"] and "rest day" in cuts[0]["reason"]
    # With another day free, the swim stays.
    full["Monday"] = [REST]
    plan, cuts = enforce_swims(_week(**full), RULE)
    assert cuts == [] and _sports(plan, "Saturday") == ["swimming"]


def test_days_outside_the_write_count_but_are_not_touched():
    plan = _week(Monday=[_swim("CSS Threshold", 60, 4)], Tuesday=[_swim()], Friday=[_swim()])
    plan, cuts = enforce_swims(plan, RULE, days=["Thursday", "Friday", "Saturday", "Sunday"])
    assert [c["day"] for c in cuts] == ["Friday"]
    assert plan["days"]["Monday"]["workouts"][0]["title"] == "CSS Threshold"


def test_the_marathon_budgets_two_short_swims_in_every_phase():
    for phase in DISTANCE_PROFILES["Marathon"]["phases"]:
        swim = phase["sport_sessions"]["swimming"]
        assert (swim["sessions"], swim["hours_each"], swim["on_top"]) == (2, 0.5, True)
    rule = swim_rule({"volume_references": {"sport_sessions": {
        "swimming": DISTANCE_PROFILES["Marathon"]["phases"][0]["sport_sessions"]["swimming"]}}})
    assert rule == RULE
    assert swim_rule({"volume_references": {"sport_sessions": {
        "swimming": {"sessions": 2, "volume_note": "triathlon"}}}}) is None


def test_on_top_swims_widen_the_top_of_the_hours_window_only():
    phase = DISTANCE_PROFILES["Marathon"]["phases"][1]
    sessions = {k: dict(v) for k, v in phase["sport_sessions"].items()}
    # No pool: the window is what it was before swims were budgeted.
    without = {k: v for k, v in sessions.items() if k != "swimming"}
    assert PeriodizationEngine._hours_range_for(phase, without, False) == phase["hours_range"]
    refs = {"phase_hours_range": "7-9", "sport_sessions": sessions}
    PeriodizationEngine._add_on_top_sessions(refs, race_week=False)
    assert refs["phase_hours_range"] == "7-10"
    refs = {"phase_hours_range": "3-5", "sport_sessions": {k: dict(v) for k, v in sessions.items()}}
    PeriodizationEngine._add_on_top_sessions(refs, race_week=True)
    assert refs["phase_hours_range"] == "3-5"
    assert refs["sport_sessions"]["swimming"]["sessions"] == 0
