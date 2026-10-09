"""Weekly volume by sport (backend/services/volume_ledger.py): the Recent
tab's ledger. Python owns the buckets, the four-week average and the "gone"
list; iOS only draws them."""
from datetime import date, datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.models.database import Activity, Base
from backend.services import volume_ledger as vl

TODAY = date(2026, 10, 9)          # a Friday; this week starts Mon Oct 5


@pytest.fixture
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


def _add(db, id_, sport, day, hours):
    db.add(Activity(id=id_, sport=sport, start_time=datetime.combine(day, datetime.min.time().replace(hour=7)),
                    duration_sec=hours * 3600, distance_m=0))


def test_sport_keys_and_week_start():
    assert [vl.sport_key(s) for s in ("running", "Trail Running", "cycling", "swimming", "strength", "yoga", None)] \
        == ["run", "run", "bike", "swim", "strength", "other", "other"]
    assert vl.week_start(date(2026, 10, 9)) == date(2026, 10, 5) and vl.week_start(date(2026, 10, 5)) == date(2026, 10, 5)


def test_weekly_volume_buckets_eight_weeks_and_averages_the_previous_four(db):
    # Eight Mondays back from Oct 5: Aug 17 … Oct 5. Bike stops after Sep 14.
    mondays = [date(2026, 8, 17) + timedelta(weeks=i) for i in range(8)]
    for i, monday in enumerate(mondays):
        _add(db, f"r{i}", "running", monday + timedelta(days=1), [3.2, 3.6, 2.9, 1.4, 1.1, 0.6, 1.3, 1.6][i])
        _add(db, f"s{i}", "strength", monday + timedelta(days=2), 2.5)
        if i <= 4:
            _add(db, f"b{i}", "cycling", monday + timedelta(days=3), [2.1, 2.4, 1.8, 2.6, 1.9][i])
    _add(db, "old", "running", date(2026, 8, 10), 5.0)        # the week before the window: ignored
    _add(db, "future", "running", date(2026, 10, 12), 5.0)    # a row dated next week: ignored
    _add(db, "zero", "running", date(2026, 10, 6), 0)          # no duration: ignored
    db.commit()

    out = vl.weekly_volume(db, today=TODAY)
    assert out["sports"] == ["run", "strength", "bike", "swim"]      # no "other" row when it has no hours
    assert [w["start"] for w in out["weeks"]] == [m.isoformat() for m in mondays]
    assert out["weeks"][0]["hours"] == {"run": 3.2, "strength": 2.5, "bike": 2.1, "swim": 0.0}
    assert out["weeks"][0]["total"] == 7.8
    assert out["this_week"] == {"run": 1.6, "strength": 2.5, "bike": 0.0, "swim": 0.0, "total": 4.1}
    # the four weeks before this one: Sep 7, 14, 21, 28
    assert out["avg_4wk"] == {"run": 1.1, "strength": 2.5, "bike": 1.1, "swim": 0.0}
    assert out["delta_4wk"] == {"run": 0.5, "strength": 0.0, "bike": -1.1, "swim": 0.0}
    # bike: last hours in week 5 (Sep 14), three weeks ago; "was" = mean of Aug 24 … Sep 14
    assert out["gone"] == [{"sport": "bike", "weeks": 3, "was": 2.2}]


def test_other_row_only_when_something_landed_there_and_none_without_training(db):
    assert vl.weekly_volume(db, today=TODAY) is None
    _add(db, "e1", "elliptical", date(2026, 10, 6), 0.75)
    db.commit()
    out = vl.weekly_volume(db, today=TODAY)
    assert out["sports"][-1] == "other" and out["this_week"]["other"] == 0.8 and out["gone"] == []


def test_a_sport_quiet_only_this_partial_week_is_not_gone(db):
    for i in range(7):
        _add(db, f"b{i}", "cycling", date(2026, 8, 17) + timedelta(weeks=i, days=2), 2.0)
    db.commit()
    out = vl.weekly_volume(db, today=TODAY)       # Friday, no ride yet this week
    assert out["this_week"]["bike"] == 0.0 and out["gone"] == []
