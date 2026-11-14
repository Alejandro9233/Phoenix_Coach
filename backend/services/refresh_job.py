"""
The smart refresh: pull COROS -> ingest -> judge recovery -> adapt today.

run_smart_refresh is the whole morning action. The phone starts it with
start() and polls status(); `job` is the one in-process record of the run
(single uvicorn process, so a module dict is the whole story).

Lived in main.py until 2026-11-14. It reaches the plan through
`plan_flows.<name>` so tests patch one place.
"""
import asyncio

from sqlalchemy.orm import Session

from backend.models.database import Activity, Athlete, RecoverySnapshot, week_plan
from backend.services import coros_mcp, coros_watch_sync, plan_flows
from backend.services.coros_scraper import CorosScraper
from backend.utils.timezone import get_local_today


def _load_ratio_label(ratio):
    """Convert load ratio to a human-readable label."""
    if ratio is None:
        return "Unknown"
    if ratio < 0.5:
        return "Detraining"
    elif ratio < 0.8:
        return "Low"
    elif ratio <= 1.0:
        return "Optimal"
    elif ratio <= 1.3:
        return "Productive"
    elif ratio <= 1.5:
        return "High"
    else:
        return "Danger"


def _backfill_days_needed(db) -> int:
    """90 when the local activity history is too shallow to trust, else 0.

    The scraper's list page reaches back ~7 activities, so a fresh or wiped
    DB under-reports every historical window (last week, progression math).
    Shallow = earliest activity missing or younger than 28 days. A new
    athlete whose COROS history genuinely starts <28d ago keeps returning 90
    — a harmless few extra pages per morning scrape, capped in the scraper.
    """
    from datetime import timedelta

    from sqlalchemy import func

    from backend.models.database import Activity

    earliest = db.query(func.min(Activity.start_time)).scalar()
    if earliest is None or earliest.date() > get_local_today() - timedelta(days=28):
        return 90
    return 0


async def pull_coros(db: Session, report, backfill_days: int, source: dict):
    """COROS data for one refresh: the official MCP first, the Playwright
    scraper only when the MCP is off, fails, or parses today only partially.

    Alex's decision after the 2026-10-07 council: no shadow period — the
    MCP is seconds instead of minutes and carries sleep, stress and real
    start times the scraper never had. The council's warning for this mode
    was wrong-but-present values; the MCP service answers with plausibility
    bounds (None, never 0) and a `partial` verdict for today that routes the
    whole pull to the scraper. Fills `source` in place (so it survives a
    failing fallback scrape) and returns the payload; the record rides the
    refresh event as `coros_source`.
    """
    from datetime import datetime, time as time_type, timedelta

    from backend.models.database import Activity

    source.update({"source": "mcp", "fallback_reason": None, "elapsed_ms": None,
                   "calls": None, "today_status": None, "missing": []})
    if coros_mcp.enabled():
        report("Syncing COROS...")
        try:
            days = 90 if backfill_days else 10
            since = datetime.combine(get_local_today() - timedelta(days=days + 1), time_type.min)
            known = {row[0] for row in db.query(Activity.id).filter(Activity.start_time >= since).all()}
            # Rows from before laps were fetched (or whose fetch failed) get
            # them a few per morning, newest first — 90 days back, the span
            # the strength log and chat read. {} marks "fetched, nothing".
            lap_since = datetime.combine(get_local_today() - timedelta(days=90), time_type.min)
            lapless = [tuple(row) for row in db.query(Activity.id, Activity.sport_code).filter(
                Activity.start_time >= lap_since, Activity.lap_data.is_(None),
                Activity.sport_code.in_(coros_mcp.LAP_SPORT_TYPES + coros_mcp.STRENGTH_SPORT_TYPES),
            ).order_by(Activity.start_time.desc()).limit(coros_mcp.LAP_BACKFILL_PER_PULL).all()]
            data = await asyncio.wait_for(
                asyncio.to_thread(coros_mcp.fetch_scrape_shaped, days, known, lap_backfill=lapless),
                timeout=120)
            source.update(elapsed_ms=data.get("elapsed_ms"), calls=data.get("calls"),
                          today_status=data.get("today_status"), missing=data.get("missing") or [])
            if data.get("today_status") == "partial":
                raise coros_mcp.CorosMcpError(
                    f"today parsed partially, missing {', '.join(data.get('missing') or [])}")
            print(f"COROS via MCP: {len(data.get('activities') or [])} activities, "
                  f"{data.get('calls')} calls, {data.get('elapsed_ms')} ms, today {data.get('today_status')}")
            return data
        except Exception as e:  # asyncio.TimeoutError included
            source["source"] = "scraper"
            source["fallback_reason"] = f"{type(e).__name__}: {e}"[:200]
            print(f"COROS MCP failed ({source['fallback_reason']}) — falling back to the scraper")
    else:
        source["source"] = "scraper"
        source["fallback_reason"] = "mcp not enabled on this host (no token)"

    report("Scraping COROS...")
    return await CorosScraper().scrape_all(backfill_days=backfill_days)


