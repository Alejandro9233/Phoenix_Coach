"""Per-block workout report from a COROS activity-detail payload.

The watch records structured workouts in blocks (warm up / main set / cool
down), but the site only surfaces whole-activity averages. This module turns
the ``/activity/detail/query`` JSON into a block-by-block report: what was
prescribed, what was run, and how much of each block stayed in range.

Verified against the 2026-08-25 tempo run (fixture:
``backend/tests/fixtures/tempo_run_detail.json``) — every number below
reconstructs the COROS web UI exactly. The load-bearing unit facts:

- distance = centimeters; every time and timestamp = CENTISECONDS
  (timestamps are unix epoch x100); pace fields = seconds per km
  (``adjustedPace`` is the UI's "effort pace"); calories = 1/1000 kcal;
  weather temperature = 0.1 C; ``summary.timezone`` = UTC offset in
  quarter-hours.
- ``summary.avgSpeed`` switches meaning by sport: s/km on runs, km/h x100
  on rides (2640 -> 26.40 km/h, verified against the 2026-08-26 Z2 ride).
  Ride blocks also prescribe HR as a band (``intensityValue`` low +
  ``intensityValueExtend`` high) where easy-run blocks use a bare cap
  (low = -1). Rides add lap groups type 11/12 (undecoded, ignored).
- ``data.lapList`` entries by ``type``: 2 = programmed blocks, 10 = km
  splits (the km counter RESTARTS at each block boundary), -1 = whole
  activity. Free runs have no type-2 entry at all.
- ``targetValue`` = prescribed seconds, for every block. A block whose time
  lands on targetValue to the centisecond ended on the timer; anything else
  means the athlete pressed the lap key.
- Prescribed range: ``intensityType`` 2 = HR cap in ``intensityValueExtend``
  (bpm); 3 = pace window ``intensityValue``..``intensityValueExtend`` /
  ``intensityMultiplier`` (s/km); 0 = none.
- ``standardRate`` = fraction 0-1 of the block in range, -1 = untargeted lap
  (``programExerciseIndex`` 255 = recorded after the program ended).
  Display it, never recompute it: the watch derives pace compliance from
  onboard real-time pace the exported stream can't reproduce.

Traps (all observed in the fixture): block times sum 1 centisecond short of
the whole; ``pauseList`` disagrees with ``summary.pauseTime`` by 75 cs (the
summary value is what the UI shows); ``bestKm`` truncates where lap paces
round; per-lap ``avgSpeedV2`` is km/h x100 while the whole-activity lap's is
s/km (this module reads only ``avgPace``/``avgSpeed`` and sidesteps it);
split-row ``avgMoveSpeed`` is garbage on partial rows, so split paces are
recomputed as time/distance. HR/cadence/power read 0 when the sensor had no
data — decoded to None, and the renderer drops the segment instead of
printing zeros a coach would read as real.
"""

from datetime import datetime, timedelta, timezone

# exerciseType -> block name as the COROS UI shows it
EXERCISE_NAMES = {1: "Warm Up", 2: "Run", 3: "Cool Down", 0: "Run"}

NON_PROGRAM_INDEX = 255  # programExerciseIndex sentinel: lap outside the program
# |time - target| under this many centiseconds counts as ended-on-timer
TIMER_TOLERANCE_CS = 100


# ---------------------------------------------------------------- formatting

def fmt_time(seconds):
    """2412.95 s -> '40:13'; 3-hour-plus gets an hour field."""
    total = int(round(seconds))
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def fmt_pace(s_per_km):
    """341.29 s/km -> 5'41" (nearest second, like the UI's block paces)."""
    if not s_per_km or s_per_km <= 0:
        return "-"
    total = int(round(s_per_km))
    return f"{total // 60}'{total % 60:02d}\""


def _fmt_pace_trunc(s_per_km):
    """bestKm arrives pre-truncated by the watch; keep its convention."""
    if not s_per_km or s_per_km <= 0:
        return "-"
    total = int(s_per_km)
    return f"{total // 60}'{total % 60:02d}\""


def _opt(value, spec):
    """None-safe format: missing sensor data renders as '-', never crashes."""
    return format(value, spec) if value is not None else "-"


def _kmh(dist_cm, time_cs):
    """Sport-independent speed from the raw cm/centisecond pair."""
    return (dist_cm / 100000) / (time_cs / 360000) if dist_cm and time_cs else None


# ------------------------------------------------------------------ decoding

def _lap_groups(data):
    return {entry.get("type"): entry.get("lapItemList") or []
            for entry in data.get("lapList") or []}


