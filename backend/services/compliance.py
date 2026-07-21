"""
Compliance Service — Matches actual activities against the weekly plan.

For each day in the weekly plan, finds matching activities by sport + date,
then computes per-workout compliance scores:
  - Duration: 80-120% of planned → "completed", else "mismatch"
  - HR: Was avg_hr inside the target zone?
  - Distance: 80-120% of planned → "completed"

Overall score is a weighted average of these components.
"""
import re
from datetime import date, datetime, timedelta
from typing import Optional
from sqlalchemy.orm import Session
from backend.models.database import Activity, WeeklyPlan


# Map sport strings to allow fuzzy matching between plan and actual
SPORT_ALIASES = {
    "running": ["running", "trail_running"],
    "cycling": ["cycling", "indoor_cycling"],
    "swimming": ["swimming", "open_water_swimming"],
    "strength": ["strength", "training", "other"],
    "rest": ["rest"],
}


def _normalize_sport(sport: str) -> str:
    """Normalize a sport string to its canonical form."""
    sport_lower = sport.lower().replace(" ", "_")
    for canonical, aliases in SPORT_ALIASES.items():
        if sport_lower in aliases:
            return canonical
    return sport_lower


def _parse_duration_to_minutes(duration_str: str) -> Optional[float]:
    """Parse duration strings like '45 min', '1h 30m', '30:00' to minutes."""
    if not duration_str or duration_str == "--":
        return None

    # "45 min" or "45min"
    m = re.match(r"(\d+)\s*min", duration_str, re.IGNORECASE)
    if m:
        return float(m.group(1))

    # "1h 30m" or "1h30m"
    m = re.match(r"(\d+)\s*h\s*(\d+)?\s*m?", duration_str, re.IGNORECASE)
    if m:
        hours = int(m.group(1))
        mins = int(m.group(2)) if m.group(2) else 0
        return hours * 60 + mins

    # "MM:SS" format used in workout steps
    m = re.match(r"(\d+):(\d+)", duration_str)
    if m:
        return int(m.group(1)) + int(m.group(2)) / 60

    return None


def _parse_hr_target(hr_str: str) -> Optional[tuple]:
    """Parse HR target strings like '120-145 bpm' to (low, high) tuple."""
    if not hr_str or hr_str in ("--", "N/A", "Easy pace"):
        return None

    m = re.search(r"(\d+)\s*[-–]\s*(\d+)", hr_str)
    if m:
        return (int(m.group(1)), int(m.group(2)))

    # Single value like "< 120 bpm"
    m = re.search(r"<\s*(\d+)", hr_str)
    if m:
        return (0, int(m.group(1)))

    return None


