"""
Hand-set weeks — a week written in Python, not by the LLM.

The 2026-11-02 week holds the Nov 8 half-marathon trial that decides the
full vs the 21K on Dec 13 (pass <= 1:37:30 at altitude). The engine only
knew it as "42 km this week" (RUN_KM_PLAN), so the generated plan put a
150-min easy long run on Sunday, a 60-min tempo on Saturday and heavy legs
on Friday — the trial wasn't in it at all. Alex, 2026-11-03: "confío más
en ti que en la IA de la app." The week below is the one agreed in chat.

A hand-set week skips generation and the volume gate (the gate audits LLM
output; it would trim a 48 km week to the 42 km cap) but still runs the
ungated write pipeline: constraint_enforcer, the long-run day, the zone,
pace, distance and fuel stamps. Trial-paced workouts carry their own
pace_target — pace_enforcer classifies them as "trial", a band the pace
model doesn't have, so the stamp leaves them alone.

The 2026-11-09 week is the 55 km marathon week around the 27 km long run.
The trial stopped at 8.2 km and Alex chose not to retest (2026-11-09): "I
need to get ready for the marathon."

Mid-week replans, adapt-today and triage still go through the LLM for the
days they rewrite.
"""
import copy
from datetime import date

EASY_HR = "140-155 bpm"
TRIAL_PACE = "4:35-4:40/km"


def _rest(summary, rationale, coach_note):
    return {
        "summary": summary,
        "workouts": [{"sport": "rest", "title": "Rest", "steps": [],
                      "total_time": "00:00", "hr_target": "--", "muscle_groups": []}],
        "rationale": rationale,
        "coach_note": coach_note,
    }


def _strength(summary_groups, main, minutes=40):
    return {"sport": "strength", "title": "Maintenance",
            "steps": [
                {"type": "warmup", "duration": "05:00", "zone": 1,
                 "description": "Band activation"},
                {"type": "main", "duration": f"{minutes - 10}:00", "zone": 2,
                 "description": main},
                {"type": "cooldown", "duration": "05:00", "zone": 1,
                 "description": "Stretch"},
            ],
            "total_time": f"{minutes} min", "hr_target": "--",
            "muscle_groups": summary_groups}


def _easy(minutes, strides=False):
    steps = [{"type": "main", "duration": f"{minutes - (4 if strides else 0)}:00",
              "zone": 2, "description": "Easy, conversational"}]
    if strides:
        steps.append({"type": "main", "duration": "04:00", "zone": 3,
                      "description": "4 x 20 s strides, 40 s easy jog between"})
    return steps


