"""Write the food swaps (`alternativas`) into backend/data/nutrition/tipos_de_dia.json.

SWAPS says which foods may stand in for which. For every menu item the grams
are the ones that match the original's main macro, to the nearest 5 g — or
the nearest whole piece for a food eaten whole. A swap that breaks a rule in
services/nutrition_swaps.py is left out and printed with the reason.
Rerun after changing a menu quantity, then run the tests.

    PYTHONPATH=. python3 scripts/nutrition_swaps.py backend/data/nutrition
"""
import copy
import json
import sys
from pathlib import Path

from backend.services.nutrition_swaps import factor, is_whole, main_macro, swap_problems, swappable

# original food -> foods that may replace it, in the order the app shows them
SWAPS = {
    "pollo": ["pollo_empanizado_kirkland", "atun_tuny", "camaron"],
    "atun": ["atun_tuny"],
    "arroz": ["pasta", "camote", "tortilla"],
    "tostada": ["tortilla"],
    "platano": ["datiles_medjool", "mango"],
    "miel": ["datiles_medjool"],
    "avena": ["avena_generica"],
}
# Day C sits 107 mg under the calcium ceiling and amaranth for oats already
# spends 71 of them: dates (64 mg per 100 g) on top would cross it.
SKIP = {"C": {"datiles_medjool"}}

D = Path(sys.argv[1])
foods = {f["id"]: f for f in json.loads((D / "alimentos.json").read_text(encoding="utf-8"))["alimentos"]}
rules = json.loads((D / "metas_y_reglas.json").read_text(encoding="utf-8"))
doc = json.loads((D / "tipos_de_dia.json").read_text(encoding="utf-8"))


def amount(orig, qty, new):
    macro = main_macro(orig)
    grams = orig["macros"][macro] * factor(orig, qty) / new["macros"][macro] * 100
    if is_whole(new):
        return max(1, round(grams / new["pieza"]["peso"])) * new["pieza"]["peso"]
    return max(5, round(grams / 5) * 5)


for t in doc["tipos_de_dia"]:
    for meal in t["comidas"]:
        for item in meal["items"]:
            item.pop("alternativas", None)
    for meal in t["comidas"]:
        for item in meal["items"]:
            orig = foods[item["food_id"]]
            if item["food_id"] not in SWAPS or not swappable(meal, orig):
                continue
            kept = []
            for new_id in SWAPS[item["food_id"]]:
                if new_id in SKIP.get(t["codigo"], ()):
                    continue
                swap = {"food_id": new_id, "cantidad": amount(orig, item["cantidad"], foods[new_id])}
                # each swap is judged alone here; the day-wide limits are judged at the end
                trial = copy.deepcopy(t)
                for m in trial["comidas"]:
                    for i in m["items"]:
                        i.pop("alternativas", None)
                        i.pop("alternativa", None)
                ti = next(i for m in trial["comidas"] if m["nombre"] == meal["nombre"]
                          for i in m["items"] if i["food_id"] == item["food_id"] and i["cantidad"] == item["cantidad"])
                ti["alternativas"] = [swap]
                why = swap_problems(trial, foods, rules)
                if why:
                    print("  left out —", "; ".join(why))
                else:
                    kept.append(swap)
            if kept:
                item["alternativas"] = kept
                print(f"{t['codigo']} {meal['nombre'][:28]:28} {item['food_id']} {item['cantidad']} ->",
                      ", ".join(f"{s['food_id']} {s['cantidad']}" for s in kept))
    left = swap_problems(t, foods, rules)
    if left:
        sys.exit("\n".join(left))
(D / "tipos_de_dia.json").write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
