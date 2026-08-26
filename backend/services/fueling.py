"""
Fuel lines for long runs — Python numbers, stamped post-generation.

At 3:10 marathon shape Alex is out ~3 hours; gut training on long runs is a
top-3 marathon determinant, and today's plans never mention fuel. The numbers
are deterministic sports-science bands, so the LLM is never asked to phrase
them (a prompt-only fuel note is a suggestion, and the Groq prompt budget has
~850 spare tokens — this costs zero). stamp_fuel() runs at the END of
run_plan_write_pipeline, mirroring pace_enforcer: force-set on every running
workout that qualifies, removed when a replan shortens the run, idempotent.

plan_normalizer.normalize_workout carries "fuel" explicitly — without that
carry every replan would erase the field (same trap as pace_target).

Bands (per hour, mid-run):
- 90-120 min: 45-60 g carbs — routine long-run fueling.
- > 120 min: 60-90 g carbs — race-gut training territory.
- Fluid: 8-10 ml/kg body weight when weight is known (rounded to 20 ml),
  500-750 ml otherwise; always phrased with a heat caveat rather than a
  location (the phone's timezone travels — the app never assumes Hermosillo).
"""
from backend.services.plan_normalizer import VALID_DAYS, map_sport
from backend.services.volume_gate import parse_minutes

FUEL_MIN_MINUTES = 90          # below this, no fuel line at all
CARB_BAND_ROUTINE = "45-60 g carbs/h"     # 90-120 min
CARB_BAND_LONG = "60-90 g carbs/h"        # > 120 min
CARB_LONG_THRESHOLD_MIN = 120
FLUID_ML_PER_KG = (8, 10)      # ml per kg body weight per hour
FLUID_DEFAULT = (500, 750)     # ml/h when weight is unknown


def _round20(x: float) -> int:
    return int(round(x / 20) * 20)


def fuel_line(duration_min, weight_kg) -> str | None:
    """The fuel string for one workout, or None when it needs no fuel line."""
    minutes = parse_minutes(duration_min)
    if not minutes or minutes < FUEL_MIN_MINUTES:
        return None
    carbs = (
        CARB_BAND_LONG if minutes > CARB_LONG_THRESHOLD_MIN else CARB_BAND_ROUTINE
    )
    if weight_kg:
        lo = _round20(FLUID_ML_PER_KG[0] * float(weight_kg))
        hi = _round20(FLUID_ML_PER_KG[1] * float(weight_kg))
    else:
        lo, hi = FLUID_DEFAULT
    return f"{carbs} · {lo}-{hi} ml fluid/h (more in heat)"


def stamp_fuel(plan_json: dict, weight_kg, days=None) -> tuple:
    """Force-set/clear the "fuel" field on running workouts in the window.

    Returns (plan_json, changes). Pure and idempotent: a qualifying run gets
    the computed line unconditionally (correction, not stripping); a run that
    no longer qualifies loses a stale line. Non-running sports are never
    touched — fuel is a long-run concern.
    """
    changes = []
    window = list(days) if days is not None else list(VALID_DAYS)
    for day_name in window:
        day = (plan_json.get("days") or {}).get(day_name)
        if not isinstance(day, dict):
            continue
        for w in day.get("workouts") or []:
            if not isinstance(w, dict):
                continue
            if map_sport(w.get("sport") or "") != "running":
                continue
            line = fuel_line(w.get("total_time"), weight_kg)
            if line is not None:
                if w.get("fuel") != line:
                    changes.append({"day": day_name, "title": w.get("title"),
                                    "set": line})
                    w["fuel"] = line
            elif "fuel" in w:
                changes.append({"day": day_name, "title": w.get("title"),
                                "set": None})
                w.pop("fuel", None)
    return plan_json, changes
