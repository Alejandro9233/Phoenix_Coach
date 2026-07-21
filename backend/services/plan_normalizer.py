"""
Plan Normalizer — Converts diverse LLM weekly plan outputs into a canonical JSON format
compatible with the backend compliance engine and the iOS client Swift models.
"""
import re
import json
from typing import Dict, List, Any, Optional

VALID_DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

DAY_KEYS_MAPPING = {
    "day_1": "Monday", "day1": "Monday", "mon": "Monday", "monday": "Monday",
    "day_2": "Tuesday", "day2": "Tuesday", "tue": "Tuesday", "tuesday": "Tuesday",
    "day_3": "Wednesday", "day3": "Wednesday", "wed": "Wednesday", "wednesday": "Wednesday",
    "day_4": "Thursday", "day4": "Thursday", "thu": "Thursday", "thursday": "Thursday",
    "day_5": "Friday", "day5": "Friday", "fri": "Friday", "friday": "Friday",
    "day_6": "Saturday", "day6": "Saturday", "sat": "Saturday", "saturday": "Saturday",
    "day_7": "Sunday", "day7": "Sunday", "sun": "Sunday", "sunday": "Sunday",
}

VALID_KEYS = VALID_DAYS + list(DAY_KEYS_MAPPING.keys())

def map_sport(sport_str: str) -> str:
    """Normalize a sport string to one of the canonical types: running, cycling, swimming, strength, rest."""
    if not sport_str:
        return "rest"
    s = sport_str.lower()
    if "run" in s or "jog" in s or "intervals" in s or "strides" in s or "tempo" in s or "opener" in s or "vo2max" in s or "repetitions" in s:
        return "running"
    if "bike" in s or "cycle" in s or "cycl" in s or "spin" in s or "ride" in s:
        return "cycling"
    if "swim" in s or "pool" in s:
        return "swimming"
    if "strength" in s or "lift" in s or "gym" in s or "weight" in s or "push" in s or "pull" in s or "plyo" in s or "core" in s:
        return "strength"
    if "rest" in s or "recovery" in s or "sleep" in s:
        return "rest"
    return "rest"

