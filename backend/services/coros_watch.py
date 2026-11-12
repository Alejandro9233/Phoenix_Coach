"""
Phoenix workouts → COROS courses on the watch.

Write-path council, 2026-10-07 (docs/COROS_MCP.md, "Watch push"): push the
week once when the plan is persisted, edit the day when an adaptation
rewrites it, never duplicate, never prescribe on a day the enforcer stripped.

This module is the pure half: a plan workout becomes a COROS course dict
(`workout_to_course`), a stripped day becomes a non-prescriptive placeholder
(`cancelled_course`), a week of plan_json becomes dated courses
(`plan_day_courses`), and the watch calendar's prose becomes items
(`parse_training_schedule`). Nothing here talks to the network or the DB;
`backend/services/coros_watch_sync.py` does.

Encoding decisions (council answers to the athlete's open questions, revised
with Alex 2026-11-12):
- Quality runs (marathon pace, tempo, intervals, trial) are run outdoors at a
  specific pace, so their MAIN sections target `pace_target` as an absolute
  s/km range; the HR range becomes a ceiling in the description. HR lags 2-3
  min into an 8-min rep, and an HR target there pushed him faster than MP.
- Every other MAIN section carries the workout's `hr_target` as an absolute
  bpm range when it has one ("142-159 bpm"); otherwise the step's `zone` n →
  `sectionIntensity` n. Live plans showed every step zone as 1 (the LLM
  copies the prompt's example) while `hr_target` was right.
- Warmup/recovery/cooldown target at least Z2 on the watch: 2 min after an
  8-min rep at 165 bpm the HR is nowhere near Z1, and a Z1 target alarmed the
  whole recovery. The plan's own step zones stay as they are.
- Repeated main+recovery pairs become one COROS interval group ("3 x").
- Stripped day = course named "CANCELLED — <enforced_reason>", one free
  section, description says don't train and delete it in the COROS app.
  COROS cannot delete or move a scheduled workout through MCP.
- Phoenix marks its courses by name, `Phoenix <slot> · <title>`, so the
  watch calendar can be read back and matched without trusting stored ids.
- The mapper REFUSES rather than guesses: a step without a zone, a
  duration that isn't M:SS / H:MM:SS, a sport COROS can't create.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import date, timedelta

SPORT_TO_COURSE = {"running": 1, "cycling": 2}
STEP_TYPE_TO_SECTION = {"warmup": 1, "main": 2, "recovery": 3, "cooldown": 4}
WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
NAME_PREFIX = "Phoenix"
NAME_RE = re.compile(r"^Phoenix (\d+) · (.*)$")
CANCELLED_MARK = "CANCELLED"
MAX_NAME = 100
PHOENIX_FOOTER = "Plan de Phoenix Coach. Si lo editas aquí, se reescribe cuando cambie el plan."
# Run bands (pace_enforcer.classify_run_workout) whose main sections target pace.
PACE_BANDS = {"marathon", "tempo", "interval", "trial"}
EASY_SECTION_MIN_ZONE = 2
SECTION_LABEL = {1: "Calentamiento", 2: "Bloque", 3: "Suave", 4: "Enfriamiento"}


class CourseMappingError(ValueError):
    """The workout can't be expressed as a COROS course. Never guessed around."""


# --------------------------------------------------------------------------
# workout → course
# --------------------------------------------------------------------------

def _duration_seconds(value) -> int:
    """'MM:SS' or 'H:MM:SS' → seconds. Anything else is refused: the
    normalizer emits MM:SS, and '45 min' summed as 0 would be a silent hole."""
    if isinstance(value, bool) or not isinstance(value, str):
        raise CourseMappingError(f"step duration must be M:SS text, got {value!r}")
    parts = value.strip().split(":")
    if len(parts) not in (2, 3) or not all(p.isdigit() for p in parts):
        raise CourseMappingError(f"step duration must be M:SS or H:MM:SS, got {value!r}")
    nums = [int(p) for p in parts]
    seconds = nums[0] * 60 + nums[1] if len(nums) == 2 else nums[0] * 3600 + nums[1] * 60 + nums[2]
    if seconds <= 0:
        raise CourseMappingError(f"step duration must be positive, got {value!r}")
    return seconds