def _decode_intensity(item):
    kind = item.get("intensityType")
    if kind == 2:
        low, cap = item.get("intensityValue"), item.get("intensityValueExtend")
        if not cap:
            return None
        # A Z2 ride prescribes a band (142-159); an easy-run cap has low = -1.
        if low and low > 0:
            return {"kind": "hr_range", "low_bpm": low, "high_bpm": cap}
        return {"kind": "hr_below", "bpm": cap}
    if kind == 3:
        mult = item.get("intensityMultiplier") or 1
        low, high = item.get("intensityValue"), item.get("intensityValueExtend")
        if low and high:
            return {"kind": "pace_range",
                    "low_s_per_km": low / mult, "high_s_per_km": high / mult}
    return None


def _decode_block(item):
    time_cs = item.get("time") or 0
    target_s = item.get("targetValue")
    is_program = item.get("programExerciseIndex") != NON_PROGRAM_INDEX
    ended = None
    if target_s:
        on_timer = abs(time_cs - target_s * 100) < TIMER_TOLERANCE_CS
        ended = "timer" if on_timer else "lap_key"
    rate = item.get("standardRate")
    stride_cm = item.get("avgStrideLength")
    osc_mm = item.get("strideHeight")
    return {
        "name": EXERCISE_NAMES.get(item.get("exerciseType"), "Block"),
        "exercise_index": item.get("exerciseIndex"),
        "is_program": is_program,
        "distance_km": (item.get("distance") or 0) / 100000,
        "time_s": time_cs / 100,
        "pace_s_per_km": item.get("avgPace") or None,
        "speed_kmh": _kmh((item.get("distance") or 0), time_cs),
        "avg_hr": item.get("avgHr") or None,
        "max_hr": item.get("maxHr") or None,
        "avg_cadence": item.get("avgCadence") or None,
        "avg_power": item.get("avgPower") or None,
        "stride_m": stride_cm / 100 if stride_cm else None,
        "ground_time_ms": item.get("groundTime") or None,
        "vertical_osc_cm": osc_mm / 10 if osc_mm else None,
        "pause_s": (item.get("pauseTime") or 0) / 100,
        "standard_rate": None if rate is None or rate < 0 else rate,
        "target_s": target_s,
        "intensity": _decode_intensity(item),
        "ended": ended,
    }


def _decode_split(item, km_in_block):
    dist_cm = item.get("distance") or 0
    time_cs = item.get("time") or 0
    pace = (time_cs / 100) / (dist_cm / 100000) if dist_cm else None
    return {
        "km_in_block": km_in_block,
        "distance_km": dist_cm / 100000,
        "time_s": time_cs / 100,
        "pace_s_per_km": pace,
        "speed_kmh": _kmh(dist_cm, time_cs),
        "avg_hr": item.get("avgHr") or None,
        "avg_power": item.get("avgPower") or None,
        "avg_cadence": item.get("avgCadence") or None,
        "partial": dist_cm < 100000,
    }


def _local_start(summary):
    ts_cs = summary.get("startTimestamp")
    if not ts_cs:
        return None
    offset = timedelta(minutes=15 * (summary.get("timezone") or 0))
    return datetime.fromtimestamp(ts_cs / 100, tz=timezone(offset))


def _tenths(value):
    return value / 10 if value is not None else None


