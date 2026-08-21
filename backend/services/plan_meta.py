"""
The single choke point for plan persistence.

Every path that writes plan_json — generation, replan-remaining, adapt-today,
apply_issue, apply_recovery, apply_travel, profile re-enforce — calls
run_plan_write_pipeline() and persists exactly what it returns. The stage
order is fixed:

    normalize_plan -> enforce_constraints -> [volume gate] -> [pace enforcer]
        -> finalize_plan_write

The bracketed stages don't exist yet. When they land they slot in HERE, once,
instead of at six call sites — per-path ordering drift is how the
week_summary staleness bug happened. test_plan_meta.py locks this
structurally: main.py and issue_triage.py may not call enforce_constraints
directly, and any function that assigns plan_json must call the pipeline.

finalize_plan_write() cures two audit findings:

- Stale metadata: partial replans rewrote only {"days"}, so week_summary and
  the stored _context drifted from the plan they described. Every write now
  recomputes _context (DB + date math, no LLM) and expected_total_hours (the
  deterministic sum of planned durations, strength excluded — gym time never
  counts toward volume targets). expected_run_km is deliberately left alone:
  it is the week's protected run-km target, not a sum of what happens to be
  scheduled.

- No provenance: after the 2026-08-17 template incident there was still no
  way to see which path wrote a week. Every write appends a receipt to
  plan_json["_revisions"]: when, which path, which days, why, what those days
  held before the rewrite (capture_before(), taken at mutation start so the
  receipt also shows what enforcement stripped), and the stripped violations
  themselves. Kept to the last MAX_REVISIONS entries. iOS ignores unknown
  top-level keys, so the field is invisible until a screen wants it.
"""

from backend.services.plan_normalizer import VALID_DAYS, map_sport, normalize_plan

REVISIONS_KEY = "_revisions"
MAX_REVISIONS = 40


def capture_before(plan_json: dict, days=None) -> dict:
    """Snapshot what each day holds, BEFORE a rewrite touches it.

    Call on the normalized plan at mutation start — after any later stage has
    run, the "before" is already gone. Returns {day: [{sport, title,
    total_time}, ...]}; `days` limits the snapshot, None captures the week
    (finalize trims to the days actually written).
    """
    snapshot = {}
    for day_name, day in (plan_json.get("days") or {}).items():
        if days is not None and day_name not in days:
            continue
        if not isinstance(day, dict):
            continue
        snapshot[day_name] = [
            {
                "sport": w.get("sport"),
                "title": w.get("title"),
                "total_time": w.get("total_time"),
            }
            for w in (day.get("workouts") or [])
            if isinstance(w, dict)
        ]
    return snapshot


def finalize_plan_write(db, plan_json: dict, *, source: str, days_written,
                        violations, reason: str = None, before: dict = None) -> dict:
    """Refresh derived metadata and append the write receipt.

    Runs after enforcement (and, later, the gates) so the receipt records what
    they stripped. Call through run_plan_write_pipeline unless a test needs
    this stage alone.
    """
    from backend.services.compliance import _parse_duration_to_minutes
    from backend.services.periodization_engine import PeriodizationEngine
    from backend.utils.timezone import get_local_now

    plan_json["_context"] = PeriodizationEngine().compute_context(db)

    total_min = 0.0
    for day in (plan_json.get("days") or {}).values():
        if not isinstance(day, dict):
            continue
        for w in day.get("workouts") or []:
            if not isinstance(w, dict):
                continue
            if map_sport(w.get("sport") or "") in ("rest", "strength"):
                continue
            total_min += _parse_duration_to_minutes(w.get("total_time") or "") or 0
    plan_json.setdefault("week_summary", {})
    plan_json["week_summary"]["expected_total_hours"] = round(total_min / 60, 1)

    days_key = sorted(
        {d for d in (days_written or []) if d in VALID_DAYS},
        key=VALID_DAYS.index,
    )
    entry = {
        "at": get_local_now().isoformat(timespec="seconds"),
        "source": source,
        "days": days_key,
        "reason": reason,
        "before": {d: before[d] for d in days_key if d in before} if before else None,
        "stripped": [
            {"day": v.get("day"), "title": v.get("title"), "reason": v.get("reason")}
            for v in (violations or [])
        ],
    }
    revisions = list(plan_json.get(REVISIONS_KEY) or [])
    revisions.append(entry)
    plan_json[REVISIONS_KEY] = revisions[-MAX_REVISIONS:]
    return plan_json


def run_plan_write_pipeline(db, plan_json: dict, *, source: str, availability: dict,
                            active_injuries, days=None, reason: str = None,
                            before: dict = None):
    """normalize -> enforce -> finalize. Returns (plan_json, violations).

    `days` scopes enforcement and the receipt; None means the full week (only
    initial generation — every other path must scope to what it rewrote, past
    days are history).
    """
    from backend.services.constraint_enforcer import enforce_constraints

    plan_json = normalize_plan(plan_json)
    plan_json, violations = enforce_constraints(
        plan_json,
        availability=availability,
        active_injuries=active_injuries,
        days=days,
    )
    if violations:
        print(f"🚧 [{source}] Stripped {len(violations)} constraint violation(s):")
        for v in violations:
            print(f"   - {v['day']}: {v['title']} — {v['reason']}")

    # B1's volume gate slots in here; C2's pace enforcer right after it.

    plan_json = finalize_plan_write(
        db, plan_json,
        source=source,
        days_written=days if days is not None else VALID_DAYS,
        violations=violations,
        reason=reason,
        before=before,
    )
    return plan_json, violations
