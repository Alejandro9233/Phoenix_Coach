"""
The athlete's profile and injury log: what Profile reads and saves.

Lived in main.py until 2026-11-14; its endpoints are thin wrappers now.
Saving the profile can change the plan (availability -> re-enforce this
week), which goes through plan_flows like every other plan write.
"""
from fastapi import HTTPException

from backend.models.database import Athlete, InjuryLog, RecoverySnapshot
from backend.services import plan_flows
from backend.services.constraint_enforcer import sport_days
from backend.utils.timezone import (
    get_local_today, get_timezone_name, is_valid_timezone, set_athlete_timezone,
)


def get_or_create_athlete(db):
    athlete = db.query(Athlete).first()
    if not athlete:
        # Create default athlete if none exists
        athlete = Athlete(name="New Athlete")
        db.add(athlete)
        db.commit()
        db.refresh(athlete)
    return athlete


def _prediction_block(db, athlete):
    """Riegel prediction for the goal race from the athlete's own race-effort
    activities. Read-only; the target stays whatever Alex set."""
    from backend.services.personal_model import get_model, race_prediction, _run_activities

    activities = _run_activities(db)
    model = get_model(db, athlete, activities)
    if not model or not model.get("lthr"):
        return None
    return race_prediction(activities, model["lthr"], athlete.race_distance,
                           athlete.target_finish_time, bests=model.get("bests"))


def _coros_prediction_block(db, athlete):
    """COROS's own race predictions (stored daily by the MCP sync since
    2026-10-08): today's half and marathon, the change since the oldest
    value in the last 90 days, and the gap to the athlete's goal when the goal
    distance is one of the two. None until a row carries them."""
    from datetime import timedelta
    from backend.services.pace_model import fmt_hms

    today = get_local_today()
    rows = db.query(RecoverySnapshot).filter(
        RecoverySnapshot.date >= today - timedelta(days=90),
        (RecoverySnapshot.pred_half_s.isnot(None)) | (RecoverySnapshot.pred_marathon_s.isnot(None)),
    ).order_by(RecoverySnapshot.date.desc()).all()
    if not rows:
        return None
    latest, oldest = rows[0], rows[-1]

    def _delta(attr):
        a, b = getattr(latest, attr), getattr(oldest, attr)
        return int(a - b) if a is not None and b is not None and latest.date != oldest.date else None

    def _parse_hms(text):
        try:
            parts = [int(x) for x in str(text).split(":")]
        except (TypeError, ValueError):
            return None
        return parts[0] * 3600 + parts[1] * 60 + (parts[2] if len(parts) > 2 else 0) if len(parts) in (2, 3) else None

    goal_attr = {"Marathon": "pred_marathon_s", "Half Marathon": "pred_half_s", "Half": "pred_half_s"}.get(
        athlete.race_distance or "")
    goal_s = _parse_hms(athlete.target_finish_time) if athlete.target_finish_time else None
    goal_pred = getattr(latest, goal_attr) if goal_attr else None
    gap_s = int(goal_pred - goal_s) if goal_pred is not None and goal_s else None
    return {
        "date": latest.date.isoformat(),
        "half": fmt_hms(latest.pred_half_s) if latest.pred_half_s else None,
        "half_sec": latest.pred_half_s,
        "marathon": fmt_hms(latest.pred_marathon_s) if latest.pred_marathon_s else None,
        "marathon_sec": latest.pred_marathon_s,
        "since_date": oldest.date.isoformat(),
        "days_tracked": (latest.date - oldest.date).days + 1,
        "half_delta_sec": _delta("pred_half_s"),
        "marathon_delta_sec": _delta("pred_marathon_s"),
        "goal_distance": athlete.race_distance if goal_attr else None,
        "goal_gap_sec": gap_s,
        "goal_gap_pct": round(gap_s / goal_s * 100, 1) if gap_s is not None and goal_s else None,
    }


def personal_block(db, athlete):
    """The Recent tab's long-run ledger. None until an LTHR is known."""
    from backend.services.personal_model import get_model, long_run_ledger, _run_activities

    if athlete is None:
        return None
    activities = _run_activities(db)
    model = get_model(db, athlete, activities)
    if not model or not model.get("lthr"):
        return None
    return {"ledger": long_run_ledger(activities, model["lthr"]),
            "model_runs": (model.get("hr_model") or {}).get("n")}


def get_profile(db):
    """Return the athlete's profile including race objectives and schedule."""
    athlete = db.query(Athlete).first()
    if not athlete:
        athlete = Athlete(name="New Athlete")
        db.add(athlete)
        db.commit()
        db.refresh(athlete)

    return {
        "name": athlete.name,
        "age": athlete.age,
        "weight_kg": athlete.weight_kg,
        "race_name": athlete.race_name,
        "race_type": athlete.race_type or "Triathlon",
        "race_distance": athlete.race_distance,
        "race_date": str(athlete.race_date) if athlete.race_date else None,
        **sport_days(athlete),
        "target_finish_time": athlete.target_finish_time,
        "training_start_date": str(athlete.training_start_date) if athlete.training_start_date else None,
        "timezone": athlete.timezone,
        "active_timezone": get_timezone_name(),
        "prediction": _prediction_block(db, athlete),
        "coros_prediction": _coros_prediction_block(db, athlete),
    }