def decode_activity(payload):
    """Full detail payload (or its ``data`` dict) -> structured block report.

    Raises ValueError on a payload that is not an activity (e.g. a saved
    COROS error response — those carry ``"data": null``).
    """
    data = payload.get("data") or payload
    if not isinstance(data, dict) or \
            (not data.get("summary") and not data.get("lapList")):
        raise ValueError(
            "not an activity detail payload "
            f"(result={payload.get('result')!r}, "
            f"message={payload.get('message')!r})")
    summary = data.get("summary") or {}
    groups = _lap_groups(data)

    # Sport families: 1xx run, 2xx bike, 3xx swim, 4xx strength. Runs and
    # rides get the full lap treatment; strength records its sets/rests as
    # dozens of zero-distance "laps" nobody should read as a block table,
    # and swim payloads are unverified (no swim in the athlete's history yet).
    family = (summary.get("sportType") or 0) // 100

    blocks = [_decode_block(item) for item in groups.get(2, [])]
    if family == 2:
        for b in blocks:
            b["name"] = "Ride" if b["name"] == "Run" else b["name"]

    # Splits: group by exerciseIndex; the km counter restarts at every block.
    splits = []
    counter = {}
    for item in groups.get(10, []):
        ex = item.get("exerciseIndex")
        counter[ex] = counter.get(ex, 0) + 1
        splits.append((ex, _decode_split(item, counter[ex])))

    weather = data.get("weather") or {}
    rate = summary.get("standardRate")
    feel = data.get("sportFeelInfo") or {}
    devices = data.get("deviceList") or []
    # summary.avgSpeed switches meaning by sport: s/km for runs,
    # km/h x100 for rides (2640 -> 26.40 km/h, verified against the UI).
    raw_speed = summary.get("avgSpeed") or None
    totals = {
        "name": summary.get("name") or "activity",
        "sport_family": family,
        "is_run": family == 1,
        "is_ride": family == 2,
        "start_local": _local_start(summary),
        "device": devices[0].get("name") if devices else None,
        "distance_km": (summary.get("distance") or 0) / 100000,
        "moving_s": (summary.get("workoutTime") or 0) / 100,
        "elapsed_s": (summary.get("totalTime") or 0) / 100,
        "pause_s": (summary.get("pauseTime") or 0) / 100,
        "pace_s_per_km": raw_speed if family == 1 else None,
        "speed_kmh": raw_speed / 100 if family == 2 and raw_speed else None,
        "effort_pace_s_per_km": summary.get("adjustedPace") or None,
        "best_km_s_per_km": summary.get("bestKm") or None,
        "avg_hr": summary.get("avgHr") or None,
        "max_hr": summary.get("maxHr") or None,
        "avg_cadence": summary.get("avgCadence") or None,
        "avg_power": summary.get("avgPower") or None,
        "calories_kcal": round((summary.get("calories") or 0) / 1000),
        "training_load": summary.get("trainingLoad") or None,
        "aerobic_effect": summary.get("aerobicEffect"),
        "anaerobic_effect": summary.get("anaerobicEffect"),
        "vo2max": summary.get("currentVo2Max") or None,
        "standard_rate": None if rate is None or rate < 0 else rate,
        "temp_c": _tenths(weather.get("temperature")),
        "feels_c": _tenths(weather.get("bodyFeelTemp")),
        "humidity_pct": _tenths(weather.get("humidity")),
        "note": feel.get("sportNote") or None,
    }
    return {"totals": totals, "blocks": blocks, "splits": splits}


# ----------------------------------------------------------------- rendering

def _prescription_text(block):
    if not block["target_s"]:
        return None
    words = fmt_time(block["target_s"])
    intensity = block["intensity"]
    if intensity and intensity["kind"] == "hr_below":
        words += f", HR ≤ {intensity['bpm']} bpm"
    elif intensity and intensity["kind"] == "hr_range":
        words += f", HR {intensity['low_bpm']}–{intensity['high_bpm']} bpm"
    elif intensity and intensity["kind"] == "pace_range":
        words += (f" at {fmt_pace(intensity['low_s_per_km'])}"
                  f"–{fmt_pace(intensity['high_s_per_km'])}/km")
    if block["ended"] == "lap_key":
        words += f" — ended early by lap key at {fmt_time(block['time_s'])}"
    else:
        words += " — completed"
    return words


def _rate_text(rate):
    return f"{round(rate * 100)}%" if rate is not None else "—"


def _split_cell(split, as_speed=False):
    if as_speed:
        cell = f"{split['speed_kmh']:.1f}" if split["speed_kmh"] else "-"
    else:
        cell = fmt_pace(split["pace_s_per_km"])
    if split["avg_hr"]:
        cell += f" {split['avg_hr']}"
    if split["partial"]:
        cell += f" ({split['distance_km']:.2f} km)"
    return cell


def _chunked(cells, per_line=8):
    return [" · ".join(cells[i:i + per_line])
            for i in range(0, len(cells), per_line)]


