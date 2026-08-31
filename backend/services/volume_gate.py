"""
Volume Gate — the numeric counterpart to constraint_enforcer.

WHY THIS EXISTS: expected_run_km / expected_total_hours were LLM-authored and
unvalidated; volume references were labeled "NOT hard limits"; nothing summed
the plan after generation on any persist path. The LLM could ship a 60 km week
into a 28-40 km phase and nothing counted it. Project rule: the LLM never
decides volume.

The gate runs inside run_plan_write_pipeline, AFTER enforce_constraints:

    normalize -> enforce_constraints -> AUDIT (this module) -> finalize

- The budget is Python-computed: C3's context["volume_targets"] owns the run-km
  target and hard cap (actuals-derived, ramp-capped); phase_hours_range owns
  the hours band. compute_budget only reads them.
- Over-ceiling is HARD: one retry with numeric feedback, then deterministic
  repair — whole low-priority sessions are stripped (extra cycling -> extra
  swimming -> shortest easy run), NEVER the week's longest run and never
  strength. Every strip carries enforced_reason.
- Under-floor is SOFT: retried once, then persisted with a visible
  week_summary.gate_warnings entry. The gate never 502s — "fail toward the
  athlete's plan surviving". Floors are feasibility-scaled by open run days
  and skipped entirely when an injury blocks running (an injured week
  legitimately under-runs).
- Hours exclude strength everywhere (gym time never counts toward volume).
- adapt-today is deliberately NOT gated: it is a single-day recovery-driven
  downgrade guarded by the deterministic recovery gate — reducing volume
  there is the feature, not a violation.

Import direction: response_agent may import this module (for the prompt's
budget line); this module must never import response_agent or plan_meta.
"""
import re
from dataclasses import dataclass, field
from datetime import datetime

from backend.services.constraint_enforcer import (
    DAY_ABBR,
    _injury_blocked_sports,
    _rest_workout,
    parse_day_list,
)
from backend.services.plan_normalizer import VALID_DAYS, map_sport

RUN_TARGET_SLACK_KM = 1.0   # under-target (soft) fires past this gap
LONG_RUN_SLACK_MIN = 5.0    # long-run shortfall (soft) fires past this

# Tolerances. The run-km hard cap already carries C3's headroom (ceiling*1.05
# or ramp cap), so it gets only an absolute grace for step rounding — not
# another multiplier.
RUN_CEILING_GRACE_KM = 2.0
RUN_FLOOR_FRAC = 0.90
HOURS_CEILING_FACTOR = 1.10
HOURS_FLOOR_FRAC = 0.90
# Travel acceptance (B4): post-rebuild run km >= 0.9 * required - 0.5.
TRAVEL_FLOOR_FRAC = 0.90
TRAVEL_GRACE_KM = 0.5

_KM_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(?:km|k)\b", re.I)
# Sports an Activity row may carry that count as gym time, not volume.
_STRENGTH_ACTIVITY_SPORTS = {"training", "strength", "gym"}

# B3: titles that are quality (hard) sessions regardless of step zones.
QUALITY_TITLES = {
    "Tempo Run", "Cruise Intervals", "VO2max Intervals", "Repetitions (R)",
    "Marathon Pace Long Run", "Progressive Run", "Sprint Intervals",
    "Sweet Spot Intervals", "Threshold Intervals", "CSS Threshold",
    "Sprint Set (swim)", "Race Simulation Brick", "Brick Session",
}
# B6: run-quality specifically — the phase priorities are about the run.
RUN_QUALITY_TITLES = {
    "Tempo Run", "Cruise Intervals", "Marathon Pace Long Run",
    "Progressive Run", "VO2max Intervals", "Repetitions (R)",
}
# B2: terminal downgrade target per sport — preserves volume, kills intensity.
_EASY_EQUIVALENT = {
    "running": "Easy Run",
    "cycling": "Endurance Ride (Z2)",
    "swimming": "Technique Session",
}

_PAREN_RE = re.compile(r"\([^)]*\)")
_COND_KM_RE = re.compile(r">\s*(\d+(?:\.\d+)?)\s*km", re.I)


def _norm_title(title: str) -> str:
    """Base form for menu matching: parentheticals (conditions, zone notes)
    and punctuation dropped, case and whitespace folded."""
    t = _PAREN_RE.sub(" ", title or "")
    t = re.sub(r"[^a-z0-9 ]", " ", t.lower())
    return " ".join(t.split())


