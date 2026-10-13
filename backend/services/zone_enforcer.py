"""
Zone Enforcer — every running step carries a Python-computed HR zone.

The LLM copies the prompt's example "zone": 1 onto every step. The week of
2026-10-12 shipped Z1 on warmups, on 140-150 bpm easy main sets and on
20-second strides alike, while the workout's hr_target was right. The label
still matters: the Today card's "target Z" reads the main step's zone, the
watch's warmup/recovery/cooldown sections carry the step zone (main sections
carry the bpm range, coros_watch.workout_to_course), and the course
description prints Z<zone> per step. Same charter as pace_enforcer: the
zone is arithmetic on things Python owns, so Python stamps it. Correction,
not stripping; never deletes a session; never touches rides, strength or
rest days.

Zones are the athlete's own COROS lactate-threshold zones (athlete.hr_zones,
six ceilings — the ones the watch itself shows), falling back to LTHR
fractions (personal_model.ZONES) when the watch hasn't sent a table. No
table and no LTHR -> no-op.

Rules, per step of a running workout:
- main: the zone of the workout's hr_target midpoint when it is a bpm range
  ("140-150 bpm" -> 145 -> Z2 for a 142-ceiling Z1). Otherwise the zone the
  title's band implies: recovery 1, easy/long run/progressive 2, marathon 3,
  tempo 4, interval 5.
- warmup / cooldown: 1.
- any step whose description names strides, openers, sprints, surges or
  pickups: 3. Twenty-second efforts on jog recoveries average a tempo-ish
  heart rate; it is the least wrong single zone for a mixed block and stops
  the watch calling the fast part of the session Z1.
- other recovery steps: 1.
"""
import re

from backend.services.pace_enforcer import classify_run_workout
from backend.services.plan_normalizer import VALID_DAYS, map_sport

COROS_MAX_ZONE = 6
# LTHR fractions when the watch hasn't sent its table (personal_model.ZONES):
# ceilings of Z1..Z4, Z5 above.
LTHR_FRACTIONS = (0.81, 0.90, 0.94, 1.00)
BAND_ZONE = {
    "recovery": 1, "easy": 2, "long_run": 2, "progressive": 2,
    "marathon": 3, "tempo": 4, "interval": 5,
}
STRIDES_ZONE = 3
_FAST_BLOCK_RE = re.compile(r"strid|opener|sprint|surge|pick-?up", re.I)


def zone_ceilings(hr_zones=None, lthr=None):
    """Ascending zone ceilings in bpm, or None when neither anchor exists.
    hr_zones is the COROS lthrZone list ({index, hr}); lthr the bare bpm."""
    from backend.services.pace_model import _zone_tops
    tops = _zone_tops(hr_zones)
    if tops:
        return [tops[i] for i in sorted(tops)]
    if isinstance(lthr, (int, float)) and not isinstance(lthr, bool) and lthr > 0:
        return [int(round(lthr * f)) for f in LTHR_FRACTIONS]
    return None


def zone_for_bpm(bpm, ceilings) -> int:
    """1 + the number of ceilings strictly below bpm, capped at COROS's six."""
    zone = 1 + sum(1 for c in ceilings if c < bpm)
    return min(zone, COROS_MAX_ZONE)


def _main_zone(workout: dict, ceilings) -> int:
    from backend.services.coros_watch import hr_target_range
    rng = hr_target_range(workout)
    if rng:
        return zone_for_bpm((rng[0] + rng[1]) / 2, ceilings)
    return BAND_ZONE.get(classify_run_workout(workout.get("title")), 2)


def step_zone(step: dict, main_zone: int) -> int:
    if _FAST_BLOCK_RE.search(str(step.get("description") or "")):
        return STRIDES_ZONE
    if step.get("type") == "main":
        return main_zone
    return 1   # warmup, cooldown, recovery jogs


def enforce_zones(plan_json: dict, hr_zones=None, lthr=None, days=None) -> tuple:
    """Stamp `zone` on every step of every running workout in the window.
    Returns (plan_json, corrections); corrections list what changed."""
    ceilings = zone_ceilings(hr_zones, lthr)
    if not ceilings:
        return plan_json, []
    corrections = []
    window = list(days) if days is not None else list(VALID_DAYS)
    for day_name in window:
        day = (plan_json.get("days") or {}).get(day_name)
        if not isinstance(day, dict):
            continue
        for w in day.get("workouts") or []:
            if not isinstance(w, dict) or map_sport(w.get("sport") or "") != "running":
                continue
            steps = [s for s in (w.get("steps") or []) if isinstance(s, dict)]
            if not steps:
                continue
            main_zone = _main_zone(w, ceilings)
            for s in steps:
                target = step_zone(s, main_zone)
                if s.get("zone") != target:
                    corrections.append({"day": day_name, "title": w.get("title"),
                                        "step": s.get("type"), "found": s.get("zone"),
                                        "set": target})
                    s["zone"] = target
    return plan_json, corrections
