"""Personal model — small fitted models of this one athlete, from scraped activities.

WHY NOT A NEURAL NETWORK: one athlete, ~200 runs. A net would learn nothing
a four-coefficient regression can't, and would hide what it learned. Every
model here is a handful of numbers you can read in the DB row.

WHAT IT PRODUCES (Python = GPS: these are numbers, the LLM reads them):

1. HR baseline — HR = a + b*speed + c*temp + d*climb, fitted on steady
   outdoor runs. A new run's residual (actual − expected) is the fitness
   signal: negative = fitter than your own average, +4 or more = running hot.
2. Intensity — avg HR as a share of LTHR, mapped to Friel zones. Replaces a
   string that was hardcoded in the iOS activity view.
3. Long-run ledger — count of runs >= 20 km under 90% LTHR in the last 16
   weeks. The one honest marathon-readiness number.
4. Race prediction — Riegel from the longest race-effort activity in the
   last year, against the target time. Constants come from pace_model so
   the plan's M-pace band and this card can never disagree.

DATA SOURCE IS THE SCRAPER, NOT FIT FILES. Alex won't import FIT often
(2026-09-08), so everything fits from activities.avg_hr / avg_speed_ms /
total_ascent_m / distance_m. Two consequences:
- Temperature is a month-of-year proxy (MONTH_TEMP_C), seeded from 20 months
  of watch lap temperatures. Hermosillo swings 20 °C → 34 °C; ignoring it
  would read every August as lost fitness.
- Treadmill is detected by total_ascent_m == 0 (no sub_sport from the
  scraper). Outdoor runs here always log some ascent.

REFIT is lazy: get_model() refits when the stored model is older than
REFIT_DAYS. A refit that can't reach MIN_FIT_RUNS keeps the previous model
(possibly the FIT-export seed from scripts/seed_personal_model.py) and only
bumps checked_at — a thin prod history must not erase a good seed.

STORAGE: one JSON on athletes.personal_model, ~a dozen numbers. No per-
activity residuals are persisted — computed on read (DB space is scarce).

Pure module except get_model()/activity_insight()/coach_lines(), which take
a db session only to read activities and write the athlete row.
"""
from datetime import datetime, timedelta, timezone

from backend.services.pace_model import RACE_KM, RIEGEL_EXP, fmt_hms, parse_hms, riegel
from backend.utils.timezone import get_local_today

# Mean lap temperature by month, Hermosillo, from 2024-12..2026-09 watch data.
MONTH_TEMP_C = {1: 23, 2: 25, 3: 27, 4: 30, 5: 30, 6: 31,
                7: 34, 8: 34, 9: 27, 10: 22, 11: 20, 12: 21}

MIN_FIT_RUNS = 20          # below this the regression is noise
REFIT_DAYS = 7
STEADY_LO, STEADY_HI = 0.70, 0.95   # avg_hr / LTHR band used for fitting and residuals
OUTLIER_SD = 2.5
MIN_RUN_M = 3000
MIN_RUN_SEC = 1500
LTHR_MIN_SEC = 2700        # LTHR estimate = best avg HR over a 45-min+ run
LTHR_MIN_RUNS = 3

HOT_BPM = 4.0              # residual at/above this = "running hot"
FIT_BPM = -3.0             # residual at/below this = "fitter than expected"

LONG_RUN_KM = 20.0
LONG_RUN_HR_FRAC = 0.90
LEDGER_WEEKS = 16

RACE_EFFORT_FRAC = 0.95    # avg_hr / LTHR at/above this counts as a race effort
PREDICTION_LOOKBACK_DAYS = 365
PREDICTION_MIN_KM = 5.0

ZONES = [(0.85, "Z1", "easy"), (0.90, "Z2", "steady"), (0.95, "Z3", "tempo"),
         (1.00, "Z4", "threshold")]


# ---------------------------------------------------------------- selection

def _is_run(a):
    return (a.sport or "").lower() == "running"


def _speed(a):
    """Ingestion stores 0 when COROS omits avgSpeed (two thirds of prod runs
    on 2026-09-08); distance / duration is the same quantity."""
    if a.avg_speed_ms:
        return float(a.avg_speed_ms)
    if a.distance_m and a.duration_sec:
        return a.distance_m / a.duration_sec
    return None


