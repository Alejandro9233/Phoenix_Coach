"""
The single choke point for plan persistence.

Every path that writes plan_json — generation, replan-remaining, adapt-today,
apply_issue, apply_recovery, apply_travel, profile re-enforce — calls
run_plan_write_pipeline() and persists exactly what it returns. The stage
order is fixed:

    [generate/retry] -> normalize_plan -> enforce_constraints
        -> volume_gate.audit -> [terminal repairs] -> finalize_plan_write

The bracketed stages run only on gated paths (a `generate` callable plus
`gate_ctx` supplied): the gate audits the enforced plan against the Python
budget (C3's volume_targets + phase hours), retries generation ONCE with
numeric feedback on hard violations, then repairs deterministically (strip
whole low-priority sessions, never the longest run) and persists. Soft
violations always persist, as week_summary.gate_warnings. The gate never
fails a request that generation itself survived — C2's pace enforcer slots in
right after the audit. Ungated paths (adapt-today's recovery downgrade,
profile re-enforce) skip the audit on purpose but still get their sums
restamped. test_plan_meta.py locks the choke point structurally: main.py and
issue_triage.py may not call enforce_constraints directly, and any function
that assigns plan_json must call the pipeline.

finalize_plan_write() cures two audit findings:

- Stale metadata: partial replans rewrote only {"days"}, so week_summary and
  the stored _context drifted from the plan they described. Every write now
  recomputes _context (DB + date math, no LLM) and stamps week_summary's
  expected_total_hours AND expected_run_km as the deterministic sums of the
  planned week (strength excluded from hours — gym time never counts). The
  LLM's numbers never survive; the week's TARGET lives separately in
  _context.volume_targets (single writer: C3).

- No provenance: after the 2026-08-17 template incident there was still no
  way to see which path wrote a week. Every write appends a receipt to
  plan_json["_revisions"]: when, which path, which days, why, what those days
  held before the rewrite (capture_before(), taken at mutation start so the
  receipt also shows what enforcement stripped), and the stripped violations
  themselves — gate repairs included. Kept to the last MAX_REVISIONS entries.
"""

from backend.services.plan_normalizer import VALID_DAYS, normalize_plan
from backend.services import volume_gate

REVISIONS_KEY = "_revisions"
MAX_REVISIONS = 40
GATE_ATTEMPTS = 2  # 1 initial + 1 retry with numeric feedback


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
                        violations, reason: str = None, before: dict = None,
                        gate_report=None, inputs: dict = None) -> dict:
    """Refresh derived metadata and append the write receipt.

    Runs after enforcement and the gate, so the receipt records what they
    stripped. Call through run_plan_write_pipeline unless a test needs this
    stage alone.
    """
    from backend.services.periodization_engine import PeriodizationEngine
    from backend.utils.timezone import get_local_now

    plan_json["_context"] = PeriodizationEngine().compute_context(db)

    # Single writer of the week_summary sums: the gate's accounting. These
    # are what the schedule adds up to; the target lives in _context.
    plan_json.setdefault("week_summary", {})
    plan_json["week_summary"]["expected_total_hours"] = round(
        volume_gate.planned_hours(plan_json), 1
    )
    plan_json["week_summary"]["expected_run_km"] = round(
        volume_gate.planned_run_km(plan_json), 1
    )
    if gate_report is not None:
        # Soft violations warn by design. A hard violation still standing
        # here survived retry AND repair (e.g. a travel rebuild that can't
        # fit the displaced km) — we persist anyway (never 502), so it must
        # be visible too.
        warnings = [v["detail"] for v in gate_report.soft + gate_report.hard]
        if warnings:
            plan_json["week_summary"]["gate_warnings"] = warnings
        else:
            plan_json["week_summary"].pop("gate_warnings", None)

    days_key = sorted(
        {d for d in (days_written or []) if d in VALID_DAYS},
        key=VALID_DAYS.index,
    )
    # "after" is captured here, where the final plan is in hand — exact and
    # free. Read-time reconstruction (chaining receipts) was rejected as the
    # kind of code that shows a confidently wrong diff. Legacy receipts
    # without it get a labeled fallback in the history feed.
    after = capture_before(plan_json, days=days_key)
    entry = {
        "at": get_local_now().isoformat(timespec="seconds"),
        "source": source,
        "days": days_key,
        "reason": reason,
        "before": {d: before[d] for d in days_key if d in before} if before else None,
        "after": after or None,
        "stripped": [
            {"day": v.get("day"), "title": v.get("title"), "reason": v.get("reason")}
            for v in (violations or [])
        ],
    }
    if inputs:
        # The scalar data this write was based on — what a same-day
        # "second opinion" compares against. Only adapt_today sets it.
        entry["inputs"] = inputs
    revisions = list(plan_json.get(REVISIONS_KEY) or [])
    revisions.append(entry)
    plan_json[REVISIONS_KEY] = revisions[-MAX_REVISIONS:]
    return plan_json