def _zone(step: dict) -> int:
    zone = step.get("zone")
    if isinstance(zone, bool) or not isinstance(zone, int):
        raise CourseMappingError(f"step {step.get('type')!r} has no integer zone ({zone!r})")
    if not 1 <= zone <= 6:
        raise CourseMappingError(f"step zone {zone} outside COROS zones 1-6")
    return zone


_HR_RANGE_RE = re.compile(r"^\s*(\d{2,3})\s*[-–]\s*(\d{2,3})\s*(?:bpm)?\s*$", re.I)


def hr_target_range(workout: dict) -> tuple[int, int] | None:
    """'142-159 bpm' → (142, 159) within COROS's 30-240 band, else None.
    A bare zone number ('2') or '--' is not a range."""
    m = _HR_RANGE_RE.match(str(workout.get("hr_target") or ""))
    if not m:
        return None
    lo, hi = int(m.group(1)), int(m.group(2))
    if not (30 <= lo < hi <= 240):
        return None
    return lo, hi


_PACE_RANGE_RE = re.compile(r"^\s*(\d{1,2}):(\d{2})\s*[-–]\s*(\d{1,2}):(\d{2})\s*(?:/\s*km)?\s*$", re.I)


def pace_target_range(workout: dict) -> tuple[int, int] | None:
    """'4:39-4:49/km' → (279, 289) s/km within COROS's 120-1499 band, else None."""
    m = _PACE_RANGE_RE.match(str(workout.get("pace_target") or ""))
    if not m:
        return None
    lo, hi = int(m.group(1)) * 60 + int(m.group(2)), int(m.group(3)) * 60 + int(m.group(4))
    if not (120 <= lo < hi <= 1499):
        return None
    return lo, hi


def targets_pace(workout: dict) -> bool:
    """A quality run with a usable pace range: its blocks target pace, not HR."""
    from backend.services.pace_enforcer import classify_run_workout
    return (workout.get("sport") == "running"
            and classify_run_workout(workout.get("title") or "") in PACE_BANDS
            and pace_target_range(workout) is not None)


def course_name(slot: int, title: str) -> str:
    return f"{NAME_PREFIX} {slot} · {title.strip() or 'Workout'}"[:MAX_NAME]


def _mmss(seconds: int) -> str:
    return f"{seconds // 60}:{seconds % 60:02d}"


def _section_text(s: dict) -> str:
    label = SECTION_LABEL[s["sectionType"]]
    if s["sectionType"] == 2 and s["intensityType"] != 2:
        label = "Rodaje"
    return f"{label} {_mmss(s['targetValue'])}"


def course_description(workout: dict, sections: list[dict]) -> str:
    """Short, Spanish, and matching what the watch targets."""
    parts = []
    hr = hr_target_range(workout)
    if targets_pace(workout):
        parts.append(f"Bloques a {workout['pace_target']}.")
        if hr:
            parts.append(f"FC techo {hr[1]}.")
    elif hr:
        parts.append(f"FC {hr[0]}-{hr[1]}.")
    dist = workout.get("distance_km")
    if isinstance(dist, (int, float)) and not isinstance(dist, bool) and dist > 0:
        parts.append(f"Unos {dist:g} km.")
    steps = []
    for s in sections:
        if s.get("intervalGroup"):
            steps.append(f"{s['repeats']} × (" + " + ".join(_section_text(x) for x in s["sets"]) + ")")
        else:
            steps.append(_section_text(s))
    if steps:
        parts.append(" · ".join(steps) + ".")
    parts.append(PHOENIX_FOOTER)
    return " ".join(parts)


