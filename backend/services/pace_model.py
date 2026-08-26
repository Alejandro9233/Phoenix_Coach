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

# Race-day pacing: the first 5K goes out deliberately slower — a 3:10 attempt
# is mostly lost by going out at sub-3 pace. The lost seconds are made back by
# a slightly faster cruise pace, so the table still lands on the target.
FIRST_5K_EASE_SEC = 6  # s/km added to the first 5K
EASE_MIN_RACE_KM = 10.1  # short races don't need a conservative-start table

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


def _fmt_mmss(sec: float) -> str:
    sec = int(round(sec))
    if sec >= 3600:
        return fmt_hms(sec)
    return f"{sec // 60}:{sec % 60:02d}"


def _zone_tops(hr_zones) -> dict:
    """COROS lthrZone list -> {index: ceiling bpm}. Entries are {index, hr}
    (0-based index, hr = the zone's boundary bpm; "ratio" may be absent and is
    ignored). Malformed entries are skipped, never fatal."""
    tops = {}
    for z in hr_zones or []:
        if not isinstance(z, dict):
            continue
        idx, hr = z.get("index"), z.get("hr")
        if isinstance(idx, int) and isinstance(hr, (int, float)) and hr > 0:
            tops[idx] = int(hr)
    return tops


def race_pacing(target_finish_time, race_distance, hr_zones=None) -> dict | None:
    """Deterministic race-day pacing table from the stored target.

    Per-5K splits with a positive-split-safe first 5K (+FIRST_5K_EASE_SEC
    s/km, recovered by the cruise pace so the total still hits the target),
    waypoint times, and HR caps from the watch's LTHR zones when present.
    None when the target or distance can't produce a table (non-running goal,
    unparseable time) — the caller degrades to a countdown without a table.
    """
    goal_km = RACE_KM.get(race_distance)
    total = parse_hms(target_finish_time)
    if not goal_km or not total:
        return None

    avg = total / goal_km
    if goal_km > EASE_MIN_RACE_KM:
        first_pace = avg + FIRST_5K_EASE_SEC
        cruise_pace = (total - 5 * first_pace) / (goal_km - 5)
    else:
        first_pace = cruise_pace = avg

    def cumulative(km: float) -> float:
        if km <= 5:
            return km * first_pace
        return 5 * first_pace + (km - 5) * cruise_pace

    splits = []
    prev_t = 0.0
    km = 5.0
    while km < goal_km - 0.05:
        t = cumulative(km)
        splits.append({
            "to_km": km,
            "split": _fmt_mmss(t - prev_t),
            "cumulative": fmt_hms(t),
            "pace": fmt_pace(first_pace if km <= 5 else cruise_pace),
        })
        prev_t = t
        km += 5.0
    splits.append({
        "to_km": round(goal_km, 1),
        "split": _fmt_mmss(total - prev_t),
        "cumulative": fmt_hms(total),
        "pace": fmt_pace(cruise_pace),
    })

    if race_distance == "Marathon":
        waypoint_marks = [("Half", RACE_KM["Half Marathon"]), ("30K", 30.0)]
    elif race_distance == "Half Marathon":
        waypoint_marks = [("10K", 10.0)]
    else:
        waypoint_marks = []
    waypoints = [
        {"label": label, "km": km_mark, "time": fmt_hms(cumulative(km_mark))}
        for label, km_mark in waypoint_marks
    ] + [{"label": "Finish", "km": round(goal_km, 1), "time": fmt_hms(total)}]

    tops = _zone_tops(hr_zones)
    hr_caps = None
    if 1 in tops and 2 in tops:
        hr_caps = {
            "first_10k": tops[1],   # top of Z2 — settle in, spend nothing
            "to_30k": tops[2],      # top of Z3 — marathon effort
            "final": None,          # race effort, no cap
        }

    return {
        "target": target_finish_time,
        "distance_km": round(goal_km, 1),
        "avg_pace": fmt_pace(avg),
        "first_5k_pace": fmt_pace(first_pace),
        "cruise_pace": fmt_pace(cruise_pace),
        "splits": splits,
        "waypoints": waypoints,
        "hr_caps": hr_caps,
    }


def race_label(distance_km) -> str:
    """21.0975 -> "Half Marathon"; anything within 3% of a known race counts
    (a scraped GPS half reads ~21.3). Unknown distances stay numeric."""
    if not distance_km:
        return "race"
    for name, km in RACE_KM.items():
        if abs(distance_km - km) / km <= 0.03:
            return name
    return f"{distance_km:g} km race"


def tuneup_verdict(result_sec, result_km, goal_distance,
                   target_finish_time=None) -> dict | None:
    """Riegel verdict from a tune-up race result: what the result predicts at
    the goal distance, and how that compares to the current target. The
    proposed target IS the prediction — Alex rounds it himself in the Profile
    picker; nothing here writes target_finish_time.
    """
    goal_km = RACE_KM.get(goal_distance)
    if not goal_km or not result_sec or not result_km or result_km <= 0:
        return None
    pred = riegel(float(result_sec), float(result_km), goal_km)
    verdict = {
        "predicted": fmt_hms(pred),
        "predicted_sec": int(round(pred)),
        "goal_distance": goal_distance,
        "goal": None,
        "delta_sec": None,
        "summary": (
            f"{fmt_hms(result_sec)} over {race_label(result_km)} predicts "
            f"{fmt_hms(pred)} for the {goal_distance} (Riegel "
            f"{RIEGEL_EXP:g}). Proposed target: {fmt_hms(pred)}."
        ),
    }
    goal_sec = parse_hms(target_finish_time)
    if goal_sec:
        delta = int(round(pred - goal_sec))
        side = "under" if delta <= 0 else "over"
        d = abs(delta)
        d_str = fmt_hms(d) if d >= 3600 else f"{d // 60}:{d % 60:02d}"
        verdict.update({
            "goal": target_finish_time,
            "delta_sec": delta,
            "summary": (
                f"{fmt_hms(result_sec)} over {race_label(result_km)} predicts "
                f"{fmt_hms(pred)} for the {goal_distance} — {d_str} {side} "
                f"the {target_finish_time} target. Proposed target: "
                f"{fmt_hms(pred)}."
            ),
        })
    return verdict


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
