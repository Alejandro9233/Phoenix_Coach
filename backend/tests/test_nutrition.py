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
import copy
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

def test_a_short_easy_swim_keeps_the_rest_day_menu():
    """Alex 2026-11-14: the technique swim replaced the walk in the deficit."""
    swim = {**_w("swimming", "Technique Session"), "total_time": "30 min"}
    code, why = day_type_from_plan(_day(swim))
    assert code == "A" and "short swim" in why
    assert day_type_from_plan(_day(swim, EASY))[0] == "B"
    assert day_type_from_plan(_day({**swim, "total_time": "60 min"}))[0] == "B"
    assert day_type_from_plan(_day({**swim, "total_time": None}))[0] == "B"
    css = {**swim, "title": "CSS Threshold", "steps": [{"type": "main", "duration": "20:00", "zone": 4}]}
    assert day_type_from_plan(_day(css))[0] == "D"



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


# --- the eve of a long run ---------------------------------------------------

def _long(minutes):
    w = _w("running", "Long Run", [{"type": "main", "duration": f"{minutes}:00", "zone": 2}])
    w["total_time"] = f"{minutes} min"
    return w


def _week(**days):
    return {"week_summary": {}, "days": {name: _day(*ws) for name, ws in days.items()}}


SAT = date(2026, 10, 31)   # calendar A, no event


def test_eve_of_a_two_hour_long_run_eats_d():
    menu = today_menu(SAT, datetime(2026, 10, 31, 9, 0),
                      _week(Saturday=[GYM], Sunday=[_long(140)]), PKG)
    assert menu["day_type"] == "D"
    # The menu says tomorrow chose it, and what today alone would be.
    assert menu["tomorrow_long_run_min"] == 140
    assert menu["reason"] == "Carb load for tomorrow's 140 min long run. Today alone is an A day."


def test_eve_of_a_shorter_long_run_stays_a():
    menu = today_menu(SAT, datetime(2026, 10, 31, 9, 0),
                      _week(Saturday=[GYM], Sunday=[_long(95)]), PKG)
    assert menu["day_type"] == "A"


def test_eve_rule_never_lowers_a_day():
    menu = today_menu(SAT, datetime(2026, 10, 31, 9, 0),
                      _week(Saturday=[LONG], Sunday=[_long(140)]), PKG)
    assert menu["day_type"] == "C"
    assert menu["tomorrow_long_run_min"] is None   # today's own run chose it


def test_sunday_does_not_look_into_next_week():
    sun = date(2026, 11, 1)
    menu = today_menu(sun, datetime(2026, 11, 1, 9, 0), _week(Sunday=[EASY], Monday=[_long(140)]), PKG)
    assert menu["day_type"] == "B"


# --- next meal --------------------------------------------------------------

def _next(hh, mm):
    menu = today_menu(PLAIN_DAY, datetime(2026, 10, 28, hh, mm), _plan("Wednesday", EASY), PKG)
    i = menu["next_meal_index"]
    return None if i is None else menu["meals"][i]["time"]


def test_next_meal_progression():
    # Morning-run hours (Alex, 2026-11-14: the marathon starts at 6:45 AM).
    assert _next(4, 0) == "5:00 AM"
    assert _next(5, 29) == "5:00 AM"        # inside the 30-min grace
    assert _next(7, 50) == "8:00 AM"
    assert _next(13, 31) == "6:30 PM"
    assert _next(19, 1) is None             # dinner is the last meal: the day is done


def test_no_bedtime_meal():
    # Alex, 2026-11-14: "no quiero la toma". Cereal and milk at 11:59 PM are
    # gone from all four days; their protein went to the chicken and beef, and
    # on C and D their carbs to the pasta and rice.
    for code, t in PKG["types"].items():
        assert not any(m["nombre"] == "Antes de dormir" for m in t["comidas"]), code
        assert t["totales"]["proteina_g"] >= 2.0 * 74.3, code
    # C swapped its recovery shake for three dates (2026-11-14): 537 g, and 6 g/kg is its floor.
    assert PKG["types"]["C"]["totales"]["carbohidrato_g"] >= 6.0 * 74.3
    # D gave 5 g of carbs to "less pasta, more sauce" (2026-11-14); 5 g/kg is its floor.
    assert PKG["types"]["D"]["totales"]["carbohidrato_g"] >= 5.0 * 74.3


