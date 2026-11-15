"""Food swaps in the nutrition package (council 2026-11-14).

Rules locked in here:
- Swaps are authored in the package and only served; nothing is matched per request.
- Every swap in the committed package passes services/nutrition_swaps.py: the
  day's kcal, protein and fat floors, and no upper limit crossed even with
  every swap of the day taken at once.
- A food eaten whole (a can of tuna, a breaded breast) is offered in whole pieces.
- Fuel around a session, gels and supplements are never swapped.
- A meal can carry a whole-meal variant (the breaded-chicken sandwich for the
  chicken tostadas); it answers to the same day rules.
- The old single `alternative` still goes out for app builds that only know one.
"""
import copy
import json
from datetime import date, datetime

from backend.services.nutrition import DATA_DIR, load_package, today_menu
from backend.services.nutrition_swaps import item_swaps, swap_problems

PKG = load_package()
RULES = json.loads((DATA_DIR / "metas_y_reglas.json").read_text(encoding="utf-8"))
NOON = datetime(2026, 10, 28, 9, 0)


def _menu(code):
    # Wed 2026-10-28 is a plain calendar B day; the menu of any type has the same shape.
    pkg = {**PKG, "calendar": {"2026-10-28": {"fecha": "2026-10-28", "tipo_dia": code}}}
    return today_menu(date(2026, 10, 28), NOON, None, pkg)


def _item(menu, meal_starts, food_id):
    return next(i for m in menu["meals"] if m["name"].startswith(meal_starts)
                for i in m["items"] if i["food_id"] == food_id)


def test_committed_swaps_pass_every_rule():
    for code, t in PKG["types"].items():
        assert swap_problems(t, PKG["foods"], RULES) == [], code


def test_the_package_has_swaps_on_every_day_type():
    for code, t in PKG["types"].items():
        assert any(i.get("alternativas") for m in t["comidas"] for i in m["items"]), code


def test_whole_foods_come_in_whole_pieces():
    chicken = _item(_menu("B"), "Comida: tostadas", "pollo_cocido")
    swaps = {s["food_id"]: s for s in chicken["alternatives"]}
    assert (swaps["atun_tuny"]["amount"], swaps["atun_tuny"]["pieces"], swaps["atun_tuny"]["whole"]) == (220, 1.0, True)
    assert swaps["camaron"]["whole"] is False
    # Two breaded breasts overshoot the day and one falls short on protein: not offered.
    assert "pollo_empanizado_kirkland" not in swaps


def test_a_swap_says_what_it_does_to_the_day():
    tuna = next(s for s in _item(_menu("B"), "Comida: tostadas", "pollo_cocido")["alternatives"] if s["food_id"] == "atun_tuny")
    # 140 g cooked chicken (181 kcal, 41.1 g protein) -> one 220 g can (205 kcal, 46.2 g)
    assert tuna["delta"] == {"kcal": 24, "protein_g": 5.1, "carbs_g": 0.0, "fat_g": 0.3}


def test_estimated_foods_are_flagged():
    swaps = {s["food_id"]: s["estimate"] for s in _item(_menu("B"), "Comida: tostadas", "pollo_cocido")["alternatives"]}
    assert swaps == {"atun_tuny": False, "camaron": False}
    dates = next(s for s in _item(_menu("D"), "Desayuno", "platano")["alternatives"] if s["food_id"] == "datiles_medjool")
    assert dates["estimate"] is True   # the label's own carbohydrate is doubted


def test_old_single_alternative_still_goes_out_first():
    pkg = copy.deepcopy(PKG)
    oats = next(i for m in pkg["types"]["D"]["comidas"] for i in m["items"] if i["food_id"] == "platano" and i.get("alternativas"))
    oats["alternativa"] = {"food_id": "naranja", "cantidad": 260}
    pkg["calendar"] = {"2026-10-28": {"fecha": "2026-10-28", "tipo_dia": "D"}}
    banana = _item(today_menu(date(2026, 10, 28), NOON, None, pkg), "Desayuno", "platano")
    assert banana["alternative"]["food_id"] == "naranja"
    assert [s["food_id"] for s in banana["alternatives"]] == ["naranja", "datiles_medjool", "mango"]


def test_session_fuel_gels_and_supplements_have_no_swaps():
    for code in PKG["types"]:
        menu = _menu(code)
        for meal in menu["meals"]:
            if meal["name"].lower().startswith(("pre-entreno", "durante", "al terminar")):
                assert not any("alternatives" in i for i in meal["items"]), (code, meal["name"])
        assert not any("alternatives" in i for i in menu["supplements"])


def test_upper_limits_count_every_swap_of_the_day_at_once():
    # Day A's bowl holds the day's chicken. Two cans of tuna for it (146 µg of
    # selenium each) cross the 400 µg ceiling, so the bowl offers no tuna.
    t = copy.deepcopy(PKG["types"]["A"])
    bowl = next(i for m in t["comidas"] if m["nombre"].startswith("Cena") for i in m["items"] if i["food_id"] == "pollo_cocido")
    assert not bowl.get("alternativas")
    bowl["alternativas"] = [{"food_id": "atun_tuny", "cantidad": 440}]
    assert any("selenio_ug" in p for p in swap_problems(t, PKG["foods"], RULES))