def update_profile(db, body: dict):
    """Update the athlete's profile."""
    from datetime import date as date_type

    athlete = db.query(Athlete).first()
    if not athlete:
        athlete = Athlete(name="New Athlete")
        db.add(athlete)

    # A disabled sport must not leave ghost sessions in the stored week, so
    # availability changes re-enforce the plan below. Snapshot to detect them.
    availability_fields = ("swim_days", "bike_days", "run_days", "strength_days")
    old_availability = {f: getattr(athlete, f, None) for f in availability_fields}

    # Update only provided fields
    updatable = [
        "name", "age", "weight_kg", "race_name", "race_type", "race_distance",
        "swim_days", "bike_days", "run_days", "strength_days", "target_finish_time",
    ]
    for field in updatable:
        if field in body:
            setattr(athlete, field, body[field])

    # Handle race_date specially (string → date)
    if "race_date" in body:
        rd = body["race_date"]
        if rd:
            athlete.race_date = date_type.fromisoformat(rd)
        else:
            athlete.race_date = None
            
    # Handle training_start_date specially (string → date)
    if "training_start_date" in body:
        tsd = body["training_start_date"]
        if tsd:
            athlete.training_start_date = date_type.fromisoformat(tsd)
        else:
            athlete.training_start_date = None

    # The phone reports its current timezone, so travelling updates this on its
    # own. Ignore garbage rather than 400-ing — this rides along with unrelated
    # profile saves and must never block them.
    if "timezone" in body:
        tz = body["timezone"]
        if tz and is_valid_timezone(tz):
            if athlete.timezone != tz:
                print(f"🕒 Timezone changed: {athlete.timezone} → {tz}")
            athlete.timezone = tz
            set_athlete_timezone(tz)
        elif tz:
            print(f"⚠️ Ignoring invalid timezone from client: {tz!r}")

    db.commit()
    db.refresh(athlete)
    print(f"Profile updated: {athlete.name}, race={athlete.race_name} on {athlete.race_date}, start={athlete.training_start_date}")

    # A prompt-only constraint is a suggestion; the stored week must obey the
    # new availability too. iOS never decodes this body, so the extra key is
    # safe — a future nudge ("Replan remaining week?") can read it.
    response = {"status": "ok", "message": "Profile updated"}
    if any(getattr(athlete, f) != old_availability[f] for f in availability_fields):
        enforcement = plan_flows.reenforce_current_week(db, athlete)
        if enforcement is not None:
            response["plan_enforcement"] = enforcement
    return response

def list_injuries(db):
    from backend.models.database import InjuryLog
    athlete = db.query(Athlete).first()
    if not athlete:
        return []
    injuries = db.query(InjuryLog).filter(InjuryLog.athlete_id == athlete.id).order_by(InjuryLog.date_reported.desc()).all()
    return [
        {
            "id": inj.id,
            "date_reported": str(inj.date_reported) if inj.date_reported else None,
            "body_part": inj.body_part,
            "status": inj.status,
            "severity": inj.severity,
            "notes": inj.notes,
            "affected_sports": inj.affected_sports,
            "expected_recovery_date": str(inj.expected_recovery_date) if inj.expected_recovery_date else None,
        } for inj in injuries
    ]

def create_injury(db, body: dict):
    from backend.models.database import InjuryLog
    from datetime import date
    
    athlete = db.query(Athlete).first()
    if not athlete:
        raise HTTPException(status_code=404, detail="Athlete not found")
        
    injury = InjuryLog(
        athlete_id=athlete.id,
        date_reported=date.fromisoformat(body["date_reported"]) if "date_reported" in body and body["date_reported"] else get_local_today(),
        body_part=body.get("body_part"),
        status=body.get("status", "Active"),
        severity=body.get("severity"),
        notes=body.get("notes"),
        affected_sports=body.get("affected_sports"),
        # Optional. NULL means the injury blocks training until resolved by hand.
        expected_recovery_date=(
            date.fromisoformat(body["expected_recovery_date"])
            if body.get("expected_recovery_date") else None
        ),
    )
    db.add(injury)
    db.commit()
    db.refresh(injury)
    # The stored week must obey the injury now, not at the next plan write:
    # blocked sports are stripped and a shoulder gets its gym sessions
    # rewritten (shoulder_guard). Pure Python, today forward.
    response = {"status": "ok", "message": "Injury logged", "id": injury.id}
    enforcement = plan_flows.reenforce_current_week(
        db, athlete, reason=f"Injury logged: {injury.body_part}")
    if enforcement is not None:
        response["plan_enforcement"] = enforcement
    return response

def update_injury(db, injury_id: int, body: dict):
    from backend.models.database import InjuryLog
    injury = db.query(InjuryLog).filter(InjuryLog.id == injury_id).first()
    if not injury:
        raise HTTPException(status_code=404, detail="Injury not found")
        
    for field in ["status", "severity", "notes", "affected_sports", "body_part"]:
        if field in body:
            setattr(injury, field, body[field])

    if "expected_recovery_date" in body:
        from datetime import date as date_type
        erd = body["expected_recovery_date"]
        injury.expected_recovery_date = date_type.fromisoformat(erd) if erd else None

    db.commit()
    return {"status": "ok", "message": "Injury updated"}


def delete_injury(db, injury_id: int):
    """Remove an injury row outright.

    Distinct from resolving: Resolved keeps the history `data_agent` reads back,
    delete is for a row logged by mistake. iOS offered a Delete swipe from the
    start and only ever removed it from the local array, so the row returned on
    the next fetch — and, if it was Active, kept stripping sessions.
    """
    from backend.models.database import InjuryLog
    injury = db.query(InjuryLog).filter(InjuryLog.id == injury_id).first()
    if not injury:
        raise HTTPException(status_code=404, detail="Injury not found")
    db.delete(injury)
    db.commit()
    return {"status": "ok", "message": "Injury deleted"}