def _is_outdoor_run(a):
    return (_is_run(a) and a.start_time is not None
            and (a.distance_m or 0) >= MIN_RUN_M
            and (a.duration_sec or 0) >= MIN_RUN_SEC
            and a.avg_hr and _speed(a)
            and (a.total_ascent_m or 0) > 0)


def _is_steady(a, lthr):
    frac = a.avg_hr / lthr
    return STEADY_LO <= frac < STEADY_HI


def month_temp(dt):
    return MONTH_TEMP_C[dt.month]


def _features(a):
    km = (a.distance_m or 0) / 1000.0
    return [1.0, _speed(a), float(month_temp(a.start_time)),
            (a.total_ascent_m or 0.0) / km if km else 0.0]


# ---------------------------------------------------------------- LTHR

def estimate_lthr(athlete, activities):
    """COROS LTHR if the watch reported one; else best avg HR over a 45-min+
    run — the same proxy the FIT analysis validated (185 vs 185)."""
    if athlete is not None and athlete.lthr:
        return int(athlete.lthr), "coros"
    hrs = sorted((a.avg_hr for a in activities
                  if _is_run(a) and a.avg_hr and (a.duration_sec or 0) >= LTHR_MIN_SEC),
                 reverse=True)
    if len(hrs) < LTHR_MIN_RUNS:
        return None, None
    return int(hrs[0]), "estimated"


# ---------------------------------------------------------------- regression

def _solve(A, b):
    """Gaussian elimination with partial pivoting. n is 4; numpy isn't a dep."""
    n = len(A)
    M = [row[:] + [b[i]] for i, row in enumerate(A)]
    for c in range(n):
        p = max(range(c, n), key=lambda r: abs(M[r][c]))
        if abs(M[p][c]) < 1e-12:
            return None
        M[c], M[p] = M[p], M[c]
        for r in range(n):
            if r == c:
                continue
            f = M[r][c] / M[c][c]
            for k in range(c, n + 1):
                M[r][k] -= f * M[c][k]
    return [M[i][n] / M[i][i] for i in range(n)]


def _ols(X, y):
    n = len(X[0])
    A = [[sum(x[i] * x[j] for x in X) for j in range(n)] for i in range(n)]
    b = [sum(x[i] * yi for x, yi in zip(X, y)) for i in range(n)]
    return _solve(A, b)


def fit_hr_model(activities, lthr):
    """Fit HR = a + b*speed + c*temp + d*climb on steady outdoor runs, with one
    outlier-trim pass. Returns None below MIN_FIT_RUNS."""
    if not lthr:
        return None
    runs = [a for a in activities if _is_outdoor_run(a) and _is_steady(a, lthr)]
    if len(runs) < MIN_FIT_RUNS:
        return None
    X = [_features(a) for a in runs]
    y = [float(a.avg_hr) for a in runs]
    coef = _ols(X, y)
    if coef is None:
        return None
    resid = [yi - sum(c * xi for c, xi in zip(coef, x)) for x, yi in zip(X, y)]
    sd = (sum(r * r for r in resid) / len(resid)) ** 0.5
    keep = [i for i, r in enumerate(resid) if abs(r) <= OUTLIER_SD * sd] if sd > 0 else list(range(len(resid)))
    if MIN_FIT_RUNS <= len(keep) < len(runs):
        X = [X[i] for i in keep]
        y = [y[i] for i in keep]
        coef2 = _ols(X, y)
        if coef2 is not None:
            coef = coef2
            resid = [yi - sum(c * xi for c, xi in zip(coef, x)) for x, yi in zip(X, y)]
            sd = (sum(r * r for r in resid) / len(resid)) ** 0.5
    return {
        "intercept": round(coef[0], 3), "speed": round(coef[1], 3),
        "temp": round(coef[2], 3), "ascent": round(coef[3], 3),
        "n": len(y), "rmse": round(sd, 2), "lthr": lthr,
    }


def predict_hr(model, a):
    x = _features(a)
    return (model["intercept"] + model["speed"] * x[1]
            + model["temp"] * x[2] + model["ascent"] * x[3])