def canonical_title(title: str, menu_entries) -> str | None:
    """Exact match (normalized) of an LLM title against menu vocabulary.
    Returns the menu's own string, or None. Deliberately no fuzzy matching —
    the prompt demands verbatim copies and the retry names the exact menu;
    add fuzziness only if retries actually thrash in practice."""
    base = _norm_title(title)
    if not base:
        return None
    for entry in menu_entries or []:
        if _norm_title(entry) == base:
            return entry
    return None


def is_quality(workout: dict) -> bool:
    """Quality = a known hard title, or any main step at zone 3+. Strides,
    openers and a Z2 long run are NOT quality (strides are the sanctioned
    recovery-week intensity; a long run is volume). Strength never counts."""
    sport = map_sport(workout.get("sport") or "")
    if sport in ("rest", "strength"):
        return False
    title = workout.get("title")
    if canonical_title(title, QUALITY_TITLES):
        return True
    # Explicitly NOT quality even with high-zone steps: strides are the
    # sanctioned recovery-week intensity, and a long run is volume.
    if canonical_title(title, {"Strides/Openers", "Long Run"}):
        return False
    for s in workout.get("steps") or []:
        if not isinstance(s, dict):
            continue
        zone = s.get("zone")
        if s.get("type") == "main" and isinstance(zone, (int, float)) and zone >= 3:
            return True
    return False


def parse_minutes(val) -> float | None:
    """Duration string -> minutes. Handles the normalizer's actual outputs:
    "45 min", "45:00" (MM:SS), "1:15:00" (H:MM:SS), "0:00", bare numbers."""
    if val is None:
        return None
    if isinstance(val, (int, float)):
        return float(val)
    s = str(val).strip().lower()
    if not s:
        return None
    m = re.match(r"^(\d+(?:\.\d+)?)\s*(?:min|minutes|m)?$", s)
    if m:
        return float(m.group(1))
    parts = s.split(":")
    try:
        nums = [float(p) for p in parts]
    except ValueError:
        return None
    if len(nums) == 2:
        # MM:SS — total_time is minutes-scale even when minutes > 90.
        return nums[0] + nums[1] / 60
    if len(nums) == 3:
        return nums[0] * 60 + nums[1] + nums[2] / 60
    return None


def workout_km(workout: dict) -> tuple:
    """(km, source) for one workout. Three tiers:
    declared (the distance_km contract field, pace-plausibility-checked for
    runs) -> parsed (km figures in title/step text, largest wins) ->
    estimated (running only: minutes / 6.0, never persisted as truth)."""
    sport = map_sport(workout.get("sport") or "")
    if sport in ("rest", "strength"):
        return 0.0, "none"
    minutes = parse_minutes(workout.get("total_time"))

    declared = workout.get("distance_km")
    if isinstance(declared, (int, float)) and declared > 0:
        plausible = True
        if sport == "running" and minutes:
            pace = minutes / float(declared)
            plausible = 2.5 <= pace <= 10.0
        if plausible:
            return float(declared), "declared"

    text_parts = [workout.get("title") or ""]
    for s in workout.get("steps") or []:
        if isinstance(s, dict):
            text_parts.append(s.get("description") or "")
    matches = _KM_RE.findall(" ".join(text_parts))
    if matches:
        km = max(float(m) for m in matches)
        if km > 0:
            return km, "parsed"

    if sport == "running" and minutes:
        return minutes / 6.0, "estimated"
    return 0.0, "none"


def _window_workouts(plan_json: dict, days=None):
    window = list(days) if days is not None else list(VALID_DAYS)
    for day_name in window:
        day = (plan_json.get("days") or {}).get(day_name)
        if not isinstance(day, dict):
            continue
        for w in day.get("workouts") or []:
            if isinstance(w, dict):
                yield day_name, w


def planned_run_km(plan_json: dict, days=None) -> float:
    return sum(
        workout_km(w)[0]
        for _, w in _window_workouts(plan_json, days)
        if map_sport(w.get("sport") or "") == "running"
    )


def planned_hours(plan_json: dict, days=None) -> float:
    """Planned hours EXCLUDING strength (gym time never counts)."""
    total_min = 0.0
    for _, w in _window_workouts(plan_json, days):
        if map_sport(w.get("sport") or "") in ("rest", "strength"):
            continue
        total_min += parse_minutes(w.get("total_time")) or 0.0
    return total_min / 60


