"""
Nutrition swaps — the rules a hand-picked food swap must pass. Authoring-time
only: scripts/nutrition_swaps.py writes `alternativas` into tipos_de_dia.json
with these, test_nutrition_swaps.py holds the committed package to them, and
services/nutrition.py just serves what is in the file.

Council 2026-11-14: swaps are authored in the package, not computed per
request. A swap is offered without recomputing the day, so it is only safe
if the day was recomputed with it once, here. Group-matching at request time
lost because the limits are day-wide: carb-matched tostadas for the long-run
pasta put calcium past its ceiling, and a protein-matched swap to whey is a
second scoop.

A meal can also carry `variantes`: a whole other meal in its place (the
breaded-chicken sandwich for the chicken tostadas), written by hand because
a food eaten whole rarely matches one item. A variant answers to the same
rules, measured against the meal it replaces; it has no main macro to hold.

A swap replaces one item of one meal. It passes when:
  - the meal is not fuel around a session (pre-workout, during, right after)
    and the item is not a per-unit one (gels, supplements);
  - the new food is not one Alex doesn't eat, and is not a supplement;
  - a food eaten whole (pieza.entera: a bagel, a can of tuna, a breaded
    breast) comes in whole pieces — Alex, 2026-11-14: "o uso toda la pieza
    o nada";
  - the item's main macro (the one carrying most of its kcal) does not drop
    more than MACRO_DROP, and the day's kcal moves no more than DAY_KCAL;
  - the day keeps its protein floor and its fat floor, and day A is not cut further;
  - no micronutrient passes its upper limit even if every item of the day
    takes its worst swap at once — nothing remembers which ones he picked.
"""
MACRO_DROP = 0.25
DAY_KCAL = 0.05
# Day A is the leanest menu and Alex set its kcal himself (1,692 since
# 2026-11-14, under the old 1,830 energy-availability floor, on purpose).
# A swap may add to it but not take it more than this share lower.
DAY_A_MAX_DROP = 0.03
BANNED = {"espinaca", "almendra", "huevo"}   # egg only where the menu already has it
SESSION_MEALS = ("pre-entreno", "durante", "al terminar")
DAILY = "Diario"
_KCAL_PER_G = (("proteina_g", 4), ("carbohidrato_disponible_g", 4), ("grasa_g", 9))


def factor(food: dict, qty: float) -> float:
    return qty if food["valores_por"] == "1 unidad" else qty / 100


def main_macro(food: dict) -> str:
    """The macro that carries most of this food's kcal."""
    return max(_KCAL_PER_G, key=lambda m: food["macros"][m[0]] * m[1])[0]


def is_whole(food: dict) -> bool:
    return bool((food.get("pieza") or {}).get("entera"))


def in_whole_pieces(food: dict, qty: float) -> bool:
    """A whole-piece food comes in whole pieces — or in the step its pieza
    allows (a bagel halves: `paso` 0.5; a can of tuna does not)."""
    pieza = food["pieza"]
    n = qty / pieza["peso"] / pieza.get("paso", 1)
    return n > 0.99 and abs(n - round(n)) <= 0.01


def pieces_label(food: dict, qty) -> str | None:
    """"3 tostadas", "1 lata" — only for a food eaten whole, in whole pieces.
    Everything else is weighed and shown in grams (the pieces-first sheet for
    every food was rejected 2026-10-21; Alex kept it for these on 2026-11-14)."""
    pieza = food.get("pieza") or {}
    if not pieza.get("entera") or not isinstance(qty, (int, float)):
        return None
    if not in_whole_pieces(food, qty):
        return None
    n = round(qty / pieza["peso"] * 2) / 2
    name = pieza["nombre"]
    if n > 1:
        name = pieza.get("plural") or (name + ("s" if name[-1] in "aeiouáéíóú" else "es"))
    count = ("" if n < 1 else str(int(n))) + ("½" if n % 1 else "")
    return f"{count} {name}"


def swappable(meal: dict, food: dict) -> bool:
    name = (meal.get("nombre") or "").lower()
    return (meal.get("hora") != DAILY and not name.startswith(SESSION_MEALS)
            and food["valores_por"] != "1 unidad")


def item_swaps(item: dict) -> list:
    """Every swap on a menu item: the old single `alternativa`, then `alternativas`."""
    one = item.get("alternativa")
    return ([one] if one else []) + list(item.get("alternativas") or [])


def delta(orig: dict, orig_qty: float, new: dict, new_qty: float) -> dict:
    """What the day gains (or loses) when new replaces orig: macros and micros."""
    a, b = factor(orig, orig_qty), factor(new, new_qty)
    out = {k: new["macros"][k] * b - orig["macros"][k] * a for k in orig["macros"]}
    for k in set(orig.get("micros") or {}) | set(new.get("micros") or {}):
        out[k] = (new.get("micros") or {}).get(k, 0) * b - (orig.get("micros") or {}).get(k, 0) * a
    return out


def _day_raw(day_type: dict, foods: dict) -> dict:
    raw = {}
    for meal in day_type["comidas"]:
        for item in meal["items"]:
            food = foods[item["food_id"]]
            k = factor(food, item["cantidad"])
            for key, v in {**food["macros"], **(food.get("micros") or {})}.items():
                raw[key] = raw.get(key, 0.0) + v * k
    return raw


