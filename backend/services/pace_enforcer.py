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
from backend.services.plan_normalizer import VALID_DAYS, map_sport

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
            label = _band_label(pace_model, classify_run_workout(w.get("title")))
            if not label:
                continue
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
