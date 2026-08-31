"""Two concurrent GET /weekly-plan requests on a plan-less week must produce
ONE generation and ONE plan row.

The Monday-morning race (2026-08-31): the app's launch burst hit the
endpoint twice, both requests saw "no plan", both spent ~7k Groq tokens
generating, and both inserted — a shadow plan beneath the served one and a
self-inflicted TPM 429. The lock makes the second request wait, re-check,
and reuse the fresh row.
"""
import threading
import time
from datetime import timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

import backend.main as main_mod
from backend.models.database import Athlete, Base, WeeklyPlan
from backend.utils.timezone import get_local_today


def _fake_week():
    return {
        "week_summary": {"focus": "f", "rationale": "r"},
        "days": {d: {"summary": "s", "workouts": [
            {"sport": "running", "title": "Easy Run", "total_time": "30 min",
             "distance_km": 5.0, "steps": []}], "rationale": "r"}
            for d in ["Monday", "Tuesday", "Wednesday", "Thursday",
                      "Friday", "Saturday", "Sunday"]},
    }


def test_concurrent_requests_generate_one_plan(monkeypatch, tmp_path):
    # File-backed + NullPool: each session opens its OWN connection, so the
    # two worker threads exercise the lock, not sqlite's one-connection
    # limits (StaticPool shares a single connection across threads and
    # throws InterfaceError when their queries overlap).
    engine = create_engine(f"sqlite:///{tmp_path}/plans.db",
                           connect_args={"check_same_thread": False},
                           poolclass=NullPool)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)
    s = SessionLocal()
    s.add(Athlete(name="T", weight_kg=78.0,
                  race_date=get_local_today() + timedelta(weeks=10),
                  race_distance="Marathon"))
    s.commit()
    s.close()

    calls = []
    from backend.services.plan_normalizer import normalize_plan

    def fake_pipeline(db, plan_json=None, **kw):
        calls.append(1)
        time.sleep(0.4)  # long enough for both threads to overlap
        return normalize_plan(_fake_week()), []

    # main imports the pipeline inside the function, so patching the source
    # module's attribute is enough (same trick as test_history).
    import backend.services.plan_meta as plan_meta
    monkeypatch.setattr(plan_meta, "run_plan_write_pipeline", fake_pipeline)
    monkeypatch.setattr(
        "backend.agents.data_agent.DataAgent.summarize", lambda self: "m")

    results, errors = [None, None], [None, None]

    def worker(i):
        db = SessionLocal()
        try:
            results[i] = main_mod._get_or_generate_weekly_plan(db)
        except Exception as e:
            errors[i] = e
        finally:
            db.close()

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert errors == [None, None]
    assert len(calls) == 1, "second request must reuse the plan, not regenerate"

    check = SessionLocal()
    try:
        week_start = get_local_today() - timedelta(days=get_local_today().weekday())
        assert check.query(WeeklyPlan).filter(
            WeeklyPlan.week_start == week_start).count() == 1
    finally:
        check.close()

    # Both callers got the same week back.
    assert results[0]["days"].keys() == results[1]["days"].keys()