def test_broken_swaps_are_caught():
    t = copy.deepcopy(PKG["types"]["A"])
    chicken = next(i for m in t["comidas"] if m["nombre"].startswith("Cena") for i in m["items"] if i["food_id"] == "pollo_cocido")
    chicken["alternativas"] = [{"food_id": "atun_tuny", "cantidad": 110},    # half a can
                               {"food_id": "carne", "cantidad": 300},         # +550 kcal
                               {"food_id": "almendra", "cantidad": 20},
                               {"food_id": "whey", "cantidad": 31}]           # a second scoop
    problems = "\n".join(swap_problems(t, PKG["foods"], RULES))
    assert "lata is eaten whole" in problems
    assert "carne 300: day kcal moves" in problems
    assert "almendra 20: not a food to swap in" in problems
    assert "whey 31: not a food to swap in" in problems

    pre = next(m for m in PKG["types"]["B"]["comidas"] if m["nombre"].startswith("Pre-entreno"))
    t = copy.deepcopy(PKG["types"]["B"])
    bread = next(i for m in t["comidas"] if m["nombre"] == pre["nombre"] for i in m["items"] if i["food_id"] == "bagel")
    bread["alternativas"] = [{"food_id": "pan", "cantidad": 54}]
    assert any("never swapped" in p for p in swap_problems(t, PKG["foods"], RULES))


def test_every_swap_food_exists():
    for t in PKG["types"].values():
        for m in t["comidas"]:
            for i in m["items"]:
                for s in item_swaps(i):
                    assert s["food_id"] in PKG["foods"]


def test_the_chicken_sandwich_stands_in_for_the_tostadas():
    # Alex, 2026-11-14: one breaded breast in a Bimbo bagel with chipotle dressing.
    # One breast can't match a chicken item, so it is a whole meal on B and D
    # (the tostadas moved from dinner to lunch on 2026-11-15).
    for code in ("B", "D"):
        dinner = next(m for m in _menu(code)["meals"] if m["name"].startswith("Comida: tostadas"))
        (sandwich,) = dinner["variants"]
        amounts = {i["food_id"]: (i["amount"], i.get("pieces")) for i in sandwich["items"]}
        assert amounts["pollo_empanizado_kirkland"] == (140, 1.0) and amounts["bagel"] == (103, 1.0)
        assert abs(sandwich["delta"]["kcal"]) <= 0.05 * _menu(code)["totals"]["kcal"]
        assert sandwich["totals"]["kcal"] == round(dinner["totals"]["kcal"] + sandwich["delta"]["kcal"])
    # on a rest day it would eat the deficit
    assert not any("Sándwich" in v["name"] for m in _menu("A")["meals"] for v in m.get("variants", []))


def test_rest_day_is_one_bowl_with_its_two_treats():
    # Alex, 2026-11-14: lunch and dinner fused into one chicken, rice and avocado
    # bowl, no tostadas, eaten in one sitting. Susalias can take part of the rice,
    # and half a bagel with cream cheese can take the oats of the snack.
    meals = _menu("A")["meals"]
    assert [m["time"] for m in meals] == ["7:00 AM", "12:00 PM", "6:30 PM"]   # morning hours since 2026-11-15
    bowl = meals[-1]
    assert not any(i["food_id"] == "tostada" for i in bowl["items"])
    (susalias,) = bowl["variants"]
    assert abs(susalias["delta"]["kcal"]) <= 5
    (bagel,) = meals[1]["variants"]
    half = next(i for i in bagel["items"] if i["food_id"] == "bagel")
    assert (half["amount"], half["pieces_label"]) == (51.5, "½ bagel")
    assert {"kefir", "whey"} <= {i["food_id"] for i in bagel["items"]}   # the shake and kefir stay


def test_a_tuna_can_does_not_halve_but_a_bagel_does():
    t = copy.deepcopy(PKG["types"]["B"])
    dinner = next(m for m in t["comidas"] if m["nombre"].startswith("Comida: tostadas"))
    dinner["variantes"] = [{"nombre": "media lata", "items": [{"food_id": "atun_tuny", "cantidad": 110},
                                                               {"food_id": "bagel", "cantidad": 51.5}]}]
    problems = "\n".join(swap_problems(t, PKG["foods"], RULES))
    assert "lata is eaten whole" in problems and "bagel is eaten whole" not in problems


def test_a_broken_variant_is_caught():
    t = copy.deepcopy(PKG["types"]["B"])
    dinner = next(m for m in t["comidas"] if m["nombre"].startswith("Comida: tostadas"))
    dinner["variantes"] = [{"nombre": "dos sándwiches", "items": [
        {"food_id": "bagel", "cantidad": 150}, {"food_id": "pollo_empanizado_kirkland", "cantidad": 280},
        {"food_id": "aderezo", "cantidad": 30}]}]
    problems = "\n".join(swap_problems(t, PKG["foods"], RULES))
    assert "bagel is eaten whole" in problems and "day kcal moves" in problems


def test_whole_foods_say_their_pieces_and_weighed_foods_do_not():
    dinner = next(m for m in _menu("B")["meals"] if m["name"].startswith("Comida: tostadas"))
    labels = {i["food_id"]: i.get("pieces_label") for i in dinner["items"]}
    assert labels["tostada"] == "3 tostadas"
    assert labels["pollo_cocido"] is None and labels["spray"] is None    # a spray has a "piece" but is not eaten whole
    swaps = {s["food_id"]: s.get("pieces_label") for s in _item(_menu("B"), "Comida: tostadas", "pollo_cocido")["alternatives"]}
    assert swaps == {"atun_tuny": "1 lata", "camaron": None}
    sandwich = {i["food_id"]: i.get("pieces_label") for i in dinner["variants"][0]["items"]}
    assert (sandwich["bagel"], sandwich["pollo_empanizado_kirkland"]) == ("1 bagel", "1 pechuga")
    eggs = _item(_menu("A"), "Desayuno", "huevo")
    assert eggs["pieces_label"] == "2 huevos"
    assert dinner["totals"]["fiber_g"] == 16.0                     # the sheet's macro line shows fiber
