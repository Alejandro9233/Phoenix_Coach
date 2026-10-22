"""Today's menu from the nutrition package (council 2026-10-21).

Rules locked in here:
- The day type comes from the stored plan: long run (by title) C, quality D,
  other cardio B, gym only or rest A. Highest wins on a two-session day.
- An untitled longest run is NOT a C day (find_long_run_day's fallback).
- The calendar wins on its fixed days: event, sin_deficit, PENDIENTE (-> C).
- No stored plan: the calendar's guess, labelled. Never generates a plan.
- Every meal falls on its own date; a package with a 1:00 AM meal is rejected.
- A bad package is a 503 for this endpoint only.
- The next meal holds for 30 min past its time, then moves on.
"""
import json
import shutil
from datetime import date, datetime, timedelta
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.main import app, get_db
from backend.models.database import Athlete, Base, WeeklyPlan
from backend.services import nutrition
from backend.services.nutrition import (
    DATA_DIR, NutritionPackageError, day_type_from_plan, load_package, today_menu,
)
from backend.utils.timezone import get_local_today


def _w(sport, title, steps=None):
    return {"sport": sport, "title": title, "total_time": "45 min", "steps": steps or []}


def _day(*workouts):
    return {"summary": "s", "rationale": "r", "coach_note": "c", "workouts": list(workouts)}


EASY = _w("running", "Easy Run", [{"type": "main", "duration": "45:00", "zone": 2}])
LONG = _w("running", "Long Run", [{"type": "main", "duration": "95:00", "zone": 2}])
TEMPO = _w("running", "Tempo Run", [{"type": "main", "duration": "20:00", "zone": 4}])
GYM = _w("strength", "Maintenance (2 sets, heavy compounds)")
REST = _w("rest", "Rest")

PKG = load_package()
# A plain week inside the calendar with no event: Wed 2026-10-28 (calendar B).
PLAIN_DAY = date(2026, 10, 28)


# --- day type from the plan ---------------------------------------------

def test_long_run_is_c():
    assert day_type_from_plan(_day(LONG))[0] == "C"


def test_quality_is_d():
    assert day_type_from_plan(_day(TEMPO)) == ("D", "Tempo Run")


def test_easy_cardio_is_b():
    assert day_type_from_plan(_day(EASY))[0] == "B"
    assert day_type_from_plan(_day(_w("elliptical", "Elliptical")))[0] == "B"


def test_gym_only_and_rest_are_a():
    assert day_type_from_plan(_day(GYM))[0] == "A"
    assert "gym only" in day_type_from_plan(_day(GYM))[1]
    assert day_type_from_plan(_day(REST)) == ("A", "Rest day")
    assert day_type_from_plan(None)[0] == "A"


def test_highest_load_wins():
    assert day_type_from_plan(_day(GYM, EASY))[0] == "B"
    assert day_type_from_plan(_day(EASY, TEMPO))[0] == "D"
    assert day_type_from_plan(_day(GYM, LONG))[0] == "C"
    assert day_type_from_plan(_day(_w("running", "Marathon Pace Long Run",
                                       [{"type": "main", "duration": "60:00", "zone": 3}])))[0] == "C"


def test_untitled_longest_run_is_not_c():
    long_easy = _w("running", "Easy Run", [{"type": "main", "duration": "70:00", "zone": 2}])
    long_easy["total_time"] = "70 min"
    assert day_type_from_plan(_day(long_easy))[0] == "B"


# --- plan vs calendar -----------------------------------------------------

def _plan(day_name, *workouts):
    return {"week_summary": {}, "days": {day_name: _day(*workouts)}}


def test_plan_overrides_calendar_guess():
    # Calendar guesses B for 2026-10-28; the real plan has a tempo there.
    menu = today_menu(PLAIN_DAY, datetime(2026, 10, 28, 9, 0), _plan("Wednesday", TEMPO), PKG)
    assert (menu["day_type"], menu["source"]) == ("D", "plan")


