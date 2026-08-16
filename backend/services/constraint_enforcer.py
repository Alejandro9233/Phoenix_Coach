"""
Constraint Enforcer — the hard gate between the LLM's plan and what ships.

WHY THIS EXISTS: availability (swim_days/bike_days/run_days/strength_days) used to
live only as text inside the prompt. `periodization_engine.py` claimed it was
enforced; it was not. The LLM was asked politely not to put strength on a
non-strength day, and when it did anyway nothing caught it — a user who removed
Sunday from strength_days and hit /weekly-plan/replan-remaining still got a
Sunday strength session. Same hole for injuries: `data_agent` labelled them
"ACTIVE INJURIES (CRITICAL)" in the summary and then trusted the model to act on
it.

Project rule: Python = GPS, LLM = Coach. A constraint that only exists in a
prompt is a suggestion. Every path that produces or mutates plan JSON must run
it through `enforce_constraints` before persisting.

Two constraint sources, both hard:
  - availability: the athlete's weekday schedule per sport
  - injuries: InjuryLog rows with status "Active", via `affected_sports`

A violating workout is removed. A day emptied by removals becomes an explicit
rest day carrying the reason, so the athlete sees why the session vanished
instead of finding a silent blank.
"""
from typing import Any, Iterable, Optional

from backend.services.plan_normalizer import VALID_DAYS, map_sport

# Weekday abbreviations as stored in Athlete.swim_days et al ("mon,wed,fri").
DAY_ABBR = {
    "Monday": "mon",
    "Tuesday": "tue",
    "Wednesday": "wed",
    "Thursday": "thu",
    "Friday": "fri",
    "Saturday": "sat",
    "Sunday": "sun",
}

# Canonical sport (from map_sport) -> the Athlete column holding its allowed days.
SPORT_TO_AVAILABILITY_KEY = {
    "swimming": "swim_days",
    "cycling": "bike_days",
    "running": "run_days",
    "strength": "strength_days",
}

# Canonical sport -> tokens that may appear in InjuryLog.affected_sports.
# Stored free-form ("run,bike"), so match generously.
SPORT_TO_INJURY_TOKENS = {
    "swimming": {"swim", "swimming"},
    "cycling": {"bike", "biking", "cycle", "cycling"},
    "running": {"run", "running"},
    "strength": {"strength", "gym", "lifting", "weights"},
}


def parse_day_list(value: Optional[str]) -> Optional[set]:
    """
    Parse "mon,wed,fri" into {"mon","wed","fri"}.

    Returns None when the field is None — meaning "no constraint recorded", which
    is different from an empty string. An empty string means "never", and that
    must be honoured: an athlete who clears every strength day wants no strength.
    """
    if value is None:
        return None
    return {d.strip().lower()[:3] for d in value.split(",") if d.strip()}


def _injury_blocked_sports(active_injuries: Iterable[Any]) -> dict:
    """
    Map canonical sport -> reason string, for sports blocked by an active injury.

    An injury with no `affected_sports` blocks nothing on its own. Guessing which
    sports a body part rules out is a coaching judgement, not arithmetic, so it
    stays with the LLM — this function only enforces what was explicitly recorded.
    """
    blocked = {}
    for inj in active_injuries or []:
        raw = getattr(inj, "affected_sports", None)
        if not raw:
            continue
        tokens = {t.strip().lower() for t in raw.split(",") if t.strip()}
        body_part = getattr(inj, "body_part", None) or "injury"
        severity = getattr(inj, "severity", None)
        detail = f"{body_part}" + (f" (severity {severity}/10)" if severity else "")
        for sport, aliases in SPORT_TO_INJURY_TOKENS.items():
            if tokens & aliases:
                blocked[sport] = detail
    return blocked


def _rest_workout(reason: str) -> dict:
    return {
        "sport": "rest",
        "title": "Rest",
        "steps": [],
        "total_time": "00:00",
        "hr_target": None,
        "enforced_reason": reason,
    }


