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
- any other step whose description names strides, openers, sprints, surges
  or pickups: 3. A warm-up that ends in strides stays 1: the Nov 8 trial's
  "Easy jog, then 4 x 20 s strides" went to the watch as Z3, a 160-168 bpm
  target for the whole 15-minute warm-up. Twenty-second efforts on jog recoveries average a tempo-ish
  heart rate; it is the least wrong single zone for a mixed block and stops
  the watch calling the fast part of the session Z1.
- other recovery steps: 1.

The workout's hr_target itself is the LLM's, and the watch's main sections
and compliance both read it as the bpm target. It is kept unless the title
names an easy, long, marathon or tempo run and the range is missing or
sits 2+ zones from that band (an easy run carrying a tempo range); then it
is restamped to the band's zone range. Within one zone the coach's call
stands. Recovery, progressive and interval runs are left alone — no closed
range, or the range is the point.
"""
import re

from backend.services.pace_enforcer import _CLASSIFICATION, classify_run_workout
from backend.services.plan_normalizer import VALID_DAYS, map_sport

COROS_MAX_ZONE = 6
# LTHR fractions when the watch hasn't sent its table (personal_model.ZONES):
# ceilings of Z1..Z4, Z5 above.
LTHR_FRACTIONS = (0.81, 0.90, 0.94, 1.00)
BAND_ZONE = {
    "recovery": 1, "easy": 2, "long_run": 2, "progressive": 2,
    "marathon": 3, "tempo": 4, "interval": 5, "trial": 4,
}
STRIDES_ZONE = 3
# Bands whose hr_target Python restamps when it is missing or 2+ zones off.
STAMP_HR_BANDS = ("easy", "long_run", "marathon", "tempo")
HR_ZONE_TOLERANCE = 1
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


def _named_band(title) -> str | None:
    """The band a title names outright — never classify_run_workout's
    "easy" fallback for a title it doesn't recognize."""
    t = (title or "").lower()
    for band, keywords in _CLASSIFICATION:
        if any(k in t for k in keywords):
            return band
    return None


def zone_range(zone: int, ceilings) -> tuple[int, int] | None:
    """(low, high) bpm of a zone with a ceiling on both sides, else None."""
    if zone < 2 or zone > len(ceilings):
        return None
    return ceilings[zone - 2] + 1, ceilings[zone - 1]


def _stamp_hr_target(workout: dict, ceilings) -> dict | None:
    """Restamp hr_target to the title band's zone when it is missing or
    2+ zones off. Returns the correction, or None when it stands."""
    from backend.services.coros_watch import hr_target_range
    band = _named_band(workout.get("title"))
    if band not in STAMP_HR_BANDS:
        return None
    zone = BAND_ZONE[band]
    rng = hr_target_range(workout)
    if rng and abs(zone_for_bpm((rng[0] + rng[1]) / 2, ceilings) - zone) <= HR_ZONE_TOLERANCE:
        return None
    target = zone_range(zone, ceilings)
    if not target:
        return None
    found = workout.get("hr_target")
    workout["hr_target"] = f"{target[0]}-{target[1]} bpm"
    return {"field": "hr_target", "found": found, "set": workout["hr_target"]}


def step_zone(step: dict, main_zone: int) -> int:
    if step.get("type") in ("warmup", "cooldown"):
        return 1
    if _FAST_BLOCK_RE.search(str(step.get("description") or "")):
        return STRIDES_ZONE
    if step.get("type") == "main":
        return main_zone
    return 1   # recovery jogs


def enforce_zones(plan_json: dict, hr_zones=None, lthr=None, days=None) -> tuple:
    """Stamp `zone` on every step of every running workout in the window,
    after guarding its hr_target (module docstring). Returns (plan_json, corrections); corrections list what changed."""
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
            fix = _stamp_hr_target(w, ceilings)
            if fix:
                corrections.append({"day": day_name, "title": w.get("title"),
                                    "step": None, **fix})
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