def _compute_workout_compliance(
    planned_workout: dict,
    actual_activity: dict,
) -> dict:
    """
    Compute compliance score for a single planned workout vs actual activity.

    Returns a dict with:
      - score: 0-100
      - duration_pct: actual/planned as percentage
      - hr_on_target: bool
      - status: "completed" | "mismatch" | "partial"
      - notes: human-readable explanation
    """
    notes = []
    scores = []

    # --- Duration compliance ---
    planned_total_time = planned_workout.get("total_time")
    planned_minutes = _parse_duration_to_minutes(planned_total_time)
    actual_duration_sec = actual_activity.get("duration_sec") or 0
    actual_minutes = actual_duration_sec / 60

    duration_pct = None
    if planned_minutes and planned_minutes > 0:
        duration_pct = round((actual_minutes / planned_minutes) * 100)

        if 80 <= duration_pct <= 120:
            scores.append(100)
            if duration_pct > 105:
                notes.append(f"Duration {duration_pct}% of planned (slightly over)")
            elif duration_pct < 95:
                notes.append(f"Duration {duration_pct}% of planned (slightly under)")
            else:
                notes.append("Duration on target")
        else:
            # Outside 80-120% range → mismatch
            scores.append(max(0, 100 - abs(duration_pct - 100)))
            if duration_pct > 120:
                notes.append(f"Duration {duration_pct}% of planned — significantly over")
            else:
                notes.append(f"Duration {duration_pct}% of planned — significantly under")

    # --- HR compliance ---
    hr_target = _parse_hr_target(planned_workout.get("hr_target", ""))
    actual_avg_hr = actual_activity.get("avg_hr")
    hr_on_target = None

    if hr_target and actual_avg_hr:
        low, high = hr_target
        # Allow ±5 bpm tolerance
        if low - 5 <= actual_avg_hr <= high + 5:
            hr_on_target = True
            scores.append(100)
            notes.append(f"Avg HR {actual_avg_hr} bpm — within target zone")
        else:
            hr_on_target = False
            deviation = min(abs(actual_avg_hr - low), abs(actual_avg_hr - high))
            scores.append(max(0, 100 - deviation * 3))
            if actual_avg_hr > high:
                notes.append(f"Avg HR {actual_avg_hr} bpm — {actual_avg_hr - high} bpm above target")
            else:
                notes.append(f"Avg HR {actual_avg_hr} bpm — {low - actual_avg_hr} bpm below target")

    # --- Distance compliance ---
    planned_distance_km = None  # Would need to parse from steps/description
    actual_distance_m = actual_activity.get("distance_m") or 0
    actual_distance_km = actual_distance_m / 1000 if actual_distance_m else 0
    distance_pct = None

    # We don't have explicit planned distance in the workout schema, skip for now

    # --- Overall score ---
    if scores:
        overall_score = round(sum(scores) / len(scores))
    else:
        overall_score = 75  # Default when we can't compute

    # Determine status
    is_duration_ok = duration_pct is None or (80 <= duration_pct <= 120)
    if is_duration_ok and (hr_on_target is None or hr_on_target):
        status = "completed"
    elif is_duration_ok or (hr_on_target is not None and hr_on_target):
        status = "partial"
    else:
        status = "mismatch"

    return {
        "score": overall_score,
        "status": status,
        "duration_pct": duration_pct,
        "hr_on_target": hr_on_target,
        "notes": " ".join(notes) if notes else "Completed.",
    }