def test_menu_shape():
    menu = today_menu(PLAIN_DAY, datetime(2026, 10, 28, 9, 0), _plan("Wednesday", EASY), PKG)
    assert menu["totals"]["kcal"] == 2075.0
    tostada = next(i for m in menu["meals"] for i in m["items"] if i["food_id"] == "tostada")
    assert (tostada["pieces"], tostada["piece_name"]) == (3.0, "tostada")
    # Alex weighs his chicken cooked (2026-11-14); the ground beef stays raw.
    pollo = next(i for m in menu["meals"] for i in m["items"] if i["food_id"] == "pollo_cocido")
    assert "COCIDO" in pollo["note"]
    weigh = {i["food_id"]: i["weigh"] for m in menu["meals"] for i in m["items"]}
    assert (weigh["pollo_cocido"], weigh["carne"], weigh["pasta"], weigh["avena_generica"]) == ("cooked", "raw", "cooked", None)
    assert {s["food_id"] for s in menu["supplements"]} == {"salmon", "vitc", "magnesio"}


def test_alternatives_are_carb_matched():
    # The package's first swap: oats (Bob's Red Mill) 43 g -> amaranth 40 g. Gone
    # from the menus on 2026-11-14 (Alex eats Granvita and doesn't buy amaranth);
    # the single `alternativa` still has to go out for a package that carries one.
    pkg = copy.deepcopy(PKG)
    oats = next(i for m in pkg["types"]["B"]["comidas"] for i in m["items"] if i["food_id"] == "avena_generica")
    oats["alternativa"] = {"food_id": "amaranto", "cantidad": 40}
    menu = today_menu(PLAIN_DAY, datetime(2026, 10, 28, 9, 0), _plan("Wednesday", EASY), pkg)
    alts = {i["food_id"]: (i["amount"], i["alternative"]["food_id"], i["alternative"]["amount"])
            for m in menu["meals"] for i in m["items"] if "alternative" in i}
    assert alts == {"avena_generica": (40, "amaranto", 40)}
    assert not any(i.get("alternativa") for t in PKG["types"].values() for m in t["comidas"] for i in m["items"])


def test_pre_workout_is_half_a_bagel():
    # 2026-11-14: Alex only buys bagels now. Half a Bimbo bagel replaces the
    # Oroweat slice and the jam gives back the difference, so the carbs hold.
    menu = today_menu(PLAIN_DAY, datetime(2026, 10, 28, 9, 0), _plan("Wednesday", EASY), PKG)
    pre = next(m for m in menu["meals"] if m["name"] == "Pre-entreno")
    bread = next(i for i in pre["items"] if i["food_id"] == "bagel")
    assert bread["pieces_label"] == "½ bagel"
    assert not any(i["food_id"] == "pan_oroweat" for t in PKG["types"].values() for m in t["comidas"] for i in m["items"])
    assert abs(pre["totals"]["carbs_g"] - 53.9) <= 1   # the Bimbo pre-workout carried 53.9 g


def test_rest_day_meets_the_fiber_target():
    # metas: fibra 38 g; day A sat at 32.8 g until the cranberries (2026-10-28).
    # 2026-11-14: the banana left day A for the deficit and took 3 g with it;
    # Alex took that cost knowingly, and Granvita oats took another gram.
    # Under 30 g is no longer a trade-off, it is a hole.
    assert PKG["types"]["A"]["totales"]["fibra_g"] >= 30


def test_long_run_day_meets_the_fat_floor():
    # metas: grasa >= 20 % de kcal; C sat at 19.5 % until 2026-10-22 (+20 g aguacate).
    # Measured on the food: the gels (pure carbs, 2026-10-29) scale with the
    # run and would push it under by themselves.
    c = PKG["types"]["C"]
    run = next(m for m in c["comidas"] if m["nombre"].startswith("Durante"))
    kcal = c["totales"]["kcal"] - run["totales"]["kcal"]
    assert c["totales"]["grasa_g"] * 9 / kcal >= 0.20


def test_rest_day_cut_stays_above_its_floors():
    # 2026-10-29: day A cut to the 2,025 kcal maintenance (Katch-McArdle x 1.2).
    # Floors: protein 2.0 g/kg, and energy. The energy floor was 30 kcal/kg of
    # lean mass (1,830) until Alex started the fat-loss deficit on 2026-11-14
    # ("el piso de las calorías no importa") and set the menu near 1,690.
    # Well under that is a new decision of his, not a side effect of an edit.
    a = PKG["types"]["A"]
    assert a["gasto_estimado_kcal"] == 2025
    assert a["totales"]["kcal"] >= 1650
    assert a["totales"]["proteina_g"] >= 2.0 * 74.3


