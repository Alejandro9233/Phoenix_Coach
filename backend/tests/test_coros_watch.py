"""Plan workout → COROS course, and the watch calendar back into items.

The mapper is the only piece that decides what the watch tells the athlete to
do, so it refuses anything it can't express instead of guessing. Contract
checks mirror the createScheduledWorkout schema sampled 2026-10-07
(docs/COROS_MCP.md, "Write path — the contract").
"""
import os
from datetime import date

import pytest

from backend.services import coros_watch as cw

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")


def _run(**kw):
    w = {"sport": "running", "title": "Easy run", "total_time": "45 min", "hr_target": "2",
         "steps": [{"type": "warmup", "duration": "10:00", "zone": 1, "description": "jog"},
                   {"type": "main", "duration": "30:00", "zone": 2, "description": "steady"},
                   {"type": "cooldown", "duration": "05:00", "zone": 1, "description": "walk"}],
         "distance_km": 7.2, "pace_target": "6:20–6:40/km"}
    w.update(kw)
    return w


def assert_course_contract(course):
    assert set(course) == {"courseName", "courseDescription", "sportType", "sections"}
    assert 1 <= len(course["courseName"]) <= 100 and course["courseDescription"]
    assert course["sportType"] in (1, 2, 5) and course["sections"]
    for s in course["sections"]:
        assert s["sectionType"] in (1, 2, 3, 4) and s["targetType"] in (1, 2, 3, 4)
        if s["targetType"] == 4:
            assert "targetValue" not in s
        else:
            assert isinstance(s["targetValue"], int) and s["targetValue"] > 0
        assert s["intensityType"] == 1
        formats = [k for k in ("sectionIntensity", "intensityValueStart", "intensityPercentStart") if k in s]
        assert len(formats) == 1, f"exactly one intensity format, got {formats}"
        if "sectionIntensity" in s:
            assert 1 <= s["sectionIntensity"] <= 6
        else:
            assert 30 <= s["intensityValueStart"] < s["intensityValueEnd"] <= 240


def test_workout_maps_steps_one_to_one_with_hr_zones():
    course = cw.workout_to_course(_run(), slot=1)       # hr_target "2" is a zone, not a range
    assert_course_contract(course)
    assert course["courseName"] == "Phoenix 1 · Easy run" and course["sportType"] == 1
    assert course["sections"] == [
        {"sectionType": 1, "targetType": 2, "targetValue": 600, "intensityType": 1, "sectionIntensity": 1},
        {"sectionType": 2, "targetType": 2, "targetValue": 1800, "intensityType": 1, "sectionIntensity": 2},
        {"sectionType": 4, "targetType": 2, "targetValue": 300, "intensityType": 1, "sectionIntensity": 1},
    ]
    d = course["courseDescription"]
    assert "Pace target 6:20–6:40/km." in d and "About 7.2 km." in d and "HR target 2." in d
    assert "Warmup 10:00 Z1 · Main 30:00 Z2 · Cooldown 05:00 Z1." in d
    assert d.endswith(cw.PHOENIX_FOOTER)


def test_main_sections_carry_the_workouts_bpm_range_when_it_has_one():
    # What live plans look like: every step zone 1 (prompt example), hr_target the real Z2 band.
    w = _run(hr_target="142-159 bpm", steps=[{"type": "warmup", "duration": "08:00", "zone": 1},
                                             {"type": "main", "duration": "20:00", "zone": 1},
                                             {"type": "cooldown", "duration": "07:00", "zone": 1}])
    course = cw.workout_to_course(w)
    assert_course_contract(course)
    main = course["sections"][1]
    assert main == {"sectionType": 2, "targetType": 2, "targetValue": 1200, "intensityType": 1,
                    "intensityValueStart": 142, "intensityValueEnd": 159}
    assert course["sections"][0]["sectionIntensity"] == 1 and course["sections"][2]["sectionIntensity"] == 1
    assert "HR target 142-159 bpm." in course["courseDescription"]


@pytest.mark.parametrize("target, expected", [
    ("142-159 bpm", (142, 159)), ("142–159", (142, 159)), ("2", None), ("--", None),
    ("159-142 bpm", None), ("20-300 bpm", None), (None, None),
])
def test_hr_target_range_parsing(target, expected):
    assert cw.hr_target_range({"hr_target": target}) == expected


def test_cycling_maps_and_recovery_steps_are_type_3():
    w = _run(sport="cycling", title="Bike Z2",
             steps=[{"type": "main", "duration": "1:00:00", "zone": 2}, {"type": "recovery", "duration": "03:00", "zone": 1}])
    course = cw.workout_to_course(w, slot=2)
    assert course["sportType"] == 2 and course["courseName"] == "Phoenix 2 · Bike Z2"
    assert [s["sectionType"] for s in course["sections"]] == [2, 3]
    assert course["sections"][0]["targetValue"] == 3600


