"""
Nutrition — today's menu from the athlete's nutrition package. Read-only.

The package (backend/data/nutrition/, README.md there) is authored outside the
app and regenerated as files: 4 day types (A rest/gym, B easy, C long run,
D quality) with timed meals and precomputed totals, plus a calendar that
GUESSED each date's type before any plan existed. Updating it is a file drop.

Python decides the day type from the stored weekly plan, not the calendar
(council 2026-10-21): chat, adapt-today and the Sunday pin move sessions, and
the calendar never hears about it. Highest load wins on a two-session day:
  C  a run titled as a long run (title only — find_long_run_day's
     longest-run fallback would turn an easy week's jog into a C day)
  D  any quality session (volume_gate.is_quality)
  B  any other cardio
  A  gym only, or rest (Alex, 2026-10-21: the package eats A on gym days)
The calendar still wins on its fixed days — an event, a sin_deficit day, or
PENDIENTE (no menu yet, eat C) — because the plan can't know "eat a full C
the day before the half". No stored plan: the calendar's guess, labelled.

Reads the stored plan only; never generates one. A menu card must not start
an LLM run, and a failed generation must not take the menu down with it.

Every meal belongs to its calendar date: the bedtime snack sits at 11:59 PM
(was 1:00 AM; moved 2026-10-21 so no meal crosses midnight). The loader
rejects a package whose meal times don't run forward through the day, so a
regenerated file that brings 1:00 AM back fails loudly instead of showing
the snack as the first meal of the morning.
"""
import json
from datetime import date, datetime, timedelta
from pathlib import Path

from backend.services.pace_enforcer import classify_run_workout
from backend.services.plan_normalizer import map_sport
from backend.services.volume_gate import is_quality

DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "nutrition"
DAY_TYPES = ("A", "B", "C", "D")
_RANK = {"A": 0, "B": 1, "D": 2, "C": 3}
PENDING = "PENDIENTE"
PENDING_FALLBACK = "C"
# A meal stays "next" until this long after its time, so the card doesn't
# skip past breakfast the minute the clock reads 12:31.
NEXT_MEAL_GRACE = timedelta(minutes=30)
_TIME_FMT = "%I:%M %p"


class NutritionPackageError(Exception):
    """The package on disk is missing or inconsistent."""


_cache: dict | None = None


def _parse_time(hora: str):
    try:
        return datetime.strptime((hora or "").strip(), _TIME_FMT).time()
    except ValueError:
        return None


def _read(name: str, data_dir: Path):
    path = data_dir / name
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise NutritionPackageError(f"{name} is missing")
    except json.JSONDecodeError as e:
        raise NutritionPackageError(f"{name} is not valid JSON: {e}")


def load_package(data_dir: Path = DATA_DIR) -> dict:
    """Read and check the package. Raises NutritionPackageError."""
    foods = {f["id"]: f for f in _read("alimentos.json", data_dir).get("alimentos") or []}
    types = {t["codigo"]: t for t in _read("tipos_de_dia.json", data_dir).get("tipos_de_dia") or []}
    calendar_doc = _read("calendario.json", data_dir)
    calendar = {d["fecha"]: d for d in calendar_doc.get("dias") or []}

    missing = [c for c in DAY_TYPES if c not in types]
    if missing:
        raise NutritionPackageError(f"day types missing: {', '.join(missing)}")
    for code, t in types.items():
        last = None
        for meal in t.get("comidas") or []:
            for item in meal.get("items") or []:
                if item.get("food_id") not in foods:
                    raise NutritionPackageError(f"day {code}: unknown food {item.get('food_id')!r}")
            if meal.get("hora") == "Diario":
                continue
            at = _parse_time(meal.get("hora"))
            if at is None:
                raise NutritionPackageError(f"day {code}: bad meal time {meal.get('hora')!r}")
            if last is not None and at <= last:
                raise NutritionPackageError(
                    f"day {code}: {meal.get('hora')} comes after {last.strftime(_TIME_FMT)} — "
                    "every meal must fall on its own date (bedtime snack at 11:59 PM)")
            last = at
    for fecha, d in calendar.items():
        if d.get("tipo_dia") not in types and d.get("tipo_dia") != PENDING:
            raise NutritionPackageError(f"calendar {fecha}: unknown day type {d.get('tipo_dia')!r}")
    return {"foods": foods, "types": types, "calendar": calendar,
            "version": calendar_doc.get("version")}


def package() -> dict:
    """The package, loaded on first use. A bad package raises on every call
    (nothing cached) so the fix is a file drop plus a restart, nothing more."""
    global _cache
    if _cache is None:
        _cache = load_package()
    return _cache