def hr_residual(model, a, lthr):
    """actual − expected, bpm. Only defined on steady outdoor runs: the fit
    never saw race efforts, so a residual there would be fiction."""
    if not model or not lthr or not _is_outdoor_run(a) or not _is_steady(a, lthr):
        return None
    return round(a.avg_hr - predict_hr(model, a), 1)


def residual_label(r):
    if r is None:
        return None
    if r >= HOT_BPM:
        return "Running hot"
    if r <= FIT_BPM:
        return "Fitter than expected"
    return "On baseline"


# ---------------------------------------------------------------- intensity

def intensity(a, lthr):
    if not lthr or not a.avg_hr:
        return None
    frac = a.avg_hr / lthr
    for top, zone, label in ZONES:
        if frac < top:
            return {"zone": zone, "label": label, "pct_lthr": round(frac * 100)}
    return {"zone": "Z5", "label": "above threshold", "pct_lthr": round(frac * 100)}


# ---------------------------------------------------------------- ledger

def long_run_ledger(activities, lthr, today=None):
    if not lthr:
        return None
    today = today or get_local_today()
    cutoff = datetime.combine(today - timedelta(weeks=LEDGER_WEEKS), datetime.min.time())
    longs = [a for a in activities
             if _is_run(a) and a.start_time and a.start_time >= cutoff
             and (a.distance_m or 0) >= LONG_RUN_KM * 1000]
    easy = [a for a in longs if a.avg_hr and a.avg_hr < LONG_RUN_HR_FRAC * lthr]
    last = max((a.start_time for a in easy), default=None)
    return {
        "weeks": LEDGER_WEEKS, "min_km": LONG_RUN_KM,
        "long_runs": len(longs), "easy_long_runs": len(easy),
        "last_easy_long_run": last.date().isoformat() if last else None,
    }


# ---------------------------------------------------------------- prediction

def race_prediction(activities, lthr, race_distance, target_finish_time, today=None, bests=None):
    """Riegel from the LONGEST race effort in the last year. Longest, not the
    fastest prediction: a 5k best extrapolates a marathon 10 min too optimistic
    (FIT analysis, 2026-09-09). Candidates are scraped runs at/above
    RACE_EFFORT_FRAC of LTHR plus `bests` seeded from the FIT export — prod's
    scrape history starts 2026-05-20, after the March half that is the real
    basis. Each best is {km, sec, date}."""
    if not lthr:
        return None
    target_km = RACE_KM.get(race_distance)
    if not target_km:
        return None
    today = today or get_local_today()
    cutoff = today - timedelta(days=PREDICTION_LOOKBACK_DAYS)
    cands = []
    for a in activities:
        if (_is_run(a) and a.start_time and a.start_time.date() >= cutoff
                and a.avg_hr and a.avg_hr >= RACE_EFFORT_FRAC * lthr
                and (a.distance_m or 0) >= PREDICTION_MIN_KM * 1000 and a.duration_sec):
            cands.append((a.distance_m / 1000.0, float(a.duration_sec), a.start_time.date()))
    for b in bests or []:
        try:
            d = datetime.fromisoformat(b["date"]).date()
        except (KeyError, ValueError, TypeError):
            continue
        if d >= cutoff and b.get("km", 0) >= PREDICTION_MIN_KM and b.get("sec"):
            cands.append((float(b["km"]), float(b["sec"]), d))
    if not cands:
        return None
    # longest distance wins; among equals, the faster one
    km, sec, when = max(cands, key=lambda c: (round(c[0], 1), -c[1] / c[0]))
    pred = riegel(sec, km, target_km)
    out = {
        "basis_km": round(km, 2), "basis_time": fmt_hms(sec), "basis_date": when.isoformat(),
        "distance": race_distance, "predicted": fmt_hms(pred), "predicted_sec": round(pred),
        "exponent": RIEGEL_EXP, "target": target_finish_time, "gap_pct": None,
    }
    tsec = parse_hms(target_finish_time)
    if tsec:
        out["gap_pct"] = round((pred / tsec - 1) * 100, 1)
    return out


# ---------------------------------------------------------------- persistence

def _run_activities(db):
    from backend.models.database import Activity
    return db.query(Activity).filter(Activity.sport == "running").all()