def test_no_plan_falls_back_to_calendar_labelled():
    menu = today_menu(PLAIN_DAY, datetime(2026, 10, 28, 9, 0), None, PKG)
    assert (menu["day_type"], menu["source"]) == ("B", "calendar")
    assert "calendar estimate" in menu["reason"]


def test_calendar_event_days_win():
    # Sat 2026-11-07: eve of the half, sin_deficit, calendar C. The plan has a shakeout.
    menu = today_menu(date(2026, 11, 7), datetime(2026, 11, 7, 9, 0), _plan("Saturday", EASY), PKG)
    assert (menu["day_type"], menu["source"], menu["no_deficit"]) == ("C", "calendar", True)


def test_pending_day_eats_c():
    menu = today_menu(date(2026, 12, 11), datetime(2026, 12, 11, 9, 0), _plan("Friday", REST), PKG)
    assert menu["day_type"] == "C"
    assert "pending" in menu["reason"]


def test_outside_calendar_without_plan_is_empty():
    menu = today_menu(date(2027, 3, 1), datetime(2027, 3, 1, 9, 0), None, PKG)
    assert menu["day_type"] is None


def test_outside_calendar_with_plan_still_works():
    menu = today_menu(date(2027, 3, 1), datetime(2027, 3, 1, 9, 0), _plan("Monday", EASY), PKG)
    assert menu["day_type"] == "B"


# --- next meal --------------------------------------------------------------

def _next(hh, mm):
    menu = today_menu(PLAIN_DAY, datetime(2026, 10, 28, hh, mm), _plan("Wednesday", EASY), PKG)
    i = menu["next_meal_index"]
    return None if i is None else menu["meals"][i]["time"]


def test_next_meal_progression():
    assert _next(9, 0) == "12:30 PM"
    assert _next(12, 59) == "12:30 PM"      # inside the 30-min grace
    assert _next(13, 1) == "3:30 PM"
    assert _next(23, 0) == "11:59 PM"


def test_bedtime_snack_is_last_meal_of_its_day():
    for code, t in PKG["types"].items():
        timed = [m for m in t["comidas"] if m["hora"] != "Diario"]
        assert timed[-1]["hora"] == "11:59 PM", code


def test_menu_shape():
    menu = today_menu(PLAIN_DAY, datetime(2026, 10, 28, 9, 0), _plan("Wednesday", EASY), PKG)
    assert menu["totals"]["kcal"] == 2199.0
    tostada = next(i for m in menu["meals"] for i in m["items"] if i["food_id"] == "tostada")
    assert (tostada["pieces"], tostada["piece_name"]) == (3.0, "tostada")
    pollo = next(i for m in menu["meals"] for i in m["items"] if i["food_id"] == "pollo")
    assert "CRUDA" in pollo["note"]
    weigh = {i["food_id"]: i["weigh"] for m in menu["meals"] for i in m["items"]}
    assert (weigh["pollo"], weigh["carne"], weigh["pasta"], weigh["avena"]) == ("raw", "raw", "cooked", None)
    assert {s["food_id"] for s in menu["supplements"]} == {"salmon", "vitc", "magnesio"}


def test_oats_carry_the_amaranth_alternative_at_the_same_grams():
    menu = today_menu(PLAIN_DAY, datetime(2026, 10, 28, 9, 0), _plan("Wednesday", EASY), PKG)
    oats = next(i for m in menu["meals"] for i in m["items"] if i["food_id"] == "avena")
    assert oats["alternative"] == {"food_id": "amaranto", "name": "Amaranto natural", "amount": 40, "unit": "g"}
    assert all("alternative" not in i for m in menu["meals"] for i in m["items"] if i["food_id"] != "avena")


def test_long_run_day_meets_the_fat_floor():
    # metas: grasa >= 20 % de kcal; C sat at 19.5 % until 2026-10-22 (+20 g aguacate).
    assert PKG["types"]["C"]["grasa_pct_kcal"] >= 20