@pytest.mark.parametrize("bad, msg", [
    (dict(sport="strength"), "running/cycling only"),
    (dict(sport="rest", steps=[]), "running/cycling only"),
    (dict(steps=[]), "no steps"),
    (dict(steps=[{"type": "main", "duration": "30:00", "zone": None}]), "no integer zone"),
    (dict(steps=[{"type": "main", "duration": "30:00", "zone": 7}]), "outside COROS zones"),
    (dict(steps=[{"type": "main", "duration": "45 min", "zone": 2}]), "M:SS"),
    (dict(steps=[{"type": "main", "duration": "00:00", "zone": 2}]), "positive"),
    (dict(steps=[{"type": "sprint", "duration": "01:00", "zone": 5}]), "unknown step type"),
])
def test_mapper_refuses_instead_of_guessing(bad, msg):
    with pytest.raises(cw.CourseMappingError, match=msg):
        cw.workout_to_course(_run(**bad))


def test_name_is_capped_at_100_and_keeps_the_marker():
    course = cw.workout_to_course(_run(title="x" * 300))
    assert len(course["courseName"]) == 100 and course["courseName"].startswith("Phoenix 1 · ")
    assert cw.NAME_RE.match(course["courseName"]).group(1) == "1"


def test_cancelled_course_is_not_a_prescription():
    course = cw.cancelled_course(1, "Active injury: left calf rules out running")
    assert_course_contract(course)
    assert course["courseName"].startswith("Phoenix 1 · CANCELLED — Active injury")
    assert course["sections"] == [{"sectionType": 2, "targetType": 4, "intensityType": 1, "sectionIntensity": 1}]
    assert "Delete it in the COROS app" in course["courseDescription"]
    assert cw.is_cancelled(course) and not cw.is_cancelled(cw.workout_to_course(_run()))


def test_course_hash_is_stable_and_content_sensitive():
    a, b = cw.workout_to_course(_run()), cw.workout_to_course(_run())
    assert cw.course_hash(a) == cw.course_hash(b) and len(cw.course_hash(a)) == 16
    assert cw.course_hash(cw.workout_to_course(_run(title="Easy run 2"))) != cw.course_hash(a)


def test_plan_day_courses_slots_pushable_workouts_only():
    plan = {"days": {
        "Monday": {"workouts": [_run(title="Mon run")]},
        "Tuesday": {"workouts": [{"sport": "strength", "title": "Gym", "steps": []}]},
        "Wednesday": {"workouts": [{"sport": "rest", "title": "Rest", "steps": [],
                                    "enforced_reason": "Active injury: calf rules out running"}]},
        "Thursday": {"workouts": [{"sport": "strength", "title": "Core", "steps": []},
                                  _run(title="Thu easy"), _run(title="Thu strides", steps=[{"type": "main", "duration": "20:00", "zone": 3}])]},
        "Friday": {"workouts": [_run(title="Broken", steps=[{"type": "main", "duration": "45 min", "zone": 2}])]},
    }}
    week = cw.plan_day_courses(plan, date(2026, 3, 9))   # a Monday
    assert set(week) == {date(2026, 3, 9) + __import__("datetime").timedelta(days=i) for i in range(7)}
    mon = week[date(2026, 3, 9)]
    assert [(s, c["courseName"]) for s, c in mon.courses] == [(1, "Phoenix 1 · Mon run")] and not mon.errors
    assert week[date(2026, 3, 10)].courses == []                       # strength is not pushed
    wed = week[date(2026, 3, 11)]
    assert wed.courses == [] and wed.enforced_reason == "Active injury: calf rules out running"
    thu = week[date(2026, 3, 12)]
    assert [(s, c["courseName"]) for s, c in thu.courses] == [(1, "Phoenix 1 · Thu easy"), (2, "Phoenix 2 · Thu strides")]
    fri = week[date(2026, 3, 13)]
    assert fri.courses == [] and len(fri.errors) == 1 and "M:SS" in fri.errors[0]
    assert week[date(2026, 3, 15)].courses == [] and week[date(2026, 3, 15)].enforced_reason is None


def test_parse_training_schedule_fixture():
    with open(os.path.join(FIXTURES, "coros_mcp_training_schedule.txt")) as f:
        sched = cw.parse_training_schedule(f.read())
    assert set(sched) == {date(2026, 3, 9), date(2026, 3, 10)}
    mon = sched[date(2026, 3, 9)]
    assert [(i["name"], i["idInPlan"], i["phoenix_slot"], i["completed"]) for i in mon] == [
        ("Phoenix 1 · Easy run", "101", 1, False), ("calf’s and hip", "102", None, False)]
    assert cw.phoenix_items(mon) == {1: mon[0]}
    tue = sched[date(2026, 3, 10)]
    assert tue == [{"name": "Easy run", "idInPlan": "82", "source": "standalone", "completed": True, "phoenix_slot": None}]


def test_parse_training_schedule_empty_or_garbage_is_empty():
    assert cw.parse_training_schedule("No workouts scheduled for 2026-03-09 to 2026-03-15.") == {}
    assert cw.parse_training_schedule("") == {}