async def run_smart_refresh(db: Session, progress=None):
    """Scrape → ingest → evaluate recovery → auto-adapt. Shared by the
    synchronous endpoint above and the background job below. `progress`
    receives human-readable stage strings for the phone's status pill."""
    from backend.services.ingestion_service import IngestionService

    def report(stage):
        if progress:
            progress(stage)

    sync_status = "ok"
    sync_message = ""
    new_activity_ids = []

    # 1. Pull COROS — MCP first, scraper as fallback — with history backfill
    # when the DB is shallow, so a wiped DB self-heals on the next refresh.
    coros_source = {}
    try:
        data = await pull_coros(db, report, _backfill_days_needed(db), coros_source)
        service = IngestionService()
        new_activity_ids = service.ingest_coros_data(data) or []
        # A scrape can finish without the payloads ingestion needs (cold CPU,
        # COROS hiccup). Say so instead of claiming a clean sync — the phone
        # shows this string.
        missing = data.get("missing") or []
        if missing:
            sync_status = "partial"
            sync_message = f"Synced, but missing: {', '.join(missing)}."
        elif coros_source["source"] == "mcp":
            sync_message = "Biometrics synced."
        else:
            sync_message = "Biometrics synced (via scraper)."
    except Exception as e:
        sync_status = "partial"
        sync_message = f"Scraper error: {str(e)}. Using cached data."

    # A dead COROS login falls back to the scraper quietly and the watch push
    # and sleep data stop. The phone only alerts on "partial", so say it.
    dead_auth = coros_mcp.dead_auth_message(coros_source)
    if dead_auth:
        sync_status = "partial"
        sync_message = dead_auth

    # 2. Get latest recovery snapshot
    report("Evaluating recovery...")
    latest = db.query(RecoverySnapshot).order_by(RecoverySnapshot.date.desc()).first()

    # --- Staleness guard: skip adaptation if recovery data is not from today ---
    from datetime import date as date_type
    recovery_data_stale = False
    stale_reason = None
    if latest:
        snap_date = latest.date if isinstance(latest.date, date_type) else latest.date.date() if hasattr(latest.date, 'date') else latest.date
        today_date = get_local_today()
        if snap_date != today_date:
            recovery_data_stale = True
            stale_reason = f"Latest snapshot is from {snap_date}, not today ({today_date})"
            print(f"⚠️ Staleness guard: {stale_reason}. Skipping adaptation.")
    else:
        recovery_data_stale = True
        stale_reason = "No recovery snapshots found"

    # 3. Deterministic recovery evaluation (only if data is fresh). Every
    # trigger is recorded with its value and threshold, fired or clear — most
    # refreshes adapt nothing, and "why did nothing happen" is half of what
    # the History debrief exists to answer.
    needs_adaptation = False
    adaptation_reasons = []
    triggers = []

    def _trigger(name, fired, value, threshold):
        triggers.append({
            "name": name, "fired": bool(fired),
            "value": value, "threshold": threshold,
        })

    if latest and not recovery_data_stale:
        athlete = db.query(Athlete).first()
        # HRV check — COROS's own normal range when the pull carried it
        # (MCP path, or the scraper's sleepHrvIntervalList), else the legacy
        # −15% vs the stored baseline. The band re-centers with COROS's
        # rolling baseline, so a regime change (altitude, illness) stops
        # firing as the athlete adapts; the fixed baseline fired on 6 of the
        # 7 refreshes after the 2026-09 move (docs/COROS_MCP.md).
        if latest.hrv_ms and latest.hrv_normal_low:
            fired = latest.hrv_ms < latest.hrv_normal_low
            _trigger("hrv_drop", fired, round(latest.hrv_ms), round(latest.hrv_normal_low))
            if fired:
                needs_adaptation = True
                hi = f"–{latest.hrv_normal_high:.0f}" if latest.hrv_normal_high else ""
                adaptation_reasons.append(
                    f"HRV {latest.hrv_ms:.0f} ms below COROS normal range "
                    f"({latest.hrv_normal_low:.0f}{hi} ms)")
        elif latest.hrv_ms and athlete and athlete.hrv_baseline:
            hrv_drop_pct = (latest.hrv_ms - athlete.hrv_baseline) / athlete.hrv_baseline * 100
            fired = hrv_drop_pct < -15
            _trigger("hrv_drop", fired, round(hrv_drop_pct, 1), -15)
            if fired:
                needs_adaptation = True
                adaptation_reasons.append(f"HRV {hrv_drop_pct:.0f}% below baseline")
        else:
            _trigger("hrv_drop", False, None, -15)
        # RHR check
        rhr_snapshots = db.query(RecoverySnapshot).order_by(RecoverySnapshot.date.desc()).limit(7).all()
        rhr_values = [s.resting_hr for s in rhr_snapshots if s.resting_hr]
        if rhr_values and latest.resting_hr:
            avg_rhr = sum(rhr_values) / len(rhr_values)
            fired = latest.resting_hr > avg_rhr + 5
            _trigger("rhr_elevated", fired, latest.resting_hr, round(avg_rhr + 5, 1))
            if fired:
                needs_adaptation = True
                adaptation_reasons.append(f"RHR elevated: {latest.resting_hr} vs avg {avg_rhr:.0f}")
        else:
            _trigger("rhr_elevated", False, None, None)
        # Fatigue state check
        fired = bool(latest.fatigue_state and latest.fatigue_state >= 4)
        _trigger("fatigue_high", fired, latest.fatigue_state, 4)
        if fired:
            needs_adaptation = True
            fatigue_labels = {4: "Fatigued", 5: "Overreaching"}
            adaptation_reasons.append(f"Fatigue zone: {fatigue_labels.get(latest.fatigue_state, 'High')}")
        # Load ratio check
        fired = bool(latest.load_ratio and latest.load_ratio > 1.4)
        _trigger("load_ratio_high", fired,
                 round(latest.load_ratio, 2) if latest.load_ratio is not None else None, 1.4)
        if fired:
            needs_adaptation = True
            adaptation_reasons.append(f"Load ratio {latest.load_ratio:.2f} — high injury risk")
        # TIB check
        fired = latest.tib is not None and latest.tib < -20
        _trigger("tib_low", fired,
                 round(latest.tib, 1) if latest.tib is not None else None, -20)
        if fired:
            needs_adaptation = True
            adaptation_reasons.append(f"Form (TIB) at {latest.tib:.0f} — deep fatigue")

    # 4. Auto-adapt if needed. The failure is no longer swallowed silently —
    # it rides the event as adaptation.error; the refresh itself still
    # succeeds (a failed adapt must never fail the sync).
    adapted = False
    adapt_error = None
    adapt_receipt_at = None
    adapt_week_start = None
    adapt_skip_reason = None
    # Completion gate (plan_flows.adapt_today_workout enforces it too, but the event
    # must say WHY nothing happened — that's half of History's job): once
    # today's training is done, the fatigue in this sync came from that
    # training, and there is nothing left today to adapt.
    if needs_adaptation and plan_flows.todays_training_done(db):
        adapt_skip_reason = "Today's training is already done — its load is what tripped the triggers."
        print(f"⛔ Completion gate: {adapt_skip_reason}")
    elif needs_adaptation and plan_flows.hand_set_today():
        # A hand-written week is not the LLM's to rewrite on a bad morning.
        # The athlete can still ask for it with Adaptar hoy (manual path).
        adapt_skip_reason = "Semana escrita a mano: no se adapta sola. Usa Adaptar hoy si la quieres cambiar."
        print(f"⛔ Hand-set week: {adapt_skip_reason}")
    elif needs_adaptation:
        try:
            report("Adapting today's plan...")
            from datetime import timedelta as _td
            today_local = get_local_today()
            week_start_local = today_local - _td(days=today_local.weekday())

            # plan_flows.adapt_today_workout has a same-day idempotency guard that
            # returns WITHOUT writing. Claim adapted (and the receipt join
            # key) only when a NEW adapt receipt actually appeared — else the
            # afternoon's no-op refresh would durably claim the morning's
            # plan change as its own and corrupt the History fold.
            def _last_adapt_at():
                plan_row = week_plan(db, week_start_local)
                revs = ((plan_row.plan_json or {}).get("_revisions") or []) if plan_row else []
                if revs and revs[-1].get("source") == "adapt_today":
                    return revs[-1].get("at")
                return None

            before_at = _last_adapt_at()
            # Off the event loop: the adapt's Groq call takes seconds, and on
            # the loop it froze every other request until it returned.
            await asyncio.to_thread(plan_flows.adapt_today_workout, body=None, db=db)
            after_at = _last_adapt_at()
            if after_at is not None and after_at != before_at:
                adapted = True
                adapt_week_start = week_start_local
                adapt_receipt_at = after_at
                print(f"🔄 Auto-adapted today's workout. Reasons: {adaptation_reasons}")
            else:
                print("ℹ️ Adaptation needed but already adapted today — no new write.")
        except Exception as e:
            print(f"⚠️ Auto-adaptation failed: {e}")
            adapted = False
            adapt_error = str(e)

    # 5. Build response
    recovery_summary = {
        "hrv_ms": latest.hrv_ms if latest else None,
        "hrv_normal_low": latest.hrv_normal_low if latest else None,
        "hrv_normal_high": latest.hrv_normal_high if latest else None,
        "sleep_hours": latest.sleep_duration_hr if latest else None,
        "sleep_score": latest.sleep_quality_score if latest else None,
        "sleep_hr_min": latest.sleep_hr_min if latest else None,
        "resting_hr": latest.resting_hr if latest else None,
        "load_ratio": latest.load_ratio if latest else None,
        "load_ratio_label": _load_ratio_label(latest.load_ratio) if latest else None,
        "cti": latest.cti if latest else None,
        "ati": latest.ati if latest else None,
        "tib": latest.tib if latest else None,
        "fatigue_state": latest.fatigue_state if latest else None,
        "stamina_level": latest.performance_index if latest else None,
    }

    adaptation = {
        "needed": needs_adaptation,
        "adapted": adapted,
        "reasons": adaptation_reasons,
        "error": adapt_error,
        "skipped_reason": adapt_skip_reason,
        "week_start": adapt_week_start.isoformat() if adapt_week_start else None,
        "receipt_at": adapt_receipt_at,
    }

    # 5b. Watch backstop (docs/COROS_MCP.md, "Watch push"): reconcile this
    # week's plan with the COROS calendar from today forward, so a plan change
    # made elsewhere (issue triage, a replan) reaches the watch by the next
    # refresh. Off unless COROS_WATCH_PUSH=1. Bounded; never fails the refresh.
    watch = None
    if coros_watch_sync.push_enabled():
        report("Updating watch...")
        try:
            from datetime import timedelta as _wtd
            _today = get_local_today()
            _ws = _today - _wtd(days=_today.weekday())
            _plan_row = week_plan(db, _ws)
            if _plan_row:
                watch = await asyncio.wait_for(
                    asyncio.to_thread(plan_flows.watch_sync_safe, db, _plan_row.plan_json, _ws), timeout=90)
        except Exception as e:  # asyncio.TimeoutError included
            watch = {"status": "error", "error": f"{type(e).__name__}: {e}"[:200], "writes": [], "skipped": []}

    # 6. Freeze the event — the durable answer to "what happened when I
    # refreshed". Sits after every swallowed-exception stage so partial
    # scrapes still record. A lost log line must never fail the sync.
    from backend.services.refresh_events import (
        build_refresh_event, record_refresh_event,
    )

    event = None
    event_id = None
    event_recorded = False
    try:
        event = build_refresh_event(
            db,
            sync_status=sync_status,
            sync_message=sync_message,
            new_activity_ids=new_activity_ids,
            recovery=recovery_summary,
            recovery_stale=recovery_data_stale,
            stale_reason=stale_reason,
            triggers=triggers,
            adaptation=adaptation,
            coros_source=coros_source,
            watch=watch,
        )
        event_id = record_refresh_event(db, event)
        event_recorded = True
    except Exception as e:
        print(f"⚠️ Refresh event not recorded: {e}")

    return {
        "sync_status": sync_status,
        "sync_message": sync_message,
        "recovery": recovery_summary,
        "recovery_data_stale": recovery_data_stale,
        "stale_reason": stale_reason,
        "adaptation": adaptation,
        "event": event,
        "event_id": event_id,
        "event_recorded": event_recorded,
        "coros_auth": coros_mcp.auth_state(),
    }