def test_daily_supplements_list_every_one_not_already_in_a_meal():
    # Creatine and ashwagandha carry no nutrients, so no menu lists them;
    # Isopure and Multilyte are scheduled inside meals already.
    menu = today_menu(PLAIN_DAY, datetime(2026, 10, 28, 9, 0), _plan("Wednesday", EASY), PKG)
    daily = {s["id"]: s for s in menu["daily_supplements"]}
    assert set(daily) == {"salmon", "vitc", "magnesio", "creatina", "ashwagandha"}
    assert daily["ashwagandha"]["dose"].startswith("1 cápsula")


# --- package checks ---------------------------------------------------------

def _broken_copy(tmp_path, mutate):
    for f in DATA_DIR.glob("*.json"):
        shutil.copy(f, tmp_path / f.name)
    path = tmp_path / "tipos_de_dia.json"
    doc = json.loads(path.read_text())
    mutate(doc)
    path.write_text(json.dumps(doc))
    return tmp_path


def test_package_rejects_after_midnight_meal(tmp_path):
    def back_to_1am(doc):
        doc["tipos_de_dia"][0]["comidas"][5]["hora"] = "1:00 AM"
    with pytest.raises(NutritionPackageError, match="11:59 PM"):
        load_package(_broken_copy(tmp_path, back_to_1am))


def test_package_rejects_unknown_food(tmp_path):
    def bad_food(doc):
        doc["tipos_de_dia"][0]["comidas"][0]["items"][0]["food_id"] = "nope"
    with pytest.raises(NutritionPackageError, match="unknown food"):
        load_package(_broken_copy(tmp_path, bad_food))


# --- endpoint -----------------------------------------------------------------

@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    session.add(Athlete(name="Test", weight_kg=74.3, race_date=None, race_distance="Marathon"))
    session.commit()
    yield session
    session.close()
    engine.dispose()


@pytest.fixture
def client(db):
    app.dependency_overrides[get_db] = lambda: (yield db)
    yield TestClient(app, raise_server_exceptions=False)
    app.dependency_overrides.clear()


def test_endpoint_reads_stored_plan_and_never_generates(client, db):
    today = get_local_today()
    week_start = today - timedelta(days=today.weekday())
    db.add(WeeklyPlan(week_start=week_start, athlete_id=1, plan_json=_plan(today.strftime("%A"), TEMPO)))
    db.commit()
    with patch("backend.main._generate_weekly_plan") as gen:
        r = client.get("/nutrition/today")
    assert r.status_code == 200
    assert gen.call_count == 0
    body = r.json()
    if body["source"] == "plan":   # a calendar fixed day can outrank the plan
        assert body["day_type"] == "D"


def test_endpoint_without_plan_does_not_generate(client):
    with patch("backend.main._generate_weekly_plan") as gen:
        r = client.get("/nutrition/today")
    assert r.status_code == 200
    assert gen.call_count == 0


def test_bad_package_is_503(client):
    with patch.object(nutrition, "package", side_effect=NutritionPackageError("calendario.json is missing")):
        r = client.get("/nutrition/today")
    assert r.status_code == 503
    assert "calendario.json" in r.json()["detail"]


def test_days_endpoint_crosses_into_unplanned_week(client, db):
    # This week's plan is stored; a later week isn't — those days fall back
    # to the calendar (or come back empty outside it), never generate.
    today = get_local_today()
    week_start = today - timedelta(days=today.weekday())
    db.add(WeeklyPlan(week_start=week_start, athlete_id=1,
                      plan_json={"week_summary": {}, "days": {}}))
    db.commit()
    with patch("backend.main._generate_weekly_plan") as gen:
        r = client.get("/nutrition/days?count=7")
    assert r.status_code == 200
    assert gen.call_count == 0
    body = r.json()
    assert [d["date"] for d in body] == [(today + timedelta(days=i)).isoformat() for i in range(7)]
    for d in body:
        if d["day_type"] is not None and d["date"] >= (week_start + timedelta(days=7)).isoformat():
            assert d["source"] == "calendar"


def test_days_endpoint_clamps_count(client):
    assert len(client.get("/nutrition/days?count=50").json()) == 7
    assert len(client.get("/nutrition/days?count=0").json()) == 1