def group_intervals(sections: list[dict]) -> list[dict]:
    """Repeated identical main+recovery pairs → one interval group. A final
    main with no recovery after it (main, rec, main, rec, main) joins the
    group, and the recovery it gains comes off the cooldown so the session
    keeps its length."""
    out, i = [], 0
    while i < len(sections):
        work = sections[i]
        if work["sectionType"] == 2 and i + 1 < len(sections) and sections[i + 1]["sectionType"] == 3:
            rest = sections[i + 1]
            j, reps = i, 0
            while j + 1 < len(sections) and sections[j] == work and sections[j + 1] == rest:
                reps, j = reps + 1, j + 2
            tail = j < len(sections) and sections[j] == work
            if tail:
                reps, j = reps + 1, j + 1
            if reps >= 2:
                out.append({"intervalGroup": True, "repeats": reps, "sets": [dict(work), dict(rest)]})
                nxt = sections[j] if j < len(sections) else None
                if tail and nxt and nxt["sectionType"] == 4 and nxt["targetType"] == 2 \
                        and nxt["targetValue"] - rest.get("targetValue", 0) >= 300:
                    out.append({**nxt, "targetValue": nxt["targetValue"] - rest["targetValue"]})
                    j += 1
                i = j
                continue
        out.append(work)
        i += 1
    return out


def workout_to_course(workout: dict, slot: int = 1) -> dict:
    """A normalized plan workout → a COROS course (createScheduledWorkout's
    `course`). Steps map to time-target sections (pace on a quality run's
    blocks, HR elsewhere); repeated block+recovery pairs become a group."""
    sport = workout.get("sport")
    if sport not in SPORT_TO_COURSE:
        raise CourseMappingError(f"{sport!r} can't be created on the watch (running/cycling only)")
    steps = workout.get("steps") or []
    if not steps:
        raise CourseMappingError("workout has no steps")
    hr_range = hr_target_range(workout)
    pace_range = pace_target_range(workout) if targets_pace(workout) else None
    sections = []
    for step in steps:
        section_type = STEP_TYPE_TO_SECTION.get(step.get("type"))
        if section_type is None:
            raise CourseMappingError(f"unknown step type {step.get('type')!r}")
        zone = _zone(step)
        section = {
            "sectionType": section_type,
            "targetType": 2,
            "targetValue": _duration_seconds(step.get("duration")),
            "intensityType": 1,
        }
        if section_type == 2 and pace_range:
            section["intensityType"] = 2
            section["intensityValueStart"], section["intensityValueEnd"] = pace_range
        elif section_type == 2 and hr_range:
            section["intensityValueStart"], section["intensityValueEnd"] = hr_range
        elif section_type == 2:
            section["sectionIntensity"] = zone
        else:
            section["sectionIntensity"] = max(zone, EASY_SECTION_MIN_ZONE)
        sections.append(section)
    sections = group_intervals(sections)
    return {
        "courseName": course_name(slot, workout.get("title") or sport.capitalize()),
        "courseDescription": course_description(workout, sections),
        "sportType": SPORT_TO_COURSE[sport],
        "sections": sections,
    }


def cancelled_course(slot: int, reason: str) -> dict:
    """The placeholder for a day Phoenix removed after pushing it. Not a
    prescription: one free section at Z1 that the athlete ends by hand."""
    reason = (reason or "session removed by the plan").strip()
    return {
        "courseName": course_name(slot, f"{CANCELLED_MARK} — {reason}"),
        "courseDescription": f"Don't train this. Delete it in the COROS app. Reason: {reason}",
        "sportType": 1,
        "sections": [{"sectionType": 2, "targetType": 4, "intensityType": 1, "sectionIntensity": 1}],
    }


def is_cancelled(course: dict) -> bool:
    m = NAME_RE.match(course.get("courseName", ""))
    return bool(m and m.group(2).startswith(CANCELLED_MARK))