def enforce_constraints(
    plan_json: dict,
    availability: Optional[dict] = None,
    active_injuries: Optional[Iterable[Any]] = None,
    days: Optional[Iterable[str]] = None,
) -> tuple:
    """
    Strip workouts that violate availability or an active injury.

    Args:
        plan_json: normalized plan, `{"days": {"Monday": {...}, ...}}`. Mutated in place.
        availability: `{"swim_days": "wed,sat,sun", ...}`. A missing or None entry
            means unconstrained for that sport.
        active_injuries: InjuryLog rows with status "Active".
        days: restrict enforcement to these day names. Defaults to all seven —
            pass the replanned subset so a mid-week change never rewrites history.

    Returns:
        `(plan_json, violations)` where each violation is
        `{"day", "sport", "title", "reason"}`. An empty list means the plan was
        already clean.
    """
    availability = availability or {}
    injury_blocks = _injury_blocked_sports(active_injuries)
    target_days = list(days) if days is not None else list(VALID_DAYS)

    days_dict = plan_json.get("days") or {}
    violations = []

    for day_name in target_days:
        day = days_dict.get(day_name)
        if not isinstance(day, dict):
            continue

        workouts = day.get("workouts") or []
        kept = []

        for workout in workouts:
            sport = map_sport(workout.get("sport", ""))

            # Rest is always permissible — it is the fallback, never a violation.
            if sport == "rest":
                kept.append(workout)
                continue

            title = workout.get("title") or sport

            injury_reason = injury_blocks.get(sport)
            if injury_reason:
                violations.append({
                    "day": day_name,
                    "sport": sport,
                    "title": title,
                    "reason": f"Active injury: {injury_reason} rules out {sport}",
                })
                continue

            allowed = parse_day_list(availability.get(SPORT_TO_AVAILABILITY_KEY.get(sport)))
            if allowed is not None and DAY_ABBR[day_name] not in allowed:
                allowed_label = ", ".join(sorted(allowed)) if allowed else "no days"
                violations.append({
                    "day": day_name,
                    "sport": sport,
                    "title": title,
                    "reason": f"{sport.capitalize()} is not available on {day_name} (allowed: {allowed_label})",
                })
                continue

            kept.append(workout)

        if len(kept) == len(workouts):
            continue

        day_reasons = [v["reason"] for v in violations if v["day"] == day_name]
        if not kept:
            kept = [_rest_workout("; ".join(day_reasons))]
            day["summary"] = "Rest — scheduled session removed"

        day["workouts"] = kept
        # Surfaced to the client so a vanished session is explained, not silent.
        day["enforcement_notes"] = day_reasons
        days_dict[day_name] = day

    plan_json["days"] = days_dict
    return plan_json, violations


def get_active_injuries(db, athlete_id: int) -> list:
    """
    Injuries currently blocking training, auto-resolving anything past its date.

    Every enforcement path reads through here, so expiry is handled once. Without
    it a three-day calf niggle would keep running blocked forever — the athlete
    would have to remember to reopen Profile and mark it Resolved, and the one
    thing we know about mid-week reporting is that they won't.

    `expected_recovery_date` is NULL for injuries logged before this existed and
    for anything entered by hand; those keep the old behaviour of lasting until
    explicitly resolved.
    """
    from backend.models.database import InjuryLog
    from backend.utils.timezone import get_local_today

    injuries = db.query(InjuryLog).filter(
        InjuryLog.athlete_id == athlete_id,
        InjuryLog.status == "Active",
    ).all()

    today = get_local_today()
    still_active = []
    expired = []
    for inj in injuries:
        if inj.expected_recovery_date and inj.expected_recovery_date < today:
            inj.status = "Recovering"
            expired.append(inj)
        else:
            still_active.append(inj)

    if expired:
        # "Recovering" rather than "Resolved" — the window elapsing is evidence
        # the plan can resume, not evidence the body part is fine. The athlete
        # closes it out in Profile.
        db.commit()
        for inj in expired:
            print(f"🩹 Injury #{inj.id} ({inj.body_part}) passed its recovery date → Recovering")

    return still_active