def completed_week_actuals(db, start_of_week, through) -> tuple:
    """(run_km, hours_excl_strength) actually recorded from Monday through
    `through` (inclusive). The gate evaluates the WHOLE week — actuals plus
    the rewritten window — never a per-day proration."""
    from backend.models.database import Activity

    rows = db.query(Activity).filter(
        Activity.start_time >= datetime.combine(start_of_week, datetime.min.time()),
        Activity.start_time <= datetime.combine(through, datetime.max.time()),
    ).all()
    run_km = 0.0
    hours = 0.0
    for a in rows:
        sport = (a.sport or "").lower()
        if "run" in sport:
            run_km += (a.distance_m or 0) / 1000
        if sport not in _STRENGTH_ACTIVITY_SPORTS:
            hours += (a.duration_sec or 0) / 3600
    return round(run_km, 2), round(hours, 2)


def compute_budget(ctx: dict) -> dict | None:
    """The numbers the plan is graded on. C3 owns computing them; this only
    reads. Missing pieces degrade to None (that check is skipped) so stub
    profiles never crash the gate."""
    if not ctx:
        return None
    vt = ctx.get("volume_targets") or {}
    refs = ctx.get("volume_references") or {}

    hours_low = hours_high = None
    rng = refs.get("phase_hours_range")
    if isinstance(rng, str):
        m = re.match(r"^\s*(\d+(?:\.\d+)?)\s*-\s*(\d+(?:\.\d+)?)\s*$", rng)
        if m:
            hours_low, hours_high = float(m.group(1)), float(m.group(2))

    budget = {
        "run_km_target": vt.get("run_km_target"),
        "run_km_hard_cap": vt.get("run_km_hard_cap"),
        "hours_low": hours_low,
        "hours_high": hours_high,
        "long_run_minutes": vt.get("long_run_minutes"),
        "max_quality": refs.get("max_quality_sessions"),
        "phase_run_sessions": (refs.get("sport_sessions") or {})
        .get("running", {}).get("sessions"),
    }
    if all(v is None for v in budget.values()):
        return None
    return budget


@dataclass
class GateReport:
    hard: list = field(default_factory=list)
    soft: list = field(default_factory=list)
    metrics: dict = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.hard

    def feedback_text(self) -> str:
        lines = ["=== CORRECTIONS — YOUR PREVIOUS PLAN FAILED VALIDATION ==="]
        for v in self.hard + [s for s in self.soft if not s.get("advisory")]:
            lines.append(f"- {v['detail']}")
        lines.append("Fix ONLY these problems. Keep everything else the same.")
        return "\n".join(lines)


def _open_run_days(window, availability, active_injuries) -> int:
    """Days in the window where a run can actually happen: run-available,
    non-travel, and running not injury-blocked (blocked -> 0)."""
    avail = availability or {}
    if "running" in _injury_blocked_sports(active_injuries or []):
        return 0
    run_days = parse_day_list(avail.get("run_days"))
    travel = set(avail.get("travel_day_names") or [])
    count = 0
    for day in window:
        if day in travel:
            continue
        if run_days is not None and DAY_ABBR.get(day) not in run_days:
            continue
        count += 1
    return count


def _race_day_exempt(ctx: dict, day_name: str) -> bool:
    """The race itself is a fact, not a menu violation. On the goal race
    week's race day — and a tune-up's — the title and quality audits stand
    down for that one day; the rest of the week stays fully gated. Without
    this, the taper rule + off-menu-quality check would hard-fail the
    marathon the prompt itself scheduled and repair it into an Easy Run."""
    if not ctx:
        return False
    if ctx.get("race_week") and day_name == ctx.get("race_day_name"):
        return True
    tu = ctx.get("tuneup") or {}
    return bool(tu.get("is_race_week") and day_name == tu.get("race_day_name"))