# --- Deep refresh as a background job ----------------------------------------
#
# The scrape takes 30-90s (plus a cold start); holding an HTTP connection open
# that long from a phone is fragile and forces the athlete to watch it. So the
# deep pull POSTs /smart-refresh/start (returns immediately) and polls
# /smart-refresh/status until the job settles. The scrape finishes even if the
# app is closed mid-way — the data is simply there on the next open.
#
# One athlete, one job: module-level state, no job IDs. In-memory on purpose —
# if the dyno restarts mid-job the job dies with it, status reverts to idle,
# and the phone's poll loop gives up gracefully. FastAPI runs one event loop,
# and the check-and-set in /start has no await between check and set, so no
# lock is needed.

job = {
    "state": "idle",  # idle | running | done | error
    "stage": "",
    "result": None,   # /smart-refresh payload once done
    "error": None,
}


def status():
    return {
        "state": job["state"],
        "stage": job["stage"],
        "result": job["result"],
        "error": job["error"],
    }


async def _smart_refresh_job(session_factory):
    # The request's session can't outlive its request — the job needs its own.
    db = session_factory()
    try:
        def set_stage(stage):
            job["stage"] = stage

        result = await run_smart_refresh(db, progress=set_stage)
        job.update(state="done", stage="", result=result)
    except BaseException as e:
        # BaseException on purpose: a CancelledError that left state
        # "running" forever would tax every future plan generation with the
        # full 90s wait (and wedge /smart-refresh/start).
        print(f"⚠️ Smart-refresh job failed: {e}")
        job.update(state="error", stage="", error=str(e))
        if not isinstance(e, Exception):
            raise
    finally:
        db.close()


def start(session_factory):
    """Kick off a deep refresh; joins the running job if one exists."""
    if job["state"] != "running":
        job.update(state="running", stage="Starting...", result=None, error=None)
        # Keep a reference — an unreferenced task may be garbage-collected
        # mid-flight (documented asyncio pitfall).
        job["task"] = asyncio.create_task(_smart_refresh_job(session_factory))
    return status()