def day_type_from_plan(plan_day: dict | None) -> tuple:
    """(day type, reason) for one plan day."""
    best, why = "A", "Rest day"
    for w in (plan_day or {}).get("workouts") or []:
        if not isinstance(w, dict):
            continue
        raw_sport = (w.get("sport") or "").lower()
        sport = map_sport(raw_sport)
        title = w.get("title") or sport.title()
        if sport == "running" and "long run" in (w.get("title") or "").lower():
            code = "C"
        elif is_quality(w):
            code = "D"
        elif sport in ("running", "cycling", "swimming") or "elliptic" in raw_sport:
            code = "B"
        elif sport == "strength":
            code, title = "A", f"{title} (gym only)"
        else:
            continue
        if _RANK[code] > _RANK[best] or (code == best and why == "Rest day"):
            best, why = code, title
    return best, why


def _item_view(item: dict, foods: dict) -> dict:
    food = foods[item["food_id"]]
    pieza = food.get("pieza") or None
    amount = item.get("cantidad")
    out = {
        "food_id": item["food_id"],
        "name": food.get("nombre"),
        "amount": amount,
        "unit": food.get("unidad") if item.get("unidad") == "unidad" else item.get("unidad"),
        "note": food.get("nota"),
    }
    if pieza and pieza.get("peso") and isinstance(amount, (int, float)):
        out["pieces"] = round(amount / pieza["peso"], 1)
        out["piece_name"] = pieza.get("nombre")
    return out


def _totals_view(t: dict) -> dict:
    t = t or {}
    return {
        "kcal": t.get("kcal"),
        "protein_g": t.get("proteina_g"),
        "carbs_g": t.get("carbohidrato_g"),
        "fat_g": t.get("grasa_g"),
        "fiber_g": t.get("fibra_g"),
        "sodium_mg": t.get("sodio_mg"),
    }


def _meal_view(meal: dict, foods: dict) -> dict:
    return {
        "time": meal.get("hora"),
        "name": meal.get("nombre"),
        "items": [_item_view(i, foods) for i in meal.get("items") or []],
        "totals": _totals_view(meal.get("totales")),
    }


def today_menu(day: date, now: datetime, plan_json: dict | None, pkg: dict | None = None) -> dict:
    """The /nutrition/today payload for `day`, with the next meal as of `now`
    (local wall time). plan_json is the stored week holding `day`, or None."""
    pkg = pkg or package()
    cal = pkg["calendar"].get(day.isoformat()) or {}
    cal_type = cal.get("tipo_dia")
    plan_day = ((plan_json or {}).get("days") or {}).get(day.strftime("%A")) if plan_json else None

    if cal_type == PENDING:
        code, source = PENDING_FALLBACK, "calendar"
        reason = f"{cal.get('evento') or 'Special day'}: menu pending, eat a full C day"
    elif cal_type and (cal.get("evento") or cal.get("sin_deficit")):
        code, source = cal_type, "calendar"
        reason = cal.get("evento") or "No-deficit day"
    elif isinstance(plan_day, dict):
        code, reason = day_type_from_plan(plan_day)
        source = "plan"
    elif cal_type:
        code, source, reason = cal_type, "calendar", "No plan this week: calendar estimate"
    else:
        return {"date": day.isoformat(), "day_type": None, "reason": "No plan and no calendar entry",
                "package_version": pkg.get("version")}

    t = pkg["types"][code]
    timed = [m for m in t.get("comidas") or [] if m.get("hora") != "Diario"]
    daily = [m for m in t.get("comidas") or [] if m.get("hora") == "Diario"]
    meals = [_meal_view(m, pkg["foods"]) for m in timed]

    next_index = None
    clock = now.replace(tzinfo=None)
    for i, m in enumerate(timed):
        at = datetime.combine(day, _parse_time(m["hora"]))
        if clock < at + NEXT_MEAL_GRACE:
            next_index = i
            break

    return {
        "date": day.isoformat(),
        "day_type": code,
        "day_name": t.get("nombre"),
        "source": source,
        "reason": reason,
        "phase": cal.get("fase"),
        "event": cal.get("evento"),
        "no_deficit": bool(cal.get("sin_deficit")),
        "note": cal.get("nota"),
        "deficit_kcal": t.get("deficit_kcal"),
        "totals": _totals_view(t.get("totales")),
        "meals": meals,
        "next_meal_index": next_index,
        "supplements": [_item_view(i, pkg["foods"]) for m in daily for i in m.get("items") or []],
        "package_version": pkg.get("version"),
    }