def _audit_titles(plan_json: dict, ctx: dict, window, report: GateReport) -> None:
    """B2: workout titles must come from the phase menu — the forbidden list
    used to be prompt prose only ('Marathon Pace Long Run' shipped in base
    and nothing caught it). Strength stays LLM freedom (the menu says 'Coach
    decides the split'); unrecognized EASY titles pass silently (drift on
    easy volume is harmless), unrecognized QUALITY titles do not."""
    menu = ctx.get("workout_menu") or {}
    forbidden = ctx.get("forbidden_workouts") or []
    phase = ctx.get("phase")
    phase_name = ctx.get("phase_name") or phase or "this phase"
    if not menu and not forbidden:
        return

    for day_name, w in _window_workouts(plan_json, window):
        sport = map_sport(w.get("sport") or "")
        if sport in ("rest", "strength"):
            continue
        if _race_day_exempt(ctx, day_name):
            continue
        title = w.get("title") or ""
        km, _ = workout_km(w)

        # Forbidden list first. Entries may carry a km condition
        # ("Long Run (>15 km)") — those fire only past the threshold, and an
        # unmet condition means the base workout is explicitly sanctioned.
        conditionally_allowed = False
        forbidden_hit = None
        base = _norm_title(title)
        for entry in forbidden:
            if _norm_title(entry) != base:
                continue
            cond = _COND_KM_RE.search(entry)
            if cond and km <= float(cond.group(1)):
                conditionally_allowed = True
            else:
                forbidden_hit = entry
            break
        # Taper rule: any quality session beyond strides is off the table.
        if (forbidden_hit is None and phase == "taper"
                and is_quality(w)
                and _norm_title(title) != _norm_title("Strides/Openers")):
            forbidden_hit = title
        if forbidden_hit:
            allowed_list = ", ".join(menu.get(sport) or []) or "the menu"
            report.hard.append({
                "kind": "forbidden_title",
                "day": day_name,
                "title": title,
                "detail": (
                    f'"{title}" on {day_name} is FORBIDDEN in {phase_name}. '
                    f"Choose ONLY from: {allowed_list}."
                ),
            })
            continue
        if conditionally_allowed:
            continue

        sport_menu = menu.get(sport)
        if not sport_menu:
            # Sport filtered out by availability — the enforcer owns that.
            continue
        if canonical_title(title, sport_menu):
            continue
        if is_quality(w):
            report.hard.append({
                "kind": "forbidden_title",
                "day": day_name,
                "title": title,
                "detail": (
                    f'"{title}" on {day_name} is not on the {phase_name} '
                    f"menu. Copy titles VERBATIM from: "
                    f"{', '.join(sport_menu)}."
                ),
            })


def _audit_quality(plan_json: dict, ctx: dict, window, budget: dict,
                   availability, active_injuries, report: GateReport) -> None:
    """B3: max_quality_sessions is a hard whole-week count (locked days'
    planned quality included). Per-sport session counts stay advisory on
    purpose — sport_sessions is a coaching reference, and the availability
    enforcer already owns where sports may land. B6: phases whose point is
    one weekly quality run get a SOFT nudge when it's missing entirely,
    behind conservative escape hatches (recovery, taper, travel-compressed,
    injured, red recovery) — when in doubt, the check skips."""
    quality_sessions = [
        {"day": day_name, "title": w.get("title")}
        for day_name, w in _window_workouts(plan_json, None)
        if is_quality(w) and not _race_day_exempt(ctx, day_name)
    ]
    window_quality = [
        s for s in quality_sessions
        if s["day"] in window
    ]
    report.metrics["quality_sessions"] = len(quality_sessions)

    max_quality = budget.get("max_quality")
    if max_quality is not None and len(quality_sessions) > max_quality:
        listing = ", ".join(f"{s['title']} {s['day']}" for s in quality_sessions)
        report.hard.append({
            "kind": "quality_count",
            "detail": (
                f"{len(quality_sessions)} quality sessions ({listing}); max "
                f"is {max_quality} — convert the excess to easy sessions."
            ),
            "sessions": window_quality,
            "max": max_quality,
        })

    # B6 — quality floor, run quality specifically.
    phase = ctx.get("phase")
    recovery_status = (ctx.get("recovery") or {}).get("status")
    open_days = _open_run_days(list(VALID_DAYS), availability, active_injuries)
    if (phase in ("base", "build", "peak")
            and not ctx.get("is_recovery_week")
            and not ((ctx.get("tuneup") or {}).get("is_race_week"))
            and (max_quality or 0) >= 2
            and "running" not in _injury_blocked_sports(active_injuries or [])
            and open_days >= 2
            and recovery_status != "red"):
        has_run_quality = any(
            canonical_title(w.get("title"), RUN_QUALITY_TITLES)
            for _, w in _window_workouts(plan_json, None)
        )
        if not has_run_quality:
            phase_name = ctx.get("phase_name") or phase
            report.soft.append({
                "kind": "quality_missing",
                "detail": (
                    f"This {phase_name} week has no quality run. Phase "
                    f"guidance calls for 1 (e.g. Tempo Run). Add ONE quality "
                    f"run from the menu on a run-available day, keeping the "
                    f"total within {max_quality} quality sessions."
                ),
            })