HAND_SET_WEEKS = {
    date(2026, 11, 2): {
        "week_summary": {
            "focus": "Half-marathon trial week: fresh legs for Sunday",
            "rationale": (
                "Sunday's 21.1 km trial decides the full vs the 21K on Dec 13: "
                "pass is 1:37:30 or faster (4:37/km) at altitude. Last week was "
                "50.7 km with a 20.1 km long run, so the job now is to arrive fresh: "
                "one short session at trial pace on Wednesday, easy running around "
                "it, no heavy legs after Tuesday."
            ),
        },
        "days": {
            "Monday": _rest(
                "Rest — 20k steps walking",
                "No session; the walk is light load and needs no make-up.",
                "",
            ),
            "Tuesday": {
                "summary": "Easy 35 min + strides, light strength",
                "workouts": [
                    {"sport": "running", "title": "Easy Run",
                     "steps": _easy(35, strides=True), "total_time": "35 min",
                     "hr_target": EASY_HR, "muscle_groups": [], "distance_km": 5.3},
                    {"sport": "strength", "title": "Maintenance",
                     "steps": [
                         {"type": "warmup", "duration": "05:00", "zone": 1,
                          "description": "Band activation"},
                         {"type": "main", "duration": "30:00", "zone": 2,
                          "description": ("Upper: bench, overhead press, rows, pull-ups, 2 sets. "
                                          "Light legs: goblet squat 2 x 8, calf raises 2 x 12. "
                                          "No deadlifts or lunges, nothing to failure.")},
                         {"type": "cooldown", "duration": "05:00", "zone": 1,
                          "description": "Stretch"},
                     ],
                     "total_time": "40 min", "hr_target": "--",
                     "muscle_groups": ["chest", "shoulders", "back", "arms", "legs"]},
                ],
                "rationale": "Last leg work before the trial, light enough to leave no soreness by Sunday.",
                "coach_note": "Moderate weights, stop two reps short of failure.",
            },
            "Wednesday": {
                "summary": "3 x 2 km at trial pace",
                "workouts": [
                    {"sport": "running", "title": "Trial Pace Intervals",
                     "steps": [
                         {"type": "warmup", "duration": "15:00", "zone": 1,
                          "description": "Easy jog"},
                         {"type": "main", "duration": "09:15", "zone": 4,
                          "description": "2 km at 4:35-4:40/km"},
                         {"type": "recovery", "duration": "02:00", "zone": 1,
                          "description": "Easy jog"},
                         {"type": "main", "duration": "09:15", "zone": 4,
                          "description": "2 km at 4:35-4:40/km"},
                         {"type": "recovery", "duration": "02:00", "zone": 1,
                          "description": "Easy jog"},
                         {"type": "main", "duration": "09:15", "zone": 4,
                          "description": "2 km at 4:35-4:40/km"},
                         {"type": "cooldown", "duration": "10:00", "zone": 1,
                          "description": "Easy jog"},
                     ],
                     "total_time": "57 min", "hr_target": "160-172 bpm",
                     "muscle_groups": [], "distance_km": 10.3,
                     "pace_target": TRIAL_PACE},
                ],
                "rationale": "One rehearsal of Sunday's pace at altitude, four days out: enough to calibrate, too short to tire.",
                "coach_note": "Hit 4:37, not faster. Note your HR on the third rep; it's your best guide for Sunday.",
            },
            "Thursday": _rest(
                "Rest",
                "Wednesday's treadmill broke one rep in, so the trial-pace work moves to Friday as pick-ups.",
                "Nothing, or an easy 20-30 min swim. Your calf and hip routine is fine today.",
            ),
            # Wednesday's reps didn't happen (the treadmill broke, 2026-11-05):
            # Friday carries a short taste of trial pace instead.
            "Friday": {
                "summary": "Easy 30 min with 3 x 1 min at trial pace",
                "workouts": [
                    {"sport": "running", "title": "Trial Pace Pick-ups",
                     "steps": [
                         {"type": "warmup", "duration": "12:00", "zone": 1,
                          "description": "Easy jog"},
                         {"type": "main", "duration": "01:00", "zone": 3,
                          "description": "Pick-up at 4:37/km (13.0 km/h on a treadmill)"},
                         {"type": "recovery", "duration": "02:00", "zone": 1,
                          "description": "Easy jog"},
                         {"type": "main", "duration": "01:00", "zone": 3,
                          "description": "Pick-up at 4:37/km (13.0 km/h on a treadmill)"},
                         {"type": "recovery", "duration": "02:00", "zone": 1,
                          "description": "Easy jog"},
                         {"type": "main", "duration": "01:00", "zone": 3,
                          "description": "Pick-up at 4:37/km (13.0 km/h on a treadmill)"},
                         {"type": "cooldown", "duration": "11:00", "zone": 1,
                          "description": "Easy jog"},
                     ],
                     "total_time": "30 min", "hr_target": EASY_HR,
                     "muscle_groups": [], "distance_km": 4.7,
                     "pace_target": "6:19-7:00/km easy · pick-ups 4:37/km"},
                ],
                "rationale": "Wednesday's reps were lost to the treadmill. One minute at a time two days out tunes the pace without tiring the legs.",
                "coach_note": ("On a treadmill, go by the machine's speed, not the watch. Note your HR at the end "
                               "of the third pick-up. No leg work in the gym from here to Sunday."),
            },
            "Saturday": {
                "summary": "Shakeout 20 min + strides",
                "workouts": [
                    {"sport": "running", "title": "Strides/Openers",
                     "steps": _easy(20, strides=True), "total_time": "20 min",
                     "hr_target": EASY_HR, "muscle_groups": [], "distance_km": 3.0},
                ],
                "rationale": "Loosens the legs the day before; the strides wake them up without cost.",
                "coach_note": "Very easy. Big carb dinner, early night.",
            },
            "Sunday": {
                "summary": "Half-marathon trial: 21.1 km, goal 1:37:30",
                "workouts": [
                    {"sport": "running", "title": "Half Marathon Time Trial",
                     "steps": [
                         {"type": "warmup", "duration": "15:00", "zone": 1,
                          "description": "Easy jog, then 4 x 20 s strides"},
                         {"type": "main", "duration": "97:30", "zone": 4,
                          "description": ("21.1 km. Km 1-5 at 4:40-4:42, km 6-15 at 4:37, "
                                          "last 6 km whatever is left. HR at or under 170 through km 5.")},
                         {"type": "cooldown", "duration": "10:00", "zone": 1,
                          "description": "Easy jog or walk"},
                     ],
                     "total_time": "123 min", "hr_target": "160-175 bpm",
                     "muscle_groups": [], "distance_km": 24.7,
                     "pace_target": "4:37/km (goal 1:37:30)"},
                ],
                "rationale": "Pass at 1:37:30 or faster -> the full on Dec 13 and the 27 km long run next Sunday. Slower -> race the 21K.",
                "coach_note": ("Don't start fast. If 4:40 already costs 172+ bpm by km 5, today isn't a 1:37 day; "
                               "run it steady and we decide on the data. Gel before the start, at km 7 and at km 14."),
            },
        },
    },
    date(2026, 11, 9): {
        "week_summary": {
            "focus": "Marathon week: 55 km around the 27 km long run",
            "rationale": (
                "The half trial stopped at 8.2 km and there is no retest: the "
                "build goes on. Easy days up front, one marathon-pace session "
                "on Thursday, a rest day, then the longest run of the cycle "
                "on Sunday."
            ),
        },
        "days": {
            "Monday": _rest("Rest", "The day after Sunday's trial.", ""),
            "Tuesday": {
                "summary": "Easy 40 min, upper-body gym",
                "workouts": [
                    {"sport": "running", "title": "Easy Run",
                     "steps": _easy(40), "total_time": "40 min",
                     "hr_target": EASY_HR, "muscle_groups": [], "distance_km": 6.1},
                    _strength(["chest", "shoulders", "back", "arms"],
                              "Upper: bench, overhead press, rows, pull-ups, 2 sets."),
                ],
                "rationale": "Back to running gently after Sunday.",
                "coach_note": "If your legs still feel bad, cut it to 30 minutes.",
            },
            "Wednesday": {
                "summary": "Easy 50 min, legs in the gym",
                "workouts": [
                    {"sport": "running", "title": "Easy Run",
                     "steps": _easy(50), "total_time": "50 min",
                     "hr_target": EASY_HR, "muscle_groups": [], "distance_km": 7.6},
                    _strength(["legs"],
                              "Squats, Romanian deadlifts, calf raises, 2 sets, "
                              "moderate weight, nothing to failure."),
                ],
                "rationale": "Legs come back this week, four days before the long run.",
                "coach_note": "Run first, gym after if you do both close together.",
            },
            "Thursday": {
                "summary": "3 x 8 min at marathon pace",
                "workouts": [
                    {"sport": "running", "title": "Marathon Pace Intervals",
                     "steps": [
                         {"type": "warmup", "duration": "15:00", "zone": 1,
                          "description": "Easy jog"},
                         {"type": "main", "duration": "08:00", "zone": 3,
                          "description": "At marathon pace"},
                         {"type": "recovery", "duration": "02:00", "zone": 1,
                          "description": "Easy jog"},
                         {"type": "main", "duration": "08:00", "zone": 3,
                          "description": "At marathon pace"},
                         {"type": "recovery", "duration": "02:00", "zone": 1,
                          "description": "Easy jog"},
                         {"type": "main", "duration": "08:00", "zone": 3,
                          "description": "At marathon pace"},
                         {"type": "cooldown", "duration": "10:00", "zone": 1,
                          "description": "Easy jog"},
                     ],
                     "total_time": "53 min", "hr_target": "160-168 bpm",
                     "muscle_groups": [], "distance_km": 9.4},
                ],
                "rationale": "The week's one hard session, at the pace the marathon asks for.",
                "coach_note": "If your legs are still bad, make it an easy run. On a treadmill, go by the machine's speed.",
            },
            "Friday": _rest("Rest", "Fresh legs for the weekend.", "Your calf and hip routine is fine today."),
            "Saturday": {
                "summary": "Easy 35 min",
                "workouts": [
                    {"sport": "running", "title": "Easy Run",
                     "steps": _easy(35), "total_time": "35 min",
                     "hr_target": EASY_HR, "muscle_groups": [], "distance_km": 5.3},
                ],
                "rationale": "Keeps the legs moving the day before the long run.",
                "coach_note": "Very easy. Big carb dinner, early night.",
            },
            "Sunday": {
                "summary": "Long run: 27 km",
                "workouts": [
                    {"sport": "running", "title": "Long Run",
                     "steps": [
                         {"type": "warmup", "duration": "10:00", "zone": 1,
                          "description": "Very easy jog"},
                         {"type": "main", "duration": "147:00", "zone": 2,
                          "description": "Steady at long-run pace"},
                         {"type": "cooldown", "duration": "10:00", "zone": 1,
                          "description": "Easy jog or walk"},
                     ],
                     "total_time": "167 min", "hr_target": "142-159 bpm",
                     "muscle_groups": [], "distance_km": 27.0},
                ],
                "rationale": "The longest run of the marathon build.",
                "coach_note": "Start slower than you want to. Practice your race gels.",
            },
        },
    },
}


def hand_set_week(monday: date) -> dict | None:
    """A fresh copy of the hand-set plan for the week starting `monday`, or
    None when the week is the LLM's to write."""
    week = HAND_SET_WEEKS.get(monday)
    return copy.deepcopy(week) if week else None
