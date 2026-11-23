"""
Shoulder guard — gym sessions an injured shoulder can do.

WHY THIS EXISTS: the injury log blocks whole sports (constraint_enforcer),
and a sore shoulder doesn't block the gym, only the pressing in it. Alex,
2026-11-23: the right shoulder hurt through a 30 km run, ten days after he
started swimming on top of overhead press and bench. He asked for no
swimming and no press, and for exercises that strengthen it. The swim is the
injury row's job (`affected_sports`: swim). "No press" has no sport to
block, and telling the LLM is a suggestion.

So while an active injury names a shoulder, every strength session being
written is replaced by one Python writes: legs and core, nothing pressed,
pulled overhead or hung from, plus ten minutes of cuff and shoulder-blade
work. The whole session is replaced because the LLM's is prose ("2 sets:
overhead press, rows, lunges") and rewriting pace prose by pattern was
already tried and dropped (pace_enforcer). The length stays the LLM's, with
a 30-minute floor.

Resolve the injury and the next plan write is the LLM's again.
"""
from typing import Any, Iterable, Optional

from backend.services.plan_normalizer import VALID_DAYS, map_sport
from backend.services.volume_gate import parse_minutes

SHOULDER_WORDS = ("shoulder", "hombro")
MIN_MINUTES = 30
ROUTINE_MINUTES = 10
MAIN = ("Legs and core, 2 sets: split squats or lunges, Romanian deadlifts, "
        "calf raises, plank, side plank. No press, bench, dips or pull-ups.")
ROUTINE = ("Shoulder routine, slow and pain-free: band external rotation 2 x 15, "
           "band rows with the elbows low 2 x 15, wall slides 2 x 10, "
           "side-lying external rotation 2 x 12 with a light weight. "
           "Stop an exercise that hurts.")


def shoulder_injury(active_injuries: Optional[Iterable[Any]]):
    """The first active injury whose body part names a shoulder, or None."""
    for inj in active_injuries or []:
        part = (getattr(inj, "body_part", None) or "").lower()
        if any(word in part for word in SHOULDER_WORDS):
            return inj
    return None


def _steps(minutes: int) -> list:
    return [
        {"type": "warmup", "duration": "05:00", "zone": 1,
         "description": "Easy mobility for hips and upper back"},
        {"type": "main", "duration": f"{minutes - ROUTINE_MINUTES - 10:02d}:00", "zone": 2,
         "description": MAIN},
        {"type": "main", "duration": f"{ROUTINE_MINUTES:02d}:00", "zone": 1,
         "description": ROUTINE},
        {"type": "cooldown", "duration": "05:00", "zone": 1,
         "description": "Stretch"},
    ]


def enforce_shoulder(plan_json: dict, active_injuries,
                     days: Optional[Iterable[str]] = None) -> tuple:
    """Returns `(plan_json, changes)`; changes are the strength sessions
    rewritten, as {"day", "title"}. Mutates plan_json in place."""
    injury = shoulder_injury(active_injuries)
    if injury is None:
        return plan_json, []
    target = set(days) if days is not None else set(VALID_DAYS)
    changes = []
    for day_name in VALID_DAYS:
        day = (plan_json.get("days") or {}).get(day_name)
        if day_name not in target or not isinstance(day, dict):
            continue
        for w in day.get("workouts") or []:
            if not isinstance(w, dict) or map_sport(w.get("sport") or "") != "strength":
                continue
            minutes = max(int(parse_minutes(w.get("total_time")) or 0), MIN_MINUTES)
            steps = _steps(minutes)
            if w.get("steps") == steps:
                continue
            w["steps"] = steps
            w["total_time"] = f"{minutes} min"
            w["muscle_groups"] = ["legs"]
            w["hr_target"] = "--"
            w["enforced_note"] = f"{injury.body_part}: no pressing or hanging, shoulder routine added"
            changes.append({"day": day_name, "title": w.get("title")})
    return plan_json, changes
