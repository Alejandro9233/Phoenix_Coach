"""
Pace Enforcer — every running workout carries a Python-computed pace_target.

The LLM used to invent workout paces. Now pace_model.py computes the bands
from the watch's measured threshold, the prompt shows them (RUN PACES card),
and this module force-sets workout["pace_target"] to the canonical band
label AFTER generation — a missing or wrong field self-heals, and the iOS
card renders pace_target as the authoritative line.

Deliberately NOT done (design decision, 2026-08-21): no regex rewriting of
pace strings inside coach prose. The earlier design admitted it produced
awkward text and it breaks quietly on phrasing drift; instead the prompt
stops asking for numeric paces in prose, and the structured field is the
truth. Correction, not stripping — a wrong pace never deletes a session
(stripping stays reserved for constraint_enforcer and the volume gate).

Runs inside run_plan_write_pipeline on every persist path. pace_model=None
(no threshold, non-running race) -> no-op, plans stay HR-zone-based.
"""
import re

from backend.services.plan_normalizer import VALID_DAYS, map_sport
from backend.services.volume_gate import parse_minutes

# Order matters: "marathon pace long run" must classify as marathon, not
# long_run. Vocabulary is closed — titles come from the phase menus and B2
# rejects inventions — so substring matching over menu words is enough.
_CLASSIFICATION = [
    ("marathon", ("marathon pace", "m-pace", "mp long")),
    ("progressive", ("progressive", "progression")),
    ("long_run", ("long run",)),
    ("tempo", ("tempo", "cruise", "threshold")),
    ("interval", ("vo2", "interval", "repetition", "sprint")),
    ("recovery", ("recovery",)),
    ("easy", ("stride", "opener", "easy", "shakeout", "jog")),
]


def classify_run_workout(title: str) -> str:
    t = (title or "").lower()
    for band, keywords in _CLASSIFICATION:
        if any(k in t for k in keywords):
            return band
    return "easy"  # unknown drifts to the safe band


def _band_label(pace_model: dict, band_key: str) -> str | None:
    bands = (pace_model or {}).get("bands") or {}
    if band_key == "progressive":
        # A progressive run spans easy down to marathon effort.
        lo = (bands.get("marathon") or {}).get("lo")
        hi = (bands.get("easy") or {}).get("hi")
        if lo is None or hi is None:
            return None
        from backend.services.pace_model import fmt_band
        return fmt_band(lo, hi)
    return (bands.get(band_key) or {}).get("label")


# --- Main-step arithmetic ------------------------------------------------
# The LLM writes step durations freehand: 2026-08-31 shipped "30:00" for
# "8 km at tempo pace" (3:45/km — faster than the athlete's threshold) on
# 3 of 5 run days. The gate checks distance against total_time only, never
# against the main step, so the watch guidance was silently impossible.
# Fires only when the main step's description names an explicit km figure
# and the workout's band is known — correction, not stripping, same charter
# as pace_target above.

_KM_RE = re.compile(r"(\d+(?:\.\d+)?)\s*km", re.I)


def _parse_step_seconds(text):
    if not isinstance(text, str):
        return None
    parts = text.strip().split(":")
    try:
        nums = [int(x) for x in parts]
    except ValueError:
        return None
    if len(nums) == 2:
        return nums[0] * 60 + nums[1]
    if len(nums) == 3:
        return nums[0] * 3600 + nums[1] * 60 + nums[2]
    return None


def _fmt_step_seconds(sec):
    sec = int(round(sec / 30.0)) * 30  # watch-friendly :00/:30 steps
    return f"{sec // 60}:{sec % 60:02d}"


def _reconcile_main_step(w, band):
    """When the main step claims N km but its duration can't cover N km in
    the prescribed band, recompute the duration at the band midpoint and
    shift total_time by the same delta. Returns a correction dict or None."""
    lo, hi = band.get("lo"), band.get("hi")  # sec/km
    if not lo or not hi:
        return None
    main = next((s for s in w.get("steps") or []
                 if isinstance(s, dict) and s.get("type") == "main"), None)
    if main is None:
        return None
    desc = main.get("description") or ""
    # Repetition structures ("5 x 1 km", "4×800m") describe one rep, not the
    # block — reconciling against them would shrink an interval session to a
    # single rep's duration. Continuous steps only.
    if re.search(r"\d\s*[x×]\s*\d", desc, re.I):
        return None
    matches = _KM_RE.findall(desc)
    if not matches:
        return None
    km = max(float(x) for x in matches)
    declared = w.get("distance_km")
    if (isinstance(declared, (int, float)) and declared > 0
            and km < 0.6 * float(declared)):
        # The matched figure is a segment ("steady, last 3 km at MP"), not
        # the step's distance — reconciling against it would shred the step.
        return None
    if not (0.5 <= km <= 60):
        return None
    cur = _parse_step_seconds(main.get("duration"))
    if cur is None:
        return None
    # Upward-only. Under-duration is the defect (impossible pace); a long
    # easy step is a choice, never an arithmetic error — a downward rewrite
    # here turned a 90-min long run into ~24 min in review (2026-08-31).
    if cur >= km * lo * 0.95:
        return None
    new_sec = km * (lo + hi) / 2.0
    old_txt = main["duration"]
    main["duration"] = _fmt_step_seconds(new_sec)
    delta_min = (int(round(new_sec / 30.0)) * 30 - cur) / 60.0
    total_min = parse_minutes(w.get("total_time"))
    if total_min:
        # parse_minutes reads every real format ("50 min", "1:15:00",
        # "75 minutes"); re-emit normalized. Delta is positive by the
        # upward-only rule above.
        w["total_time"] = f"{int(round(total_min + delta_min))} min"
    return {"step_from": old_txt, "step_to": main["duration"],
            "step_km": km}


def enforce_paces(plan_json: dict, pace_model: dict, days=None) -> tuple:
    """Set pace_target on every running workout in the window. Returns
    (plan_json, corrections); corrections list what changed, for logs."""
    if not pace_model:
        return plan_json, []

    corrections = []
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
            band_key = classify_run_workout(w.get("title"))
            label = _band_label(pace_model, band_key)
            if not label:
                continue
            band = (pace_model.get("bands") or {}).get(band_key) or {}
            if band_key != "progressive":
                fix = _reconcile_main_step(w, band)
                if fix:
                    corrections.append({"day": day_name,
                                        "title": w.get("title"), **fix})
            if w.get("pace_target") != label:
                corrections.append({
                    "day": day_name,
                    "title": w.get("title"),
                    "found": w.get("pace_target"),
                    "set": label,
                })
            w["pace_target"] = label

    # The goal-vs-fitness note reaches the athlete even if the LLM ignored
    # it — appended once to the week's rationale, deterministically.
    note = pace_model.get("note")
    if note:
        ws = plan_json.setdefault("week_summary", {})
        rationale = ws.get("rationale") or ""
        if note not in rationale:
            ws["rationale"] = f"{rationale} {note}".strip()

    return plan_json, corrections
