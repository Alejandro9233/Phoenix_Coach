"""
Pace Model — Python-derived training paces from the watch's threshold pace.

WHY %-of-threshold over VDOT: the watch measures lactate-threshold pace
directly and refreshes it continuously (ingestion writes COROS ltsp into
athletes.threshold_pace_min_km); a VDOT table would re-derive threshold from
race results we don't store. Six bands, each a fixed multiplier of measured
threshold — Daniels-style intensity bands.

GOAL vs FITNESS: the goal time only ever influences the marathon-pace band.
Easy, long, tempo and interval paces always anchor on current fitness — a
3:10 ambition must not make Tuesday's easy run faster. When the goal outruns
fitness by more than GOAL_GAP_PCT, M-pace work is capped at
MP_CAP_AHEAD x fitness pace and a note says so; the note rides in
week_summary.rationale and /training-context so the athlete hears it from
the coach, not from a silent number.

This module owns EVERY pace/prediction constant (Riegel included) — any
race-readiness or prediction feature must import from here, so the plan's
M-pace band and a prediction card can never disagree.

Pure module: no DB, no LLM. compute_context wires it in as ctx["pace_model"].
"""
import re

RACE_KM = {
    "5k": 5.0,
    "10k": 10.0,
    "Half Marathon": 21.0975,
    "Marathon": 42.195,
}

# Band multipliers. interval/tempo scale threshold pace T; long/easy/recovery
# scale fitness marathon pace (fit_mp = MP_FROM_T x T).
INTERVAL_BAND = (0.94, 0.97)
TEMPO_BAND = (0.99, 1.02)
MP_FROM_T = 1.05
MP_BAND = (1.04, 1.06)
LONG_RUN_BAND = (1.15, 1.25)   # x fit_mp
EASY_BAND = (1.22, 1.35)       # x fit_mp
RECOVERY_BAND = (1.35, 1.45)   # x fit_mp

GOAL_GAP_PCT = 3.0     # goal within 3% of fitness -> train at goal
MP_CAP_AHEAD = 0.97    # else M-pace at most 3% faster than fitness pace
GOAL_BAND_SEC = 5      # goal-anchored band is goal_mp +/- 5 s/km

RIEGEL_EXP = 1.06
# Threshold paces outside 3:00-7:00 /km are bad watch data, not fitness.
T_BOUNDS_SEC = (180, 420)


def parse_hms(s) -> int | None:
    """"3:10:00" / "03:10:00" -> seconds. None on garbage."""
    if not s or not isinstance(s, str):
        return None
    m = re.match(r"^\s*(\d{1,2}):(\d{2}):(\d{2})\s*$", s)
    if not m:
        return None
    h, mi, sec = int(m.group(1)), int(m.group(2)), int(m.group(3))
    if mi > 59 or sec > 59:
        return None
    return h * 3600 + mi * 60 + sec


def fmt_pace(sec_km: float) -> str:
    sec_km = int(round(sec_km))
    return f"{sec_km // 60}:{sec_km % 60:02d}/km"


def fmt_band(lo: float, hi: float) -> str:
    lo_i, hi_i = int(round(lo)), int(round(hi))
    return f"{lo_i // 60}:{lo_i % 60:02d}-{hi_i // 60}:{hi_i % 60:02d}/km"


def fmt_hms(total_sec: float) -> str:
    total_sec = int(round(total_sec))
    return f"{total_sec // 3600}:{(total_sec % 3600) // 60:02d}:{total_sec % 60:02d}"


def riegel(t_sec: float, d_from_km: float, d_to_km: float) -> float:
    """Riegel time prediction across distances (exponent 1.06). Validates
    Alex's own rule of thumb: 1:27 half -> ~3:01 marathon."""
    return t_sec * (d_to_km / d_from_km) ** RIEGEL_EXP


def _band(lo: float, hi: float) -> dict:
    return {"lo": round(lo, 1), "hi": round(hi, 1), "label": fmt_band(lo, hi)}


def compute_pace_model(threshold_pace_min_km, target_finish_time,
                       race_distance) -> dict | None:
    """The six pace bands, in sec/km, from measured threshold and/or goal.

    Missing data degrades: threshold only -> source "lt_only" (marathon band
    = fitness band). Goal only (fresh DB pre-scrape) -> threshold derived
    back from the goal, source "goal_only". Both missing, non-running race,
    or implausible threshold -> None (plans stay HR-zone-based, exactly as
    before this module existed).
    """
    if race_distance not in RACE_KM:
        return None

    t_sec = None
    if threshold_pace_min_km:
        t_sec = float(threshold_pace_min_km) * 60
        if not (T_BOUNDS_SEC[0] <= t_sec <= T_BOUNDS_SEC[1]):
            t_sec = None

    goal_mp = None
    goal_sec = parse_hms(target_finish_time)
    if goal_sec:
        goal_mp = goal_sec / RACE_KM[race_distance]

    if t_sec is None and goal_mp is None:
        return None

    if t_sec is None:
        # Goal-only: derive the threshold the goal implies.
        t_sec = goal_mp / MP_FROM_T
        source = "goal_only"
    elif goal_mp is None:
        source = "lt_only"
    else:
        source = "lt_and_goal"

    fit_mp = MP_FROM_T * t_sec

    anchor = "fitness"
    gap_pct = None
    note = None
    if goal_mp is not None and source != "goal_only":
        gap_pct = (fit_mp - goal_mp) / fit_mp * 100
        if gap_pct <= GOAL_GAP_PCT:
            anchor = "goal"
            marathon = _band(goal_mp - GOAL_BAND_SEC, goal_mp + GOAL_BAND_SEC)
        else:
            marathon = _band(MP_CAP_AHEAD * fit_mp, MP_BAND[1] * t_sec)
            note = (
                f"Goal {target_finish_time} implies {fmt_pace(goal_mp)}, but "
                f"current LT pace ({fmt_pace(t_sec)}) supports ~"
                f"{fmt_pace(fit_mp)} marathon shape. Day-to-day paces follow "
                f"current fitness; M-pace work capped at "
                f"{fmt_pace(MP_CAP_AHEAD * fit_mp)}. Re-evaluated as the "
                f"watch's threshold pace updates."
            )
    else:
        if source == "goal_only":
            anchor = "goal"
        marathon = _band(MP_BAND[0] * t_sec, MP_BAND[1] * t_sec)

    return {
        "source": source,
        "anchor": anchor,
        "threshold_sec_km": round(t_sec, 1),
        "goal_mp_sec_km": round(goal_mp, 1) if goal_mp is not None else None,
        "fit_mp_sec_km": round(fit_mp, 1),
        "gap_pct": round(gap_pct, 1) if gap_pct is not None else None,
        "bands": {
            "recovery": _band(RECOVERY_BAND[0] * fit_mp, RECOVERY_BAND[1] * fit_mp),
            "easy": _band(EASY_BAND[0] * fit_mp, EASY_BAND[1] * fit_mp),
            "long_run": _band(LONG_RUN_BAND[0] * fit_mp, LONG_RUN_BAND[1] * fit_mp),
            "marathon": marathon,
            "tempo": _band(TEMPO_BAND[0] * t_sec, TEMPO_BAND[1] * t_sec),
            "interval": _band(INTERVAL_BAND[0] * t_sec, INTERVAL_BAND[1] * t_sec),
        },
        "note": note,
    }