def course_hash(course: dict) -> str:
    return hashlib.sha256(json.dumps(course, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16]


# --------------------------------------------------------------------------
# plan_json → dated courses
# --------------------------------------------------------------------------

@dataclass
class DayCourses:
    date: date
    courses: list[tuple[int, dict]] = field(default_factory=list)   # (slot, course)
    errors: list[str] = field(default_factory=list)                 # unmappable workouts
    error_slots: set[int] = field(default_factory=set)              # slots that failed to map
    enforced_reason: str | None = None                              # the day was stripped


def plan_day_courses(plan_json: dict, week_start: date) -> dict[date, DayCourses]:
    """Every day of the week → its pushable courses. Slots count pushable
    workouts only (1-based), so a strength session between two runs doesn't
    shift the run's slot. Unmappable workouts land in `errors`, never as a
    guessed course."""
    days = (plan_json or {}).get("days") or {}
    out = {}
    for i, name in enumerate(WEEKDAYS):
        d = week_start + timedelta(days=i)
        dc = DayCourses(date=d)
        workouts = (days.get(name) or {}).get("workouts") or []
        reasons = [w.get("enforced_reason") for w in workouts if w.get("enforced_reason")]
        if reasons:
            dc.enforced_reason = "; ".join(reasons)
        slot = 0
        for w in workouts:
            if w.get("sport") not in SPORT_TO_COURSE:
                continue
            slot += 1
            try:
                dc.courses.append((slot, workout_to_course(w, slot)))
            except CourseMappingError as e:
                dc.errors.append(f"{name} slot {slot} ({w.get('title')}): {e}")
                dc.error_slots.add(slot)
        out[d] = dc
    return out


# --------------------------------------------------------------------------
# watch calendar (queryTrainingSchedule prose) → items
# --------------------------------------------------------------------------

_SKIP_PREFIXES = ("Estimated Time", "Distance", "Load", "Status", "idInPlan", "To view or modify",
                  "To reschedule", "Training Schedule", "===")


def parse_training_schedule(text: str) -> dict[date, list[dict]]:
    """{date: [{name, idInPlan, source, completed, phoenix_slot}]}. Unparsable
    text yields {} — callers must treat an empty read as 'unknown', not as
    'nothing scheduled', before creating anything."""
    from backend.services.coros_mcp import _day_blocks

    out: dict[date, list[dict]] = {}
    for d, lines in _day_blocks(text).items():
        items, cur = [], {}

        def flush():
            if cur.get("name") or cur.get("idInPlan"):
                m = NAME_RE.match(cur.get("name") or "")
                cur["phoenix_slot"] = int(m.group(1)) if m else None
                cur.setdefault("idInPlan", None)
                cur.setdefault("source", None)
                cur.setdefault("completed", False)
                items.append(dict(cur))
            cur.clear()

        for line in lines:
            if line.startswith("idInPlan:"):
                cur["idInPlan"] = line.split(":", 1)[1].strip()
            elif line.startswith("Status:"):
                cur["completed"] = "completed" in line.lower()
            elif line.startswith("Standalone workout") or line.startswith("Plan workout") or "training plan" in line.lower():
                cur["source"] = "standalone" if line.startswith("Standalone") else "plan"
            elif line.startswith("To view or modify") or line.startswith("To reschedule"):
                flush()
            elif line.startswith(_SKIP_PREFIXES):
                continue
            elif "name" not in cur:
                cur["name"] = line
            else:
                # a second name before the previous item flushed → new item
                flush()
                cur["name"] = line
        flush()
        if items:
            out[d] = items
    return out


def phoenix_items(items: list[dict]) -> dict[int, dict]:
    """Slot → item for the Phoenix-named workouts on one date."""
    return {it["phoenix_slot"]: it for it in items if it.get("phoenix_slot")}