def _day_problems(code: str, base: dict, d: dict, protein_floor: float, fat_floor: float) -> list:
    """What a change of d does to the day's energy, protein and fat."""
    out = []
    kcal = base["kcal"] + d["kcal"]
    if abs(d["kcal"]) > DAY_KCAL * base["kcal"]:
        out.append(f"day kcal moves {d['kcal']:+.0f}")
    if code == "A" and d["kcal"] < -DAY_A_MAX_DROP * base["kcal"]:
        out.append(f"day A falls to {kcal:.0f} kcal")
    if base["proteina_g"] + d["proteina_g"] < protein_floor:
        out.append(f"day protein falls to {base['proteina_g'] + d['proteina_g']:.0f} g")
    # Only a change that takes fat out can break the fat floor: a day sitting
    # on the line is not broken by 10 g more carbohydrate. Day C sits under
    # it already (its gels are pure carbohydrate).
    if d["grasa_g"] <= -1 and base["grasa_g"] * 9 / base["kcal"] * 100 >= fat_floor > (base["grasa_g"] + d["grasa_g"]) * 9 / kcal * 100:
        out.append(f"day fat falls under {fat_floor}% of kcal")
    return out


def swap_problems(day_type: dict, foods: dict, rules: dict) -> list:
    """Why this day type's swaps can't ship, one line each. Empty = fine."""
    code = day_type["codigo"]
    metas = rules["metas"]
    protein_floor = metas["macros"]["proteina_g_por_kg_min"] * metas["perfil"]["peso_kg"]
    fat_floor = metas["macros"]["grasa_pct_kcal_min"]
    base = _day_raw(day_type, foods)
    worst = {}
    out = []
    for meal in day_type["comidas"]:
        meal_worst = {}
        for item in meal["items"]:
            orig = foods[item["food_id"]]
            item_worst = {}
            for swap in item_swaps(item):
                new = foods[swap["food_id"]]
                tag = f"day {code}, {meal['nombre']}: {item['food_id']} -> {swap['food_id']} {swap['cantidad']}"
                if not swappable(meal, orig):
                    out.append(f"{tag}: this item is never swapped")
                if swap["food_id"] in BANNED or new["valores_por"] == "1 unidad" or swap["food_id"] == "whey":
                    out.append(f"{tag}: not a food to swap in")
                if is_whole(new) and not in_whole_pieces(new, swap["cantidad"]):
                    out.append(f"{tag}: {new['pieza']['nombre']} is eaten whole")
                d = delta(orig, item["cantidad"], new, swap["cantidad"])
                macro = main_macro(orig)
                had = orig["macros"][macro] * factor(orig, item["cantidad"])
                if d[macro] < -MACRO_DROP * had:
                    out.append(f"{tag}: {macro} drops {-d[macro]:.0f} of {had:.0f} g")
                out += [f"{tag}: {why}" for why in _day_problems(code, base, d, protein_floor, fat_floor)]
                for k, v in d.items():
                    item_worst[k] = max(item_worst.get(k, 0.0), v)
            for k, v in item_worst.items():
                meal_worst[k] = meal_worst.get(k, 0.0) + v
        for var in meal.get("variantes") or []:
            tag = f"day {code}, {meal['nombre']}: variant {var['nombre']!r}"
            if meal.get("hora") == DAILY or (meal.get("nombre") or "").lower().startswith(SESSION_MEALS):
                out.append(f"{tag}: this meal is never swapped")
            d = {}
            already = {i["food_id"] for i in meal["items"]}   # what the meal has anyway is not "swapped in"
            for sign, items in ((-1, meal["items"]), (1, var["items"])):
                for item in items:
                    food = foods[item["food_id"]]
                    if sign == 1 and item["food_id"] not in already and (
                            item["food_id"] in BANNED or food["valores_por"] == "1 unidad" or item["food_id"] == "whey"):
                        out.append(f"{tag}: {item['food_id']} is not a food to swap in")
                    if sign == 1 and is_whole(food) and not in_whole_pieces(food, item["cantidad"]):
                        out.append(f"{tag}: {food['pieza']['nombre']} is eaten whole")
                    k = factor(food, item["cantidad"])
                    for key, v in {**food["macros"], **(food.get("micros") or {})}.items():
                        d[key] = d.get(key, 0.0) + sign * v * k
            out += [f"{tag}: {why}" for why in _day_problems(code, base, d, protein_floor, fat_floor)]
            for k, v in d.items():   # the variant replaces the meal, item swaps and all
                meal_worst[k] = max(meal_worst.get(k, 0.0), v)
        for k, v in meal_worst.items():
            worst[k] = worst.get(k, 0.0) + max(v, 0.0)
    for key, m in metas["micros"].items():
        limit = m.get("limite_superior")
        if limit and base.get(key, 0) <= limit < base.get(key, 0) + worst.get(key, 0):
            out.append(f"day {code}: {key} can reach {base.get(key, 0) + worst.get(key, 0):.0f} of {limit} with every swap taken")
    return out