def _gels(menu):
    meal = next(m for m in menu["meals"] if m["name"].startswith("Durante"))
    return meal, next(i for i in meal["items"] if i["food_id"] == "gel")


def test_long_run_gels_follow_the_run_at_80_g_per_hour():
    # Sun 2026-11-01, 128 min: 80 g/h x 2.13 h = 171 g -> 4 gels of 40 g.
    menu = today_menu(date(2026, 11, 1), datetime(2026, 11, 1, 9, 0), _plan("Sunday", _long(128)), PKG)
    meal, gel = _gels(menu)
    assert gel["amount"] == 4
    assert "80 g/h" in meal["name"] and "cada 30 min" in meal["name"]


def test_gel_rate_steps_up_after_the_half():
    # Sun 2026-11-22, 150 min: 90 g/h x 2.5 h = 225 g -> 6 gels, one every 25 min.
    menu = today_menu(date(2026, 11, 22), datetime(2026, 11, 22, 9, 0), _plan("Sunday", _long(150)), PKG)
    meal, gel = _gels(menu)
    assert gel["amount"] == 6
    assert "90 g/h" in meal["name"] and "cada 25 min" in meal["name"]


def test_gels_move_the_totals_with_them():
    # 180 min at 80 g/h = 240 g -> 6 gels, 2 more than the package's 4.
    base = PKG["types"]["C"]["totales"]
    menu = today_menu(date(2026, 11, 1), datetime(2026, 11, 1, 9, 0), _plan("Sunday", _long(180)), PKG)
    meal, gel = _gels(menu)
    assert gel["amount"] == 6
    assert menu["totals"]["kcal"] == base["kcal"] + 2 * 160
    assert menu["totals"]["carbs_g"] == round(base["carbohidrato_g"] + 2 * 40, 1)
    assert menu["deficit_kcal"] == PKG["types"]["C"]["deficit_kcal"] - 2 * 160
    # The package itself is untouched for the next request.
    assert next(i for m in PKG["types"]["C"]["comidas"] for i in m["items"] if i["food_id"] == "gel")["cantidad"] == 4


def test_the_27_km_calendar_day_still_follows_the_run():
    # 2026-11-15 is a fixed calendar day (evento); the gels still scale.
    menu = today_menu(date(2026, 11, 15), datetime(2026, 11, 15, 9, 0), _plan("Sunday", _long(170)), PKG)
    assert (menu["day_type"], menu["source"]) == ("C", "calendar")
    assert _gels(menu)[1]["amount"] == 6  # 90 g/h x 2.83 h = 255 g -> 6.4


def test_c_day_without_a_long_run_keeps_the_package_gels():
    # The half on 2026-11-08: calendar C, the plan's race isn't titled a long run.
    race = {"sport": "running", "title": "Half Marathon Trial", "total_time": "100 min", "steps": []}
    menu = today_menu(date(2026, 11, 8), datetime(2026, 11, 8, 9, 0), _plan("Sunday", race), PKG)
    meal, gel = _gels(menu)
    assert gel["amount"] == 4 and "g/h" not in meal["name"]


def test_gel_ladder_out_of_order_is_rejected(tmp_path):
    pkg_dir = tmp_path / "pkg"
    shutil.copytree(DATA_DIR, pkg_dir)
    rules = json.loads((pkg_dir / "metas_y_reglas.json").read_text())
    rules["geles_tirada"]["carb_g_por_hora"].reverse()
    (pkg_dir / "metas_y_reglas.json").write_text(json.dumps(rules))
    with pytest.raises(NutritionPackageError, match="date order"):
        load_package(pkg_dir)


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
    with patch("backend.services.plan_flows._generate_weekly_plan") as gen:
        r = client.get("/nutrition/today")
    assert r.status_code == 200
    assert gen.call_count == 0
    body = r.json()
    if body["source"] == "plan":   # a calendar fixed day can outrank the plan
        assert body["day_type"] == "D"


def test_endpoint_without_plan_does_not_generate(client):
    with patch("backend.services.plan_flows._generate_weekly_plan") as gen:
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
    with patch("backend.services.plan_flows._generate_weekly_plan") as gen:
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