def render_report(report):
    """Plain-text report, readable in a terminal and pasteable to a coach."""
    totals, blocks, splits = report["totals"], report["blocks"], report["splits"]
    lines = []

    start = totals["start_local"]
    when = start.strftime("%a %b %-d %Y, %-I:%M %p") if start else ""
    title = totals["name"].upper()
    lines.append(f"{title} — {when}" + (f"  ({totals['device']})" if totals["device"] else ""))

    pause = f" ({fmt_time(totals['elapsed_s'])} total, {fmt_time(totals['pause_s'])} paused)" \
        if totals["pause_s"] >= 1 else ""
    headline = [f"{totals['distance_km']:.2f} km",
                f"{fmt_time(totals['moving_s'])} moving{pause}"]
    if totals["pace_s_per_km"]:
        pace = f"{fmt_pace(totals['pace_s_per_km'])}/km"
        if totals["effort_pace_s_per_km"]:
            pace += f" (effort {fmt_pace(totals['effort_pace_s_per_km'])})"
        headline.append(pace)
    elif totals["speed_kmh"]:
        headline.append(f"{totals['speed_kmh']:.1f} km/h avg")
    if totals["avg_hr"]:
        headline.append(f"{totals['avg_hr']} bpm avg / {totals['max_hr']} max")
    lines.append(" · ".join(headline))

    extras = []
    if totals["avg_power"]:
        extras.append(f"{totals['avg_power']} W")
    if totals["avg_cadence"]:
        extras.append(f"{totals['avg_cadence']} "
                      + ("rpm" if totals["is_ride"] else "spm"))
    if totals["calories_kcal"]:
        extras.append(f"{totals['calories_kcal']} kcal")
    if totals["training_load"]:
        extras.append(f"load {totals['training_load']}")
    if totals["aerobic_effect"]:
        effect = f"aerobic {totals['aerobic_effect']:.1f}"
        if totals["anaerobic_effect"] is not None:
            effect += f" / anaerobic {totals['anaerobic_effect']:.1f}"
        extras.append(effect)
    if totals["vo2max"]:
        extras.append(f"VO2max {totals['vo2max']}")
    if totals["is_run"] and totals["best_km_s_per_km"]:
        extras.append(f"best km {_fmt_pace_trunc(totals['best_km_s_per_km'])}")
    if extras:
        lines.append(" · ".join(extras))

    if any(v for v in (totals["temp_c"], totals["feels_c"], totals["humidity_pct"])):
        weather = f"weather {_opt(totals['temp_c'], '.1f')}°C"
        if totals["feels_c"] is not None:
            weather += f" (feels {totals['feels_c']:.1f})"
        if totals["humidity_pct"] is not None:
            weather += f", humidity {totals['humidity_pct']:.0f}%"
        lines.append(weather)
    if totals["standard_rate"] is not None:
        lines.append(f"workout compliance {_rate_text(totals['standard_rate'])}"
                     " (time-weighted over programmed blocks)")
    if totals["note"]:
        lines.append(f"athlete note: {totals['note']}")

    if blocks and not (totals["is_run"] or totals["is_ride"]):
        lines.append("")
        lines.append(f"({len(blocks)} laps — lap detail is decoded for "
                     "runs and rides only)")
        blocks, splits = [], []

    if blocks:
        lines.append("")
        # A free run's auto-km laps arrive in the same type-2 group as real
        # program blocks; only a prescription makes them "blocks".
        lines.append("BLOCKS" if any(b["target_s"] for b in blocks) else "LAPS")
        for i, b in enumerate(blocks, 1):
            marker = f"{i:>2}" if b["is_program"] else " +"
            if totals["is_ride"]:
                effort = f"{_opt(b['speed_kmh'], '4.1f')} km/h"
            else:
                effort = f"{fmt_pace(b['pace_s_per_km']):>6}/km"
            row = (f"{marker}  {b['name']:<10} {b['distance_km']:5.2f} km  "
                   f"{fmt_time(b['time_s']):>7}  {effort}")
            if b["avg_hr"]:
                row += f"  {b['avg_hr']} bpm"
            if b["avg_cadence"]:
                row += f"  {b['avg_cadence']} " \
                       + ("rpm" if totals["is_ride"] else "spm")
            if b["avg_power"]:
                row += f"  {b['avg_power']} W"
            if b["standard_rate"] is not None:
                row += f"   in range {_rate_text(b['standard_rate'])}"
            lines.append(row)
            prescription = _prescription_text(b)
            if prescription:
                lines.append(f"   prescribed {prescription}")
            elif not b["is_program"]:
                lines.append("   after the program ended")

    block_splits = {}
    for ex, s in splits:
        block_splits.setdefault(ex, []).append(s)
    as_speed = totals["is_ride"]
    unit_note = " — km/h" if as_speed else ""
    if blocks and any(len(v) > 1 for v in block_splits.values()):
        lines.append("")
        lines.append(f"KM SPLITS (counter restarts each block{unit_note})")
        for b in blocks:
            rows = block_splits.get(b["exercise_index"], [])
            if not rows:
                continue
            label = b["name"] if b["is_program"] else "+"
            chunks = _chunked([_split_cell(s, as_speed) for s in rows])
            lines.append(f"{label:<10} {chunks[0]}")
            lines.extend(f"{'':<10} {chunk}" for chunk in chunks[1:])
    elif not blocks and len(splits) > 1:
        # Free run: no programmed blocks, so the splits are one flat sequence.
        lines.append("")
        lines.append(f"KM SPLITS{unit_note}")
        lines.extend(_chunked([_split_cell(s, as_speed) for _, s in splits]))

    form = [b for b in blocks if b["is_program"] and b["ground_time_ms"]]
    if form:
        lines.append("")
        lines.append("FORM (per block)")
        lines.append("ground contact "
                     + "/".join(_opt(b["ground_time_ms"], "d") for b in form) + " ms · "
                     "vert osc "
                     + "/".join(_opt(b["vertical_osc_cm"], ".1f") for b in form) + " cm · "
                     "stride "
                     + "/".join(_opt(b["stride_m"], ".2f") for b in form) + " m")

    return "\n".join(lines)
