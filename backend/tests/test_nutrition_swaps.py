"""Food swaps in the nutrition package (council 2026-11-14).

Rules locked in here:
- Swaps are authored in the package and only served; nothing is matched per request.
- Every swap in the committed package passes services/nutrition_swaps.py: the
  day's kcal, protein and fat floors, and no upper limit crossed even with
  every swap of the day taken at once.
- A food eaten whole (a can of tuna, a breaded breast) is offered in whole pieces.
- Fuel around a session, gels and supplements are never swapped.
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
    chicken = _item(_menu("B"), "Cena", "pollo")
    swaps = {s["food_id"]: s for s in chicken["alternatives"]}
    assert (swaps["atun_tuny"]["amount"], swaps["atun_tuny"]["pieces"], swaps["atun_tuny"]["whole"]) == (220, 1.0, True)
    assert (swaps["pollo_empanizado_kirkland"]["pieces"], swaps["pollo_empanizado_kirkland"]["whole"]) == (1.0, True)
    assert swaps["camaron"]["whole"] is False


def test_a_swap_says_what_it_does_to_the_day():
    tuna = next(s for s in _item(_menu("B"), "Cena", "pollo")["alternatives"] if s["food_id"] == "atun_tuny")
    # 133 g raw chicken (129 kcal, 29.3 g protein) -> one 220 g can (205 kcal, 46.2 g)
    assert tuna["delta"] == {"kcal": 76, "protein_g": 16.9, "carbs_g": 0.0, "fat_g": 0.9}


def test_estimated_foods_are_flagged():
    swaps = {s["food_id"]: s["estimate"] for s in _item(_menu("B"), "Cena", "pollo")["alternatives"]}
    assert swaps == {"pollo_empanizado_kirkland": True, "atun_tuny": False, "camaron": False}
    dates = next(s for s in _item(_menu("A"), "Merienda", "platano")["alternatives"] if s["food_id"] == "datiles_medjool")
    assert dates["estimate"] is True   # the label's own carbohydrate is doubted


def test_old_single_alternative_still_goes_out_first():
    oats = _item(_menu("B"), "Desayuno", "avena")
    assert oats["alternative"]["food_id"] == "amaranto"
    assert [s["food_id"] for s in oats["alternatives"]] == ["amaranto", "avena_generica"]


def test_session_fuel_gels_and_supplements_have_no_swaps():
    for code in PKG["types"]:
        menu = _menu(code)
        for meal in menu["meals"]:
            if meal["name"].lower().startswith(("pre-entreno", "durante", "al terminar")):
                assert not any("alternatives" in i for i in meal["items"]), (code, meal["name"])
        assert not any("alternatives" in i for i in menu["supplements"])


def test_long_run_day_stays_under_the_calcium_ceiling_with_every_swap():
    # Dates for the banana on day C would cross it; the authoring script leaves them out.
    t = copy.deepcopy(PKG["types"]["C"])
    banana = next(i for m in t["comidas"] for i in m["items"] if i["food_id"] == "platano" and i.get("alternativas"))
    banana["alternativas"].append({"food_id": "datiles_medjool", "cantidad": 40})
    shake = next(i for m in t["comidas"] if m["nombre"].startswith("Licuado") for i in m["items"] if i["food_id"] == "miel")
    shake["alternativas"] = [{"food_id": "datiles_medjool", "cantidad": 60}]
    assert any("calcio_mg" in p for p in swap_problems(t, PKG["foods"], RULES))


def test_broken_swaps_are_caught():
    t = copy.deepcopy(PKG["types"]["A"])
    chicken = next(i for m in t["comidas"] if m["nombre"].startswith("Cena") for i in m["items"] if i["food_id"] == "pollo")
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
    bread = next(i for m in t["comidas"] if m["nombre"] == pre["nombre"] for i in m["items"] if i["food_id"] == "pan_oroweat")
    bread["alternativas"] = [{"food_id": "pan", "cantidad": 54}]
    assert any("never swapped" in p for p in swap_problems(t, PKG["foods"], RULES))


def test_every_swap_food_exists():
    for t in PKG["types"].values():
        for m in t["comidas"]:
            for i in m["items"]:
                for s in item_swaps(i):
                    assert s["food_id"] in PKG["foods"]