def _utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _stale(model):
    checked = model.get("checked_at") if model else None
    if not checked:
        return True
    try:
        return datetime.fromisoformat(checked) < _utcnow() - timedelta(days=REFIT_DAYS)
    except ValueError:
        return True


def get_model(db, athlete, activities=None, force=False, commit=True):
    """The stored model, refit if older than REFIT_DAYS. A failed refit keeps
    the previous hr_model (seed included) and only bumps checked_at.

    commit=False for callers inside a larger transaction (data_agent runs
    under regenerate's delete-then-rollback; a commit there would persist the
    plan delete). The refit still lands on the athlete object and rides the
    caller's commit or rollback."""
    if athlete is None:
        return None
    stored = dict(athlete.personal_model or {})
    if athlete.lthr and stored.get("lthr") != int(athlete.lthr):
        # The watch's LTHR is what the rest of the app (zones, pace model,
        # coach context) already uses; the seed's estimate must not compete.
        stored["lthr"], stored["lthr_source"] = int(athlete.lthr), "coros"
        force = True
    if not force and not _stale(stored):
        return stored
    activities = activities if activities is not None else _run_activities(db)
    lthr, source = estimate_lthr(athlete, activities)
    fitted = fit_hr_model(activities, lthr)
    now = _utcnow().isoformat(timespec="seconds")
    if fitted:
        stored["hr_model"] = fitted
        stored["fitted_at"] = now
        stored["source"] = "scraper"
    stored["lthr"] = lthr if lthr else stored.get("lthr")
    stored["lthr_source"] = source if lthr else stored.get("lthr_source")
    stored["checked_at"] = now
    athlete.personal_model = stored
    from sqlalchemy.orm.attributes import flag_modified
    flag_modified(athlete, "personal_model")
    if commit:
        db.commit()
    return stored


def activity_insight(db, athlete, activity, model=None):
    """What the iOS activity view shows instead of the old placeholders."""
    model = model if model is not None else get_model(db, athlete)
    lthr = (model or {}).get("lthr")
    r = hr_residual((model or {}).get("hr_model"), activity, lthr)
    return {
        "hr_residual_bpm": r,
        "hr_residual_label": residual_label(r),
        "intensity": intensity(activity, lthr),
    }


def coach_lines(db, athlete, today=None):
    """Prompt lines for data_agent. Numbers only; the coach writes the words."""
    activities = _run_activities(db)
    model = get_model(db, athlete, activities, commit=False)
    if not model or not model.get("lthr"):
        return []
    lthr = model["lthr"]
    lines = ["", f"PERSONAL MODEL (LTHR {lthr} bpm, {model.get('lthr_source')}):"]
    hr_model = model.get("hr_model")
    if hr_model:
        recent = sorted((a for a in activities if a.start_time), key=lambda a: a.start_time, reverse=True)
        rs = []
        for a in recent:
            r = hr_residual(hr_model, a, lthr)
            if r is not None:
                rs.append((a.start_time.date().isoformat(), r))
            if len(rs) == 3:
                break
        if rs:
            parts = ", ".join(f"{d} {r:+.1f}" for d, r in rs)
            lines.append(f"  HR vs your baseline on last steady runs (bpm, +hot/−fit): {parts}"
                         f" -> latest: {residual_label(rs[0][1])}")
        lines.append(f"  Heat model: {hr_model['temp']:+.2f} bpm per °C; this month's proxy {month_temp(datetime.combine(today or get_local_today(), datetime.min.time()))} °C")
    ledger = long_run_ledger(activities, lthr, today)
    if ledger:
        lines.append(f"  Long-run ledger, last {ledger['weeks']} wk: {ledger['easy_long_runs']} easy runs"
                     f" >= {ledger['min_km']:.0f} km under 90% LTHR ({ledger['long_runs']} total that distance)")
    pred = race_prediction(activities, lthr, athlete.race_distance, athlete.target_finish_time, today,
                           bests=model.get("bests"))
    if pred:
        gap = f"; target {pred['target']} -> {pred['gap_pct']:+.0f}% gap" if pred.get("gap_pct") is not None else ""
        lines.append(f"  Race prediction: {pred['distance']} {pred['predicted']} from"
                     f" {pred['basis_km']:g} km in {pred['basis_time']} on {pred['basis_date']}{gap}")
    return lines