def audit_plan(plan_json: dict, ctx: dict, *, days=None, availability=None,
               active_injuries=None, completed_run_km: float = 0.0,
               completed_hours: float = 0.0,
               required_run_km: float = None) -> GateReport:
    """Sum the enforced plan and compare against the Python budget.

    `days` is the rewritten window (same shape as enforce_constraints); the
    bands always compare completed + window totals — the whole week, never a
    proration. Hard violations block (retry, then repair); soft ones become
    week_summary.gate_warnings.
    """
    budget = compute_budget(ctx) or {}
    window = list(days) if days is not None else list(VALID_DAYS)

    window_run_km = planned_run_km(plan_json, window)
    window_hours = planned_hours(plan_json, window)
    week_run_km = completed_run_km + window_run_km
    week_hours = completed_hours + window_hours

    report = GateReport(metrics={
        "run_km_window": round(window_run_km, 1),
        "run_km_week": round(week_run_km, 1),
        "hours_window": round(window_hours, 1),
        "hours_week_excl_strength": round(week_hours, 1),
        "completed_run_km": round(completed_run_km, 1),
        "completed_hours": round(completed_hours, 1),
    })

    blocked = _injury_blocked_sports(active_injuries or [])
    open_days = _open_run_days(window, availability, active_injuries)

    cap = budget.get("run_km_hard_cap")
    if cap is not None and week_run_km > cap + RUN_CEILING_GRACE_KM:
        report.hard.append({
            "kind": "run_km_high",
            "detail": (
                f"Planned running totals {week_run_km:.1f} km this week "
                f"(including {completed_run_km:.1f} km already run); the hard "
                f"cap is {cap:.1f} km. Reduce planned running to at most "
                f"{max(0.0, cap - completed_run_km):.1f} km."
            ),
            "week_run_km": round(week_run_km, 1),
            "cap": cap,
        })

    target = budget.get("run_km_target")
    if (target is not None and "running" not in blocked and open_days > 0):
        sessions = budget.get("phase_run_sessions") or 4
        feasibility = min(1.0, open_days / max(1, sessions))
        floor = target * RUN_FLOOR_FRAC
        effective_floor = completed_run_km + max(0.0, floor - completed_run_km) * feasibility
        if week_run_km < effective_floor:
            report.soft.append({
                "kind": "run_km_low",
                "detail": (
                    f"Planned running totals {week_run_km:.1f} km this week; "
                    f"the target is {target:.1f} km (floor "
                    f"{effective_floor:.1f} km with {open_days} open run "
                    f"day(s)). Add running on open days if possible."
                ),
                "week_run_km": round(week_run_km, 1),
                "floor": round(effective_floor, 1),
                "target": target,
            })
        elif days is None and week_run_km < target - RUN_TARGET_SLACK_KM:
            # Full-week generations only: a mid-week replan is routinely
            # under target because days were already missed — that is the
            # athlete's history, not a plan defect (review 2026-08-31).
            # Above the floor but short of the target. Targets are
            # actuals-derived (best-of-3 x ramp), so habitually planning at
            # the floor quietly flattens the ramp — 2026-08-31 shipped 40.0
            # against 41.8 with 4.5 km of headroom, silently.
            report.soft.append({
                "kind": "run_km_under_target",
                "detail": (
                    f"Planned running totals {week_run_km:.1f} km; the "
                    f"target is {target:.1f} km. Add "
                    f"{target - week_run_km:.1f} km on open days — the "
                    f"ramp is derived from actuals, so under-planning "
                    f"compounds into next week's target."
                ),
                "week_run_km": round(week_run_km, 1),
                "target": target,
                "advisory": True,  # warns, never burns the gate retry
            })

    hours_high = budget.get("hours_high")
    if hours_high is not None and week_hours > hours_high * HOURS_CEILING_FACTOR:
        report.hard.append({
            "kind": "hours_high",
            "detail": (
                f"Planned training totals {week_hours:.1f} h this week "
                f"excluding strength; the ceiling is {hours_high:.1f} h. "
                f"Cut sessions to fit."
            ),
            "week_hours": round(week_hours, 1),
            "ceiling": hours_high,
        })

    # Race weeks (goal or tune-up): the run target is race-shaped but
    # phase_hours_range isn't, so a deliberately light race week would trip
    # the hours floor — and the race IS the week's quality, so the quality
    # nudge is spurious too. Both checks are soft-only; hatching them costs
    # nothing.
    race_week = bool(
        ctx and (ctx.get("race_week")
                 or (ctx.get("tuneup") or {}).get("is_race_week"))
    )

    hours_low = budget.get("hours_low")
    if (hours_low is not None and open_days > 0
            and not race_week
            and week_hours < hours_low * HOURS_FLOOR_FRAC):
        report.soft.append({
            "kind": "hours_low",
            "detail": (
                f"Planned training totals {week_hours:.1f} h excluding "
                f"strength; the phase guide is {hours_low:.1f}-"
                f"{hours_high:.1f} h."
            ),
            "week_hours": round(week_hours, 1),
            "floor": hours_low,
        })

    # The long run is the phase's stated priority and the number with the
    # least slack for the marathon goal, yet long_run_minutes was prompt-only
    # advisory — 80 vs 86 shipped silently (2026-08-31). Soft on purpose.
    lr_target = budget.get("long_run_minutes")
    if (days is None and lr_target and lr_target > 0 and not race_week
            and "running" not in blocked and open_days > 0):
        # Full-week generations only — in a windowed replan the short long
        # run may sit on a locked day, making the finding unfixable and the
        # feedback an invitation to plant a duplicate long run.
        longest_run_min = 0.0
        for _d, w in _window_workouts(plan_json, None):  # whole week
            if map_sport(w.get("sport") or "") == "running":
                longest_run_min = max(
                    longest_run_min, parse_minutes(w.get("total_time")) or 0.0)
        if longest_run_min < lr_target - LONG_RUN_SLACK_MIN:
            report.soft.append({
                "kind": "long_run_short",
                "detail": (
                    f"The week's longest run is {longest_run_min:.0f} min; "
                    f"the phase prescribes a {lr_target:.0f}-min long run. "
                    f"Lengthen the long run."
                ),
                "longest_run_min": round(longest_run_min, 1),
                "target_min": lr_target,
                "advisory": True,  # warns, never burns the gate retry
            })

    _audit_titles(plan_json, ctx or {}, window, report)
    _audit_quality(plan_json, ctx or {}, window, budget,
                   availability, active_injuries, report)

    # B4: travel rebuilds must preserve displaced run km. Compares the
    # rewritten window only — already-run km can't cover a future shortfall.
    if required_run_km is not None and required_run_km > 0:
        if window_run_km < required_run_km * TRAVEL_FLOOR_FRAC - TRAVEL_GRACE_KM:
            report.hard.append({
                "kind": "travel_run_km",
                "detail": (
                    f"The rebuild schedules only {window_run_km:.1f} km of "
                    f"running on the open days; at least "
                    f"{required_run_km:.1f} km must be preserved. Move run "
                    f"sessions (especially the long run) to open days and "
                    f"drop strength or cycling instead."
                ),
                "week_run_km": round(week_run_km, 1),
                "required": round(required_run_km, 1),
            })

    return report