def get_weekly_plan_status(db: Session) -> Optional[dict]:
    """
    Returns the current weekly plan enriched with actual completion data.

    For each day, adds an "actual" key containing:
      - completed: bool
      - skipped: bool (past day with no activity)
      - activities: list of matched activities
      - compliance: per-workout compliance data
    
    Also adds a "week_progress" key at the top level.
    """
    today = date.today()
    start_of_week = today - timedelta(days=today.weekday())  # Monday
    end_of_week = start_of_week + timedelta(days=6)  # Sunday

    # Get the weekly plan
    plan_record = db.query(WeeklyPlan).filter(
        WeeklyPlan.week_start == start_of_week
    ).order_by(WeeklyPlan.id.desc()).first()

    if not plan_record:
        return None

    from backend.services.plan_normalizer import normalize_plan
    plan_json = normalize_plan(plan_record.plan_json)
    days_dict = plan_json.get("days", {})

    # Get all activities for this week
    week_activities = db.query(Activity).filter(
        Activity.start_time >= datetime.combine(start_of_week, datetime.min.time()),
        Activity.start_time <= datetime.combine(end_of_week, datetime.max.time()),
    ).order_by(Activity.start_time).all()

    # Group activities by day name
    activities_by_day = {}
    for act in week_activities:
        if act.start_time:
            day_name = act.start_time.strftime("%A")
            if day_name not in activities_by_day:
                activities_by_day[day_name] = []
            activities_by_day[day_name].append(act)

    # Day ordering for determining past/future
    day_names_ordered = [
        "Monday", "Tuesday", "Wednesday", "Thursday",
        "Friday", "Saturday", "Sunday"
    ]
    today_name = today.strftime("%A")
    today_idx = day_names_ordered.index(today_name) if today_name in day_names_ordered else -1

    # Enrich each day
    enriched_days = {}
    total_planned_sessions = 0
    total_completed_sessions = 0
    total_planned_minutes = 0
    total_actual_minutes = 0
    total_actual_load = 0

    for day_name in day_names_ordered:
        day_plan = days_dict.get(day_name)
        if not day_plan:
            enriched_days[day_name] = {
                "summary": "No plan",
                "workouts": [],
                "actual": {"completed": False, "skipped": False, "is_rest": True,
                           "activities": [], "compliance": None}
            }
            continue

        planned_workouts = day_plan.get("workouts", [])
        day_idx = day_names_ordered.index(day_name)
        is_past = day_idx < today_idx
        is_today = day_idx == today_idx
        is_future = day_idx > today_idx

        # Check if this is a rest day
        is_rest_day = (
            not planned_workouts
            or all(w.get("sport", "").lower() == "rest" for w in planned_workouts)
        )

        # Count planned sessions (non-rest)
        non_rest_workouts = [w for w in planned_workouts if w.get("sport", "").lower() != "rest"]
        total_planned_sessions += len(non_rest_workouts)

        # Sum planned minutes
        for w in non_rest_workouts:
            pm = _parse_duration_to_minutes(w.get("total_time", ""))
            if pm:
                total_planned_minutes += pm

        # Find matching activities
        day_activities = activities_by_day.get(day_name, [])
        matched_activities = []
        workout_compliance = []

        for workout in non_rest_workouts:
            planned_sport = _normalize_sport(workout.get("sport", ""))

            # Find best matching activity
            best_match = None
            for act in day_activities:
                actual_sport = _normalize_sport(act.sport or "")
                if actual_sport == planned_sport:
                    best_match = act
                    break

            if best_match:
                # Remove from pool so it's not double-matched
                day_activities = [a for a in day_activities if a.id != best_match.id]

                act_data = {
                    "id": best_match.id,
                    "sport": best_match.sport,
                    "duration_sec": best_match.duration_sec,
                    "duration_min": round((best_match.duration_sec or 0) / 60, 1),
                    "distance_km": round((best_match.distance_m or 0) / 1000, 2),
                    "avg_hr": best_match.avg_hr,
                    "max_hr": best_match.max_hr,
                    "training_load": best_match.training_load,
                    "start_time": best_match.start_time.isoformat() if best_match.start_time else None,
                }
                matched_activities.append(act_data)

                compliance = _compute_workout_compliance(workout, {
                    "duration_sec": best_match.duration_sec,
                    "avg_hr": best_match.avg_hr,
                    "distance_m": best_match.distance_m,
                })
                workout_compliance.append({
                    "workout_title": workout.get("title", ""),
                    "planned_sport": planned_sport,
                    **compliance,
                })

                total_completed_sessions += 1
                total_actual_minutes += (best_match.duration_sec or 0) / 60
                total_actual_load += best_match.training_load or 0
            else:
                workout_compliance.append({
                    "workout_title": workout.get("title", ""),
                    "planned_sport": planned_sport,
                    "score": 0,
                    "status": "missed" if is_past else "pending",
                    "duration_pct": None,
                    "hr_on_target": None,
                    "notes": "No matching activity found." if is_past else "Upcoming workout.",
                })

        # Also account for extra (unmatched) activities
        extra_activities = []
        for act in day_activities:
            extra_activities.append({
                "id": act.id,
                "sport": act.sport,
                "duration_min": round((act.duration_sec or 0) / 60, 1),
                "distance_km": round((act.distance_m or 0) / 1000, 2),
                "avg_hr": act.avg_hr,
                "training_load": act.training_load,
                "note": "Extra activity (not in plan)",
            })
            total_actual_minutes += (act.duration_sec or 0) / 60
            total_actual_load += act.training_load or 0

        # Determine day completion status
        completed = len(matched_activities) > 0 and all(
            c["status"] in ("completed", "partial") for c in workout_compliance
            if c["status"] not in ("pending",)
        )
        skipped = is_past and not is_rest_day and len(matched_activities) == 0

        # Build day actual data
        actual = {
            "completed": completed,
            "skipped": skipped,
            "is_rest": is_rest_day,
            "is_past": is_past,
            "is_today": is_today,
            "is_future": is_future,
            "activities": matched_activities,
            "extra_activities": extra_activities,
            "compliance": workout_compliance,
        }

        enriched_day = {**day_plan, "actual": actual}
        enriched_days[day_name] = enriched_day

    # Build week progress
    week_progress = {
        "sessions_completed": total_completed_sessions,
        "sessions_planned": total_planned_sessions,
        "completion_pct": round(
            (total_completed_sessions / total_planned_sessions * 100)
            if total_planned_sessions > 0 else 0
        ),
        "hours_done": round(total_actual_minutes / 60, 1),
        "hours_planned": round(total_planned_minutes / 60, 1),
        "total_training_load": round(total_actual_load),
    }

    return {
        "week_summary": plan_json.get("week_summary"),
        "days": enriched_days,
        "week_progress": week_progress,
    }