def parse_duration_to_string(dur: Any) -> str:
    """Ensure duration is a clean MM:SS string or similar format."""
    if dur is None:
        return "00:00"
    if isinstance(dur, (int, float)):
        # If it's a number like 45, convert it to "45:00" or "45 min" depending on magnitude
        if dur > 120:  # probably seconds
            mins = int(dur // 60)
            secs = int(dur % 60)
            return f"{mins:02d}:{secs:02d}"
        else:  # probably minutes
            return f"{int(dur)} min"
    
    dur_str = str(dur).strip()
    if not dur_str:
        return "00:00"
    
    # If it is already in MM:SS or HH:MM:SS
    if re.match(r"^\d+:\d+(:\d+)?$", dur_str):
        return dur_str
        
    return dur_str

def parse_zone_to_int(zone: Any) -> Optional[int]:
    """Extract a clean integer zone (e.g. from '1-2', 'Zone 2', or 2)."""
    if zone is None:
        return None
    if isinstance(zone, int):
        return zone
    
    zone_str = str(zone).strip()
    # Try to find the first digit
    match = re.search(r"\d+", zone_str)
    if match:
        try:
            return int(match.group(0))
        except ValueError:
            return None
    return None

def normalize_step(step: dict) -> dict:
    """Ensure a workout step has exact keys: type, duration, zone, description."""
    # Step Type: Warmup, Main, Cooldown, Recovery
    step_type = step.get("type") or step.get("step") or "main"
    step_type = str(step_type).lower()
    if "warm" in step_type:
        step_type = "warmup"
    elif "cool" in step_type:
        step_type = "cooldown"
    elif "rec" in step_type:
        step_type = "recovery"
    else:
        step_type = "main"

    duration = parse_duration_to_string(step.get("duration"))
    zone = parse_zone_to_int(step.get("zone"))
    description = step.get("description") or step.get("notes") or step.get("note") or ""

    return {
        "type": step_type,
        "duration": duration,
        "zone": zone,
        "description": str(description)
    }

def normalize_workout(workout: dict) -> dict:
    """Ensure a workout has sport, title, steps, total_time, hr_target."""
    # Sport & Title
    sport = map_sport(workout.get("sport") or workout.get("type") or workout.get("workout"))
    title = workout.get("title") or workout.get("workout") or workout.get("type") or (sport.capitalize() + " Workout")
    
    # Total Time
    total_time = workout.get("total_time") or workout.get("duration")
    if total_time is not None:
        if isinstance(total_time, (int, float)):
            total_time = f"{int(total_time)} min"
        else:
            total_time = str(total_time)
    else:
        total_time = "30 min"

    # HR Target
    hr_target = workout.get("hr_target") or workout.get("zone")
    if hr_target is not None:
        hr_target = str(hr_target)
    else:
        hr_target = "--"

    # Steps
    steps_raw = workout.get("steps") or []
    steps = []
    if isinstance(steps_raw, list):
        for s in steps_raw:
            if isinstance(s, dict):
                steps.append(normalize_step(s))
            elif isinstance(s, str):
                # Parse string step!
                step_type = "warmup" if "warm" in s.lower() else ("cooldown" if "cool" in s.lower() else "main")
                
                # Try to parse duration (e.g. "15 min" -> "15:00")
                dur_match = re.search(r"(\d+)\s*min", s, re.IGNORECASE)
                duration = f"{dur_match.group(1)}:00" if dur_match else "10:00"
                
                # Try to parse zone (e.g. "Z2" or "zone 2" -> 2)
                zone_match = re.search(r"z(\d+)", s, re.IGNORECASE)
                zone = int(zone_match.group(1)) if zone_match else (2 if step_type == "warmup" else (1 if step_type == "cooldown" else 3))
                
                steps.append({
                    "type": step_type,
                    "duration": duration,
                    "zone": zone,
                    "description": s
                })
    
    # If rest day, steps should be empty
    if sport == "rest":
        steps = []

    return {
        "sport": sport,
        "title": title,
        "steps": steps,
        "total_time": total_time,
        "hr_target": hr_target,
        "muscle_groups": workout.get("muscle_groups", [])
    }

def normalize_plan(plan_json: dict) -> dict:
    """
    Normalizes any weekly plan JSON layout into the expected canonical format:
    {
      "week_summary": {
        "focus": "...",
        "rationale": "...",
        "expected_total_hours": float,
        "expected_run_km": float
      },
      "days": {
        "Monday": {
          "summary": "...",
          "workouts": [...],
          "rationale": "...",
          "coach_note": "..."
        },
        ...
      },
      "_context": {...},       # Optional preserved metadata
      "weekly_review": {...}   # Optional preserved metadata
    }
    """
    if not plan_json or not isinstance(plan_json, dict):
        return {}

    normalized = {
        "week_summary": {
            "focus": "Aerobic Base Building",
            "rationale": "Base training phase focus.",
            "expected_total_hours": 8.0,
            "expected_run_km": 20.0
        },
        "days": {},
    }

    # Preserve metadata fields
    if "_context" in plan_json:
        normalized["_context"] = plan_json["_context"]
    if "weekly_review" in plan_json:
        review_val = plan_json["weekly_review"]
        if isinstance(review_val, dict):
            parts = []
            if "grade" in review_val and review_val["grade"]:
                parts.append(f"Grade: {review_val['grade']}")
            if "went_well" in review_val and review_val["went_well"]:
                parts.append(f"• Went Well: {review_val['went_well']}")
            if "needs_attention" in review_val and review_val["needs_attention"]:
                parts.append(f"• Needs Attention: {review_val['needs_attention']}")
            if "next_week_impact" in review_val and review_val["next_week_impact"]:
                parts.append(f"• Next Week: {review_val['next_week_impact']}")
            if "motivation" in review_val and review_val["motivation"]:
                parts.append(f"• Motivation: {review_val['motivation']}")
            normalized["weekly_review"] = "\n".join(parts)
        else:
            normalized["weekly_review"] = str(review_val)

    # 1. Normalize week summary first
    ws = plan_json.get("week_summary")
    if isinstance(ws, dict):
        normalized["week_summary"]["focus"] = ws.get("focus") or ws.get("phase") or "Aerobic Base Building"
        normalized["week_summary"]["rationale"] = ws.get("rationale") or ws.get("notes") or "Base training phase focus."
        
        hours_val = ws.get("expected_total_hours") or ws.get("total_hours")
        if hours_val:
            try:
                normalized["week_summary"]["expected_total_hours"] = float(hours_val)
            except (ValueError, TypeError):
                pass
                
        run_val = ws.get("expected_run_km") or ws.get("run_km") or ws.get("total_run_km")
        if run_val:
            try:
                normalized["week_summary"]["expected_run_km"] = float(run_val)
            except (ValueError, TypeError):
                pass
    else:
        # Fallback to top-level keys
        normalized["week_summary"]["focus"] = plan_json.get("focus") or plan_json.get("phase") or "Aerobic Base Building"
        normalized["week_summary"]["rationale"] = plan_json.get("rationale") or plan_json.get("notes") or "Base training phase focus."
        
        hours_val = plan_json.get("expected_total_hours") or plan_json.get("total_hours")
        if hours_val:
            try:
                normalized["week_summary"]["expected_total_hours"] = float(hours_val)
            except (ValueError, TypeError):
                pass
                
        run_val = plan_json.get("expected_run_km") or plan_json.get("run_km") or plan_json.get("total_run_km")
        if run_val:
            try:
                normalized["week_summary"]["expected_run_km"] = float(run_val)
            except (ValueError, TypeError):
                pass

    # Strictly ensure focus and rationale are strings
    normalized["week_summary"]["focus"] = str(normalized["week_summary"]["focus"])
    normalized["week_summary"]["rationale"] = str(normalized["week_summary"]["rationale"])

    days_source = {}
    
    # 2. Find where the day keys are located using a robust candidate search
    candidates = []
    
    # Top-level plan_json itself
    if any(day in plan_json for day in VALID_KEYS):
        candidates.append(plan_json)
        
    # Nested dictionaries (e.g. under "days", "workouts", "schedule", "plan", "training_plan")
    for key, val in plan_json.items():
        if isinstance(val, dict):
            if any(day in val for day in VALID_KEYS):
                candidates.append(val)
            # Check one level deeper
            for sub_key, sub_val in val.items():
                if isinstance(sub_val, dict):
                    if any(day in sub_val for day in VALID_KEYS):
                        candidates.append(sub_val)

    # Choose candidate with the most day keys
    best_candidate = {}
    max_days_found = 0
    for cand in candidates:
        days_found = sum(1 for day in VALID_KEYS if day in cand)
        if days_found > max_days_found:
            max_days_found = days_found
            best_candidate = cand
            
    if max_days_found > 0:
        days_source = best_candidate

    # 3. If no dictionary candidate was found, check recursively for a list of workouts/sessions
    if not days_source:
        lists_to_check = []
        
        def find_lists(obj):
            if isinstance(obj, list):
                lists_to_check.append(obj)
            elif isinstance(obj, dict):
                for v in obj.values():
                    find_lists(v)
                    
        find_lists(plan_json)
        
        for lst in lists_to_check:
            temp_days = {}
            for item in lst:
                if isinstance(item, dict) and ("day" in item or "day_name" in item or "workout" in item or "sport" in item):
                    day_val = item.get("day") or item.get("day_name")
                    if day_val:
                        day_name = str(day_val).strip().capitalize()
                        if day_name in VALID_DAYS or str(day_val).lower() in DAY_KEYS_MAPPING:
                            canonical_day = DAY_KEYS_MAPPING.get(str(day_val).lower()) or day_name
                            
                            if canonical_day not in temp_days:
                                temp_days[canonical_day] = {
                                    "summary": "Scheduled workout",
                                    "workouts": [],
                                    "rationale": item.get("rationale") or "Base training.",
                                    "coach_note": item.get("motivation") or item.get("coach_note") or ""
                                }
                            
                            temp_days[canonical_day]["workouts"].append(normalize_workout(item))
            if temp_days:
                days_source = temp_days
                break

    # ─── NORMALIZE EACH DAY ───
    for day_name in VALID_DAYS:
        # Find day data using either the day name directly or the day index mapping
        day_data = None
        for k, v in days_source.items():
            mapped_day = DAY_KEYS_MAPPING.get(k.lower())
            if mapped_day == day_name:
                day_data = v
                break
            elif k.strip().capitalize() == day_name:
                day_data = v
                break
        
        # If no plan exists for this day, create a default rest day
        if not day_data:
            normalized["days"][day_name] = {
                "summary": "Rest Day",
                "workouts": [{
                    "sport": "rest",
                    "title": "Rest & Recovery",
                    "steps": [],
                    "total_time": "0 min",
                    "hr_target": "--"
                }],
                "rationale": "Prioritize recovery and muscle adaptation.",
                "coach_note": "Rest is where the body adapts and grows stronger."
            }
            continue

        # If day_data is a dict
        if isinstance(day_data, dict):
            summary = day_data.get("summary") or day_data.get("notes") or ""
            rationale = day_data.get("rationale") or day_data.get("notes") or ""
            coach_note = day_data.get("coach_note") or day_data.get("motivation") or day_data.get("motivational_note") or day_data.get("notes") or ""

            # Check workouts
            workouts_raw = day_data.get("workouts") or day_data.get("workout")
            workouts = []
            
            if isinstance(workouts_raw, list):
                for w in workouts_raw:
                    if isinstance(w, dict):
                        workouts.append(normalize_workout(w))
            elif isinstance(workouts_raw, dict):
                # Single workout in dictionary format
                workouts.append(normalize_workout(workouts_raw))
            elif "workout" in day_data:
                # Handle legacy string format or detailed workout fields
                legacy_sport = map_sport(str(day_data["workout"]))
                
                # Check if steps are structured under "details" or directly in day_data
                steps = []
                details = day_data.get("details") or day_data
                
                # Filter details keys to check for warmup/main_set/cooldown steps
                step_keys = [k for k in details.keys() if k.lower() in ["warmup", "main_set", "cooldown", "mainset", "cool_down", "warm_up", "warm-up"]]
                
                if step_keys:
                    # Sort keys to ensure warmup is first, main_set is second, cooldown is last
                    sorted_keys = sorted(step_keys, key=lambda k: 0 if "warm" in k.lower() else (2 if "cool" in k.lower() else 1))
                    for k in sorted_keys:
                        step_desc = details[k]
                        step_type = "warmup" if "warm" in k.lower() else ("cooldown" if "cool" in k.lower() else "main")
                        
                        # Try to parse duration (e.g. "15 min" -> "15:00")
                        dur_match = re.search(r"(\d+)\s*min", str(step_desc), re.IGNORECASE)
                        duration = f"{dur_match.group(1)}:00" if dur_match else "10:00"
                        
                        # Try to parse zone (e.g. "Z2" or "zone 2" -> 2)
                        zone_match = re.search(r"z(\d+)", str(step_desc), re.IGNORECASE)
                        zone = int(zone_match.group(1)) if zone_match else (2 if step_type == "warmup" else (1 if step_type == "cooldown" else 3))
                        
                        steps.append({
                            "type": step_type,
                            "duration": duration,
                            "zone": zone,
                            "description": str(step_desc)
                        })
                elif isinstance(day_data.get("steps"), list):
                    # Steps list under top day structure
                    for s in day_data["steps"]:
                        if isinstance(s, dict):
                            steps.append(normalize_step(s))
                        elif isinstance(s, str):
                            step_type = "warmup" if "warm" in s.lower() else ("cooldown" if "cool" in s.lower() else "main")
                            dur_match = re.search(r"(\d+)\s*min", s, re.IGNORECASE)
                            duration = f"{dur_match.group(1)}:00" if dur_match else "10:00"
                            zone_match = re.search(r"z(\d+)", s, re.IGNORECASE)
                            zone = int(zone_match.group(1)) if zone_match else (2 if step_type == "warmup" else (1 if step_type == "cooldown" else 3))
                            
                            steps.append({
                                "type": step_type,
                                "duration": duration,
                                "zone": zone,
                                "description": s
                            })
                
                workouts.append({
                    "sport": legacy_sport,
                    "title": str(day_data["workout"]),
                    "steps": steps,
                    "total_time": day_data.get("total_time") or ("45 min" if legacy_sport != "rest" else "0 min"),
                    "hr_target": day_data.get("hr_target") or "--",
                    "muscle_groups": day_data.get("muscle_groups", [])
                })
                if not summary:
                    summary = str(day_data["workout"])

            # If still no workouts, make it a rest day
            if not workouts:
                workouts.append({
                    "sport": "rest",
                    "title": "Rest Day",
                    "steps": [],
                    "total_time": "0 min",
                    "hr_target": "--"
                })
                if not summary:
                    summary = "Rest and active recovery."
                if not rationale:
                    rationale = "Let the body recover from training stress."

            normalized["days"][day_name] = {
                "summary": summary,
                "workouts": workouts,
                "rationale": rationale,
                "coach_note": coach_note
            }
        else:
            # Fallback if day_data is string or not dict
            normalized["days"][day_name] = {
                "summary": str(day_data),
                "workouts": [{
                    "sport": "rest",
                    "title": "Rest Day",
                    "steps": [],
                    "total_time": "0 min",
                    "hr_target": "--"
                }],
                "rationale": "Rest and active recovery.",
                "coach_note": "Enjoy your recovery."
            }

    return normalized