def _strippable(plan_json: dict, days) -> list:
    """(day, workout, km, minutes) candidates for terminal repair, in strip
    priority order: extra cycling -> extra swimming -> shortest easy runs.
    Never the week's longest run (protected), never strength or rest."""
    max_run_km = 0.0
    max_run_ref = None
    entries = []
    for day_name, w in _window_workouts(plan_json, days):
        sport = map_sport(w.get("sport") or "")
        if sport in ("rest", "strength"):
            continue
        km, _ = workout_km(w)
        minutes = parse_minutes(w.get("total_time")) or 0.0
        entries.append((day_name, w, sport, km, minutes))
    # The longest run of the WHOLE week is protected, even outside the window.
    for _, w in _window_workouts(plan_json, None):
        if map_sport(w.get("sport") or "") == "running":
            km, _ = workout_km(w)
            if km > max_run_km:
                max_run_km, max_run_ref = km, id(w)

    order = {"cycling": 0, "swimming": 1, "running": 2}
    candidates = [
        e for e in entries
        if not (e[2] == "running" and id(e[1]) == max_run_ref)
    ]
    candidates.sort(key=lambda e: (order.get(e[2], 3), e[3], e[4]))
    return candidates


def _downgrade_to_easy(workout: dict, sport: str, reason: str) -> None:
    """B2's terminal move: same duration and distance, intensity gone. A
    downgrade is safe by construction where a strip could silently delete
    protected run km."""
    workout["title"] = _EASY_EQUIVALENT.get(sport, "Easy Run")
    for s in workout.get("steps") or []:
        if isinstance(s, dict) and isinstance(s.get("zone"), (int, float)):
            s["zone"] = min(s["zone"], 2)
    workout["enforced_reason"] = reason