def run_plan_write_pipeline(db, plan_json: dict = None, *, source: str,
                            availability: dict, active_injuries, days=None,
                            reason: str = None, before: dict = None,
                            generate=None, gate_ctx: dict = None,
                            completed_run_km: float = 0.0,
                            completed_hours: float = 0.0,
                            required_run_km: float = None,
                            inputs: dict = None):
    """[generate] -> normalize -> enforce -> [gate] -> finalize.

    Returns (plan_json, violations). Two calling modes:

    - Ungated: pass `plan_json`. Normalize + enforce + finalize, as ever.
    - Gated: pass `generate` (a callable taking feedback-or-None and
      returning the FULL raw week dict, merges already applied — each call
      must merge into a fresh copy so a rejected attempt can't leak) plus
      `gate_ctx` (the training context). The gate audits completed + window
      totals; hard violations trigger one retry with numeric feedback, then
      deterministic repairs. A generation exception propagates to the caller
      (fail loud, persist nothing — 2026-08-17).

    `days` scopes enforcement, the audit window, and the receipt; None means
    the full week (only initial generation — every other path must scope to
    what it rewrote, past days are history).
    """
    from backend.services.constraint_enforcer import enforce_constraints

    gated = generate is not None and gate_ctx is not None
    attempts = GATE_ATTEMPTS if gated else 1
    feedback = None
    report = None

    for attempt in range(1, attempts + 1):
        raw = generate(feedback) if generate is not None else plan_json
        candidate = normalize_plan(raw)
        candidate, violations = enforce_constraints(
            candidate,
            availability=availability,
            active_injuries=active_injuries,
            days=days,
        )
        if not gated:
            break
        report = volume_gate.audit_plan(
            candidate, gate_ctx, days=days,
            availability=availability, active_injuries=active_injuries,
            completed_run_km=completed_run_km,
            completed_hours=completed_hours,
            required_run_km=required_run_km,
        )
        # Any actionable violation earns the one retry (soft floors included —
        # the LLM often can fix an undershoot); only hard ones block past it.
        # Advisory softs (under-target, long-run shortfall) surface as
        # gate_warnings without burning a ~7k-token regeneration — two
        # generations in one minute exceed the 8000 Groq TPM budget.
        actionable_soft = [v for v in report.soft if not v.get("advisory")]
        if (not report.hard and not actionable_soft) or attempt == attempts:
            break
        feedback = report.feedback_text()
        kinds = [v["kind"] for v in report.hard + report.soft]
        print(f"🔁 [{source}] Volume gate retry: {kinds}")

    if violations:
        print(f"🚧 [{source}] Stripped {len(violations)} constraint violation(s):")
        for v in violations:
            print(f"   - {v['day']}: {v['title']} — {v['reason']}")

    if report is not None and not report.ok:
        candidate, repairs = volume_gate.apply_terminal_repairs(
            candidate, report, days=days
        )
        if repairs:
            repair_names = [f"{r['day']}: {r['title']}" for r in repairs]
            print(f"✂️ [{source}] Gate repairs: {repair_names}")
            violations = list(violations) + repairs
            # Re-audit so the stamped sums and warnings describe the repaired
            # plan, not the rejected one.
            report = volume_gate.audit_plan(
                candidate, gate_ctx, days=days,
                availability=availability, active_injuries=active_injuries,
                completed_run_km=completed_run_km,
                completed_hours=completed_hours,
                required_run_km=required_run_km,
            )

    candidate = finalize_plan_write(
        db, candidate,
        source=source,
        days_written=days if days is not None else VALID_DAYS,
        violations=violations,
        reason=reason,
        before=before,
        gate_report=report,
        inputs=inputs,
    )

    # Pace enforcement rides the fresh _context finalize just computed, so
    # every path — gated or not, adapt-today included — stamps pace_target
    # without a second context build. Only touches workout fields; sums and
    # receipts above are unaffected.
    from backend.services.pace_enforcer import enforce_paces

    pace_model = (candidate.get("_context") or {}).get("pace_model")
    candidate, pace_fixes = enforce_paces(candidate, pace_model, days=days)
    if pace_fixes:
        print(f"🏃 [{source}] Pace targets set on {len(pace_fixes)} workout(s)")

    # Fuel lines ride the same slot: Python-computed carb/fluid bands stamped
    # on qualifying long runs, cleared when a replan shortens one. Workout
    # fields only — sums and receipts above are unaffected.
    from backend.models.database import Athlete
    from backend.services.fueling import stamp_fuel

    athlete = db.query(Athlete).first()
    candidate, fuel_changes = stamp_fuel(
        candidate, athlete.weight_kg if athlete else None, days=days
    )
    if fuel_changes:
        print(f"🥤 [{source}] Fuel lines updated on {len(fuel_changes)} workout(s)")

    # Step reconciliation (pace enforcer) can lengthen total_time AFTER the
    # sums were stamped above — re-stamp so week_summary describes the plan
    # actually persisted (review 2026-08-31). Deltas are small and upward-only,
    # so the audited bands stay honest; only the bookkeeping moves.
    if any("step_from" in c for c in pace_fixes):
        candidate.setdefault("week_summary", {})
        candidate["week_summary"]["expected_total_hours"] = round(
            volume_gate.planned_hours(candidate), 1)
        candidate["week_summary"]["expected_run_km"] = round(
            volume_gate.planned_run_km(candidate), 1)

    return candidate, violations
