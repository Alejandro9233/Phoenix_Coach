"""Recompute backend/data/nutrition/tipos_de_dia.json totals from alimentos.json with the package's
own rules (checked 2026-10-22 to reproduce day C exactly): item = value x
qty/100 (or x qty per unit), kcal/sodium to integers and macros to 1 dp with
Python round(); meal and day totals are rounded sums of the raw values;
micros from raw, 2 dp. --only=AB limits it to those day types."""
import json
import sys
from pathlib import Path

D = Path(sys.argv[1])
WEIGHT = 74.3
foods = {f["id"]: f for f in json.loads((D / "alimentos.json").read_text())["alimentos"]}
doc = json.loads((D / "tipos_de_dia.json").read_text())
MAC = {"kcal": "kcal", "proteina_g": "proteina_g", "carbohidrato_g": "carbohidrato_disponible_g",
       "grasa_g": "grasa_g", "grasa_saturada_g": "grasa_saturada_g", "fibra_g": "fibra_g", "sodio_mg": "sodio_mg"}


def factor(food, qty):
    return qty if food["valores_por"] == "1 unidad" else qty / 100


def rnd(key, v):
    return float(round(v)) if key in ("kcal", "sodio_mg") else round(v, 1)


def recompute(t):
    raw_day = {k: 0.0 for k in MAC}
    micros = {}
    for meal in t["comidas"]:
        raw_meal = {k: 0.0 for k in MAC}
        for it in meal["items"]:
            f = foods[it["food_id"]]
            k = factor(f, it["cantidad"])
            it["macros"] = {key: rnd(key, f["macros"][src] * k) for key, src in MAC.items()}
            for key, src in MAC.items():
                raw_meal[key] += f["macros"][src] * k
            for mk, mv in (f.get("micros") or {}).items():
                micros[mk] = micros.get(mk, 0.0) + mv * k
        meal["totales"] = {key: rnd(key, v) for key, v in raw_meal.items()}
        for key in MAC:
            raw_day[key] += raw_meal[key]
    t["totales"] = {key: rnd(key, v) for key, v in raw_day.items()}
    tot = t["totales"]
    t["proteina_g_por_kg"] = round(tot["proteina_g"] / WEIGHT, 2)
    t["carbohidrato_g_por_kg"] = round(tot["carbohidrato_g"] / WEIGHT, 2)
    t["grasa_pct_kcal"] = round(tot["grasa_g"] * 9 / tot["kcal"] * 100, 1)
    t["deficit_kcal"] = float(round(t["gasto_estimado_kcal"] - tot["kcal"]))
    m = {k: round(v, 2) for k, v in micros.items()}
    m["epa_dha_g"] = round(micros.get("epa_g", 0) + micros.get("dha_g", 0), 2)
    t["micros_totales"] = {k: m.get(k, 0.0) for k in t["micros_totales"]}


only = next((a.split("=")[1] for a in sys.argv if a.startswith("--only=")), "ABCD")
for t in doc["tipos_de_dia"]:
    if t["codigo"] in only:
        recompute(t)
(D / "tipos_de_dia.json").write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n")
for t in doc["tipos_de_dia"]:
    tot = t["totales"]
    print(t["codigo"], tot, t["proteina_g_por_kg"], t["carbohidrato_g_por_kg"], t["grasa_pct_kcal"], t["deficit_kcal"],
          "Ca", t["micros_totales"]["calcio_mg"], "VitE", t["micros_totales"]["vitE_mg"])