def _find_workout(plan_json: dict, day_name: str, title: str):
    day = (plan_json.get("days") or {}).get(day_name) or {}
    for w in day.get("workouts") or []:
        if isinstance(w, dict) and w.get("title") == title:
            return w
    return None


def apply_terminal_repairs(plan_json: dict, report: GateReport, days=None) -> tuple:
    """Deterministic fix after the retry also failed. Ceilings strip whole
    sessions until under budget; bad titles and excess quality DOWNGRADE to
    the easy equivalent (keeps the km, kills the intensity) — never a bare
    strip, because stripping a mis-titled long run would silently delete
    protected run km. Floors are never repaired (they become gate_warnings).
    Returns (plan_json, repairs) — receipt-style violation dicts."""
    repairs = []

    # B2: forbidden / off-menu titles -> phase easy equivalent.
    for v in report.hard:
        if v["kind"] != "forbidden_title":
            continue
        w = _find_workout(plan_json, v["day"], v["title"])
        if w is None:
            continue
        sport = map_sport(w.get("sport") or "")
        reason = f"Downgraded: {v['title']} is not allowed here"
        _downgrade_to_easy(w, sport, reason)
        repairs.append({"day": v["day"], "title": v["title"], "reason": reason})

    # B3: excess quality -> downgrade latest-first within the window,
    # long-run-family sessions last (they carry the week's protected km).
    for v in report.hard:
        if v["kind"] != "quality_count":
            continue
        excess = report.metrics.get("quality_sessions", 0) - v["max"]
        # Downgrade order: non-long-run sessions first, latest in the week
        # first — the earliest quality and the long-run family survive.
        candidates = sorted(
            v["sessions"],
            key=lambda s: (
                canonical_title(s["title"], {"Marathon Pace Long Run", "Long Run"})
                is not None,
                -(VALID_DAYS.index(s["day"]) if s["day"] in VALID_DAYS else 0),
            ),
        )
        for s in candidates:
            if excess <= 0:
                break
            w = _find_workout(plan_json, s["day"], s["title"])
            if w is None or not is_quality(w):
                continue
            reason = "Downgraded: over the weekly quality-session cap"
            _downgrade_to_easy(w, map_sport(w.get("sport") or ""), reason)
            repairs.append({"day": s["day"], "title": s["title"], "reason": reason})
            excess -= 1

    hard_kinds = {v["kind"] for v in report.hard}
    if not hard_kinds & {"run_km_high", "hours_high"}:
        return plan_json, repairs

    over_run = next((v for v in report.hard if v["kind"] == "run_km_high"), None)
    over_hours = next((v for v in report.hard if v["kind"] == "hours_high"), None)

    excess_km = (over_run["week_run_km"] - over_run["cap"]) if over_run else 0.0
    excess_hours = (
        over_hours["week_hours"] - over_hours["ceiling"] * HOURS_CEILING_FACTOR
        if over_hours else 0.0
    )

    for day_name, w, sport, km, minutes in _strippable(plan_json, days):
        if excess_km <= 0 and excess_hours <= 0:
            break
        helps_km = sport == "running" and excess_km > 0
        helps_hours = excess_hours > 0
        if not (helps_km or helps_hours):
            continue
        day = plan_json["days"][day_name]
        reason = "Removed: weekly volume over budget (system trim)"
        day["workouts"] = [x for x in day["workouts"] if x is not w]
        if not day["workouts"]:
            day["workouts"] = [_rest_workout(reason)]
            day["summary"] = "Rest — volume over budget"
        repairs.append({"day": day_name, "title": w.get("title"), "reason": reason})
        if sport == "running":
            excess_km -= km
        excess_hours -= minutes / 60

    return plan_json, repairs
