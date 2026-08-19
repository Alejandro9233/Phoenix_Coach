"""
Issue Triage — turns "my right calf is shot, can't run" into a plan change.

THE PROBLEM THIS SOLVES: mid-week reality (soreness, a tweak, a closed gym) had
no low-friction path into the plan. Logging an injury meant opening a Profile
form you'd never open at 9pm, and even then `data_agent` merely mentioned it to
the LLM as "ACTIVE INJURIES (CRITICAL)" — a hint, not a constraint.

THE SHAPE: chat is the front door, but nothing is written until the athlete
confirms. The flow is deliberately three steps with a human gate in the middle:

    1. detect   cheap Python keyword filter, then one LLM extraction call
    2. propose  Python computes affected days + options; NOTHING is written
    3. apply    athlete confirms per day -> InjuryLog written, plan regenerated

WHY THE HUMAN GATE: an LLM misreading "my legs are dead after that tempo" as a
3-day running ban would silently gut a training week. The proposal is a
suggestion; only `apply_issue` mutates state.

DIVISION OF LABOUR (project rule: Python = GPS, LLM = Coach):
  - LLM: reads free text, names the body part, judges severity and which sports
    are affected. Language, not arithmetic.
  - Python: which planned days actually collide, what the substitute options
    are, and the final enforcement pass. Everything that must be exact.
"""
import json
import re
from datetime import date, timedelta
from typing import Optional

from backend.services.constraint_enforcer import (
    SPORT_TO_INJURY_TOKENS,
    enforce_constraints,
    get_active_injuries,
)
from backend.services.plan_normalizer import VALID_DAYS, map_sport, normalize_plan
from backend.utils.timezone import get_local_today

# Cheap pre-filter. Runs on EVERY chat message, so it must stay pure Python —
# paying for an extraction call on "how did my week go?" is waste. False
# positives are harmless (the extractor rejects them); false negatives just mean
# the athlete has to be more explicit.
_ISSUE_PATTERN = re.compile(
    r"\b("
    r"sore|soreness|sored?|pain|painful|hurts?|hurting|injur\w*|ache|aching|achy|"
    r"tight|tightness|strain\w*|sprain\w*|pull(ed)?|tweak\w*|niggle|twinge|"
    r"stiff|swollen|swelling|inflam\w*|tendon\w*|shin splints?|blister\w*|"
    r"can'?t run|cant run|can'?t walk|cannot run|shouldn'?t run|"
    # Spanish — the athlete trains in Hermosillo and code-switches.
    r"dolor|duele|lesi[oó]n|molestia|adolorid\w*|lastim\w*"
    r")\b",
    re.IGNORECASE,
)

_EXTRACTION_SYSTEM = """You are a triage assistant for a triathlon coaching app.

Decide whether the athlete is reporting a PHYSICAL PROBLEM that should change their
training plan — soreness, pain, an injury, or a body part they cannot load.

Report ONLY genuine physical limitations. These are NOT issues:
  - normal post-workout fatigue with no pain ("legs felt heavy", "tired today")
  - questions about pain ("what should I do if my knee hurts?")
  - past problems described as resolved ("my calf finally feels fine")
  - schedule or equipment problems (the gym being closed is not an injury)

Respond ONLY with JSON:
{
  "is_issue": true | false,
  "body_part": "short name, e.g. Right calf",
  "severity": 1-10,
  "affected_sports": ["run"|"bike"|"swim"|"strength"],
  "duration_days": integer 1-14,
  "notes": "one sentence in the athlete's own terms",
  "coach_note": "one or two sentences to the athlete, warm and practical"
}

If is_issue is false, return {"is_issue": false} and nothing else.

Guidance on severity: 1-3 mild niggle, train around it; 4-6 real soreness that
rules out loading the area; 7-10 sharp pain, stop and consider a professional.
Only list a sport in affected_sports if that sport would actually load the area.
Prefer a SHORTER duration_days — the plan can always be extended."""


def looks_like_issue(message: str) -> bool:
    """Pure-Python gate. True means 'worth paying for an extraction call'."""
    if not message:
        return False
    return bool(_ISSUE_PATTERN.search(message))


def extract_issue(message: str) -> Optional[dict]:
    """
    Ask the LLM to turn free text into a structured issue.

    Returns None when this is not an issue, or when anything at all goes wrong.
    Chat must never break because triage failed — the athlete just gets a normal
    coaching reply instead of a card.
    """
    from backend.core.llm_client import chat_completion

    try:
        raw = chat_completion(
            messages=[
                {"role": "system", "content": _EXTRACTION_SYSTEM},
                {"role": "user", "content": message},
            ],
            json_mode=True,
        )
        data = json.loads(raw)
    except Exception as e:
        print(f"⚠️ Issue extraction failed (falling back to normal chat): {e}")
        return None

    if not isinstance(data, dict) or not data.get("is_issue"):
        return None

    sports = data.get("affected_sports") or []
    if isinstance(sports, str):
        sports = [s.strip() for s in sports.split(",") if s.strip()]
    sports = [s.strip().lower() for s in sports if isinstance(s, str) and s.strip()]
    if not sports:
        # An issue that blocks nothing changes no plan. Don't show an empty card.
        return None

    try:
        severity = max(1, min(10, int(data.get("severity") or 5)))
    except (TypeError, ValueError):
        severity = 5
    try:
        duration_days = max(1, min(14, int(data.get("duration_days") or 3)))
    except (TypeError, ValueError):
        duration_days = 3

    return {
        "body_part": (data.get("body_part") or "Unspecified").strip(),
        "severity": severity,
        "affected_sports": sports,
        "duration_days": duration_days,
        "notes": (data.get("notes") or message)[:500],
        "coach_note": (data.get("coach_note") or "").strip(),
    }


def _canonical_blocked_sports(affected_sports) -> set:
    """Map loose tokens ("run", "bike") to canonical sports ("running", "cycling")."""
    tokens = {s.strip().lower() for s in affected_sports if s and s.strip()}
    return {sport for sport, aliases in SPORT_TO_INJURY_TOKENS.items() if tokens & aliases}


# Substitute preference per blocked sport, most aerobically similar first.
# Swimming leads for a lower-limb running injury because it unloads the legs.
_SWAP_PREFERENCE = {
    "running": ["cycling", "swimming", "strength"],
    "cycling": ["swimming", "running", "strength"],
    "swimming": ["cycling", "running", "strength"],
    "strength": ["swimming", "cycling", "running"],
}

_SWAP_LABEL = {
    "cycling": "bike",
    "swimming": "swim",
    "running": "run",
    "strength": "strength",
}


def _pick_substitute(blocked_sport: str, blocked_sports: set, availability: dict, day_name: str) -> Optional[str]:
    """
    Choose a safe substitute sport for a given day, or None if nothing fits.

    Must clear both gates the plan itself has to clear: not blocked by the
    injury, and allowed by the athlete's weekday availability. Offering a
    Sunday swim the athlete can't do is worse than offering rest.
    """
    from backend.services.constraint_enforcer import (
        DAY_ABBR,
        SPORT_TO_AVAILABILITY_KEY,
        parse_day_list,
    )

    for candidate in _SWAP_PREFERENCE.get(blocked_sport, []):
        if candidate in blocked_sports:
            continue
        allowed = parse_day_list(availability.get(SPORT_TO_AVAILABILITY_KEY.get(candidate)))
        if allowed is not None and DAY_ABBR[day_name] not in allowed:
            continue
        return candidate
    return None


def build_proposal(db, issue: dict) -> Optional[dict]:
    """
    Work out what this issue would actually change, without changing anything.

    Returns None when the issue collides with nothing in the plan — there is no
    point showing a confirmation card that proposes no edits.
    """
    from backend.models.database import Athlete, WeeklyPlan

    today = get_local_today()
    start_of_week = today - timedelta(days=today.weekday())

    plan_record = db.query(WeeklyPlan).filter(
        WeeklyPlan.week_start == start_of_week
    ).order_by(WeeklyPlan.id.desc()).first()
    if not plan_record:
        return None

    plan_json = normalize_plan(plan_record.plan_json)
    days_dict = plan_json.get("days") or {}

    athlete = db.query(Athlete).first()
    availability = {
        "swim_days": athlete.swim_days if athlete and athlete.swim_days is not None else "wed,sat,sun",
        "bike_days": athlete.bike_days if athlete and athlete.bike_days is not None else "mon,tue,wed,thu,fri,sat,sun",
        "run_days": athlete.run_days if athlete and athlete.run_days is not None else "mon,tue,wed,thu,fri,sat,sun",
        "strength_days": athlete.strength_days if athlete and athlete.strength_days is not None else "mon,wed,fri",
    }

    blocked_sports = _canonical_blocked_sports(issue["affected_sports"])
    if not blocked_sports:
        return None

    # The injury window, clipped to this week's plan. Days already trained are
    # untouchable — the athlete cannot un-run yesterday's run.
    window_end = today + timedelta(days=issue["duration_days"] - 1)
    today_idx = today.weekday()

    affected_days = []
    for offset in range(today_idx, 7):
        day_date = start_of_week + timedelta(days=offset)
        if day_date > window_end:
            break

        day_name = VALID_DAYS[offset]
        day = days_dict.get(day_name)
        if not isinstance(day, dict):
            continue

        blocked_workouts = [
            w for w in (day.get("workouts") or [])
            if map_sport(w.get("sport", "")) in blocked_sports
        ]
        if not blocked_workouts:
            continue

        primary = map_sport(blocked_workouts[0].get("sport", ""))
        substitute = _pick_substitute(primary, blocked_sports, availability, day_name)

        options = []
        if substitute:
            options.append({
                "id": "swap",
                "sport": substitute,
                "label": f"Swap to {_SWAP_LABEL[substitute]}",
                "detail": f"Keeps the day's training load with a {_SWAP_LABEL[substitute]} session instead.",
            })
        options.append({
            "id": "rest",
            "sport": "rest",
            "label": "Rest",
            "detail": "Drops the session. Lower weekly load, more recovery.",
        })

        affected_days.append({
            "day": day_name,
            "date": str(day_date),
            "is_today": day_date == today,
            "blocked_workouts": [
                {
                    "sport": map_sport(w.get("sport", "")),
                    "title": w.get("title") or "Session",
                    "total_time": w.get("total_time"),
                }
                for w in blocked_workouts
            ],
            "options": options,
            "recommended_option": "swap" if substitute else "rest",
        })

    if not affected_days:
        return None

    return {
        "type": "issue_proposal",
        "issue": {
            "body_part": issue["body_part"],
            "severity": issue["severity"],
            "affected_sports": sorted(blocked_sports),
            "duration_days": issue["duration_days"],
            "notes": issue["notes"],
        },
        "coach_note": issue.get("coach_note", ""),
        "affected_days": affected_days,
        "window_end": str(window_end),
    }


def apply_issue(db, issue: dict, choices: dict) -> dict:
    """
    Commit a confirmed proposal: log the injury, then rebuild the affected days.

    Args:
        issue: the confirmed issue dict (the athlete may have edited it).
        choices: `{"Thursday": "swap", "Saturday": "rest"}`. A day missing from
            this map is left alone — the enforcer will still strip anything the
            injury forbids, so an omission fails safe rather than open.

    Returns a summary of what changed.
    """
    from sqlalchemy.orm.attributes import flag_modified

    from backend.agents.data_agent import DataAgent
    from backend.agents.response_agent import ResponseAgent
    from backend.models.database import Athlete, InjuryLog, WeeklyPlan
    from backend.services.periodization_engine import PeriodizationEngine

    today = get_local_today()
    start_of_week = today - timedelta(days=today.weekday())

    athlete = db.query(Athlete).first()
    if not athlete:
        raise ValueError("No athlete on record")

    plan_record = db.query(WeeklyPlan).filter(
        WeeklyPlan.week_start == start_of_week
    ).order_by(WeeklyPlan.id.desc()).first()
    if not plan_record:
        raise ValueError("No weekly plan for this week")

    blocked_sports = _canonical_blocked_sports(issue.get("affected_sports") or [])

    duration_days = issue.get("duration_days") or 3
    try:
        duration_days = max(1, min(14, int(duration_days)))
    except (TypeError, ValueError):
        duration_days = 3
    window_end = today + timedelta(days=duration_days - 1)

    # 1. Log it. Written BEFORE regeneration so the LLM's athlete summary and the
    #    enforcer both see the injury on this same request.
    injury = InjuryLog(
        athlete_id=athlete.id,
        date_reported=today,
        body_part=issue.get("body_part") or "Unspecified",
        status="Active",
        severity=issue.get("severity"),
        notes=issue.get("notes"),
        affected_sports=",".join(sorted(blocked_sports)),
        # Auto-resolves in get_active_injuries once this passes, so the athlete
        # doesn't have to remember to clear it.
        expected_recovery_date=window_end,
    )
    db.add(injury)
    db.commit()
    db.refresh(injury)

    plan_json = normalize_plan(plan_record.plan_json)
    days_dict = plan_json.get("days") or {}

    rest_days = [d for d, c in choices.items() if c == "rest" and d in VALID_DAYS]
    swap_days = [d for d, c in choices.items() if c == "swap" and d in VALID_DAYS]

    # 2. Rest days are pure arithmetic — no LLM needed to delete a session.
    reason = f"{injury.body_part} (reported {today})"
    for day_name in rest_days:
        day = days_dict.get(day_name)
        if not isinstance(day, dict):
            continue
        day["workouts"] = [{
            "sport": "rest",
            "title": "Rest",
            "steps": [],
            "total_time": "00:00",
            "hr_target": None,
            "enforced_reason": reason,
        }]
        day["summary"] = f"Rest — {injury.body_part}"
        days_dict[day_name] = day

    # 3. Swap days go back to the coach. It now sees the injury in the athlete
    #    summary, and whatever it returns still has to clear the enforcer below.
    regenerated = []
    if swap_days:
        engine = PeriodizationEngine()
        training_context = engine.compute_context(db)
        profile = {
            "race_name": athlete.race_name,
            "race_distance": athlete.race_distance,
            "race_date": str(athlete.race_date) if athlete.race_date else None,
            "weekly_hours_target": athlete.weekly_hours_target or 8.0,
            "swim_days": athlete.swim_days,
            "bike_days": athlete.bike_days,
            "run_days": athlete.run_days,
            "strength_days": athlete.strength_days,
        }
        summary_lines = [
            f"The athlete reported: {injury.body_part} "
            f"(severity {injury.severity}/10). Avoid loading it.",
            f"Sports ruled out for the next {issue.get('duration_days', 3)} days: "
            f"{', '.join(sorted(blocked_sports))}.",
            "Replace the affected sessions with equivalent-load training in a SAFE sport. "
            "Keep the athlete's weekly volume as close to target as the injury allows.",
        ]

        try:
            result = ResponseAgent().generate_remaining_days(
                athlete_summary=DataAgent(db).summarize(),
                profile=profile,
                training_context=training_context,
                completed_days_summary="\n".join(summary_lines),
                days_to_plan=swap_days,
            )
            for day_name, new_day in (result.get("days") or {}).items():
                if day_name not in swap_days or not isinstance(new_day, dict):
                    continue
                new_day.pop("adaptation", None)
                new_day.pop("original_workouts", None)
                days_dict[day_name] = new_day
                regenerated.append(day_name)
        except Exception as e:
            # A failed regeneration must not leave a blocked session standing.
            # Fall back to rest; the enforcer would strip it anyway, this just
            # gives the athlete a clear reason instead of a bare gap.
            print(f"⚠️ Swap regeneration failed, resting {swap_days} instead: {e}")
            for day_name in swap_days:
                day = days_dict.get(day_name)
                if not isinstance(day, dict):
                    continue
                day["workouts"] = [{
                    "sport": "rest",
                    "title": "Rest",
                    "steps": [],
                    "total_time": "00:00",
                    "hr_target": None,
                    "enforced_reason": f"{reason} — could not build a substitute session",
                }]
                days_dict[day_name] = day

    plan_json["days"] = days_dict
    plan_json = normalize_plan(plan_json)

    # 4. Final gate, over the WHOLE injury window rather than just the days the
    #    athlete picked an option for. A day the client omitted must not keep a
    #    session the injury forbids — trusting the caller to send every day is
    #    the same mistake as trusting the prompt.
    #
    #    Clipped to today onward: enforcing earlier days would rewrite training
    #    that already happened.
    window_days = [
        VALID_DAYS[offset]
        for offset in range(today.weekday(), 7)
        if start_of_week + timedelta(days=offset) <= window_end
    ]

    enforce_days = sorted(set(window_days) | set(rest_days) | set(swap_days))
    from backend.services.constraint_enforcer import get_travel_day_names
    plan_json, violations = enforce_constraints(
        plan_json,
        availability={
            "swim_days": athlete.swim_days,
            "bike_days": athlete.bike_days,
            "run_days": athlete.run_days,
            "strength_days": athlete.strength_days,
            "travel_day_names": get_travel_day_names(db, athlete.id, start_of_week),
        },
        active_injuries=get_active_injuries(db, athlete.id),
        days=enforce_days,
    )
    if violations:
        print(f"🚧 Stripped {len(violations)} violation(s) after applying issue #{injury.id}")

    plan_record.plan_json = plan_json
    plan_record.last_adapted = None
    flag_modified(plan_record, "plan_json")
    db.commit()

    return {
        "status": "applied",
        "injury_id": injury.id,
        "body_part": injury.body_part,
        "rest_days": rest_days,
        "swapped_days": regenerated,
        "violations": violations,
        "plan": plan_json,
    }


# ─── Recovery: the same door, swinging out ────────────────────────────────────
#
# Reporting an injury had a low-friction path (above); reporting that it healed
# did not — the extraction prompt explicitly ignores "my calf finally feels
# fine", so chat was a one-way door. Worse, resolving an injury by hand never
# gave the week back: the rest days apply_issue wrote stayed rest days.
#
# Same three-step shape, mirrored, same human gate:
#   1. detect   keyword filter (+ an active injury on record), one LLM call to
#               match the message to a specific injury
#   2. propose  Python finds the remaining days that are rest *because of that
#               injury*; NOTHING is written
#   3. apply    athlete confirms -> status Resolved, those days regenerated

_RECOVERY_PATTERN = re.compile(
    r"\b("
    r"recovered|recovery|healed|back to normal|good to go|all (good|better)|"
    r"(feels?|feeling|is|are) (fine|good|great|better|normal|ok(ay)?)|"
    r"no (more )?pain|pain[ -]?free|doesn'?t hurt( anymore)?|stopped hurting|"
    # Spanish — same athlete, same code-switching as the issue filter.
    r"recuperad\w*|ya no (me )?duele|sin dolor|ya estoy bien|me siento (bien|mejor)|ya san\w*"
    r")\b",
    re.IGNORECASE,
)


def looks_like_recovery(message: str) -> bool:
    """Pure-Python gate. Callers must also check an active injury exists —
    good news about nothing on record is just conversation."""
    if not message:
        return False
    return bool(_RECOVERY_PATTERN.search(message))


def get_open_injuries(db, athlete_id: int) -> list:
    """
    Injuries the athlete can still close out: Active AND Recovering.

    Distinct from get_active_injuries on purpose — enforcement only cares about
    Active (a Recovering row no longer blocks training), but recovery triage
    must also match Recovering: that's exactly the state an auto-expired window
    parks an injury in while it waits for the athlete to say "it's fine now".
    """
    from backend.models.database import InjuryLog

    return db.query(InjuryLog).filter(
        InjuryLog.athlete_id == athlete_id,
        InjuryLog.status.in_(["Active", "Recovering"]),
    ).all()


def extract_recovery(message: str, active_injuries: list):
    """
    Ask the LLM whether the message says one of the ACTIVE injuries is healed.

    Returns the matched InjuryLog row, or None. None on any failure — chat must
    never break because recovery triage failed.
    """
    from backend.core.llm_client import chat_completion

    injuries_by_id = {inj.id: inj for inj in active_injuries}
    if not injuries_by_id:
        return None

    injury_lines = "\n".join(
        f"  id={inj.id}: {inj.body_part} (reported {inj.date_reported}, "
        f"blocks: {inj.affected_sports or 'nothing'})"
        for inj in active_injuries
    )
    system = (
        "You are a triage assistant for a triathlon coaching app.\n\n"
        "The athlete has these ACTIVE injuries on record:\n"
        f"{injury_lines}\n\n"
        "Decide whether their message says one of these problems is RESOLVED — "
        "healed, pain-free, ready to train normally again.\n\n"
        "NOT recovery: still hurts, only partially better (\"a bit better but "
        "still sore\" keeps the restriction), questions (\"when can I run "
        "again?\"), new problems, or talk unrelated to these injuries.\n\n"
        "Respond ONLY with JSON:\n"
        '{"is_recovery": true, "injury_id": <id from the list>}\n'
        'or {"is_recovery": false}'
    )

    try:
        raw = chat_completion(
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": message},
            ],
            json_mode=True,
        )
        data = json.loads(raw)
    except Exception as e:
        print(f"⚠️ Recovery extraction failed (falling back to normal chat): {e}")
        return None

    if not isinstance(data, dict) or not data.get("is_recovery"):
        return None
    try:
        return injuries_by_id.get(int(data.get("injury_id")))
    except (TypeError, ValueError):
        return None


def _injury_rest_days(days_dict: dict, injury, today) -> list:
    """
    Remaining days (today → Sunday) that are rest *because of this injury*.

    Both writers of injury rest days stamp the body part into the day:
    `apply_issue` via `enforced_reason` / the day summary, the enforcer via
    `enforced_reason` and `enforcement_notes`. Days the athlete swapped to
    another sport hold real training and are left alone.
    """
    body_part = (injury.body_part or "").lower()
    if not body_part:
        return []

    matched = []
    for offset in range(today.weekday(), 7):
        day_name = VALID_DAYS[offset]
        day = days_dict.get(day_name)
        if not isinstance(day, dict):
            continue
        workouts = day.get("workouts") or []
        if not workouts or any(map_sport(w.get("sport", "")) != "rest" for w in workouts):
            continue
        haystack = " ".join(
            [str(w.get("enforced_reason") or "") for w in workouts]
            + [str(day.get("summary") or "")]
            + [str(n) for n in (day.get("enforcement_notes") or [])]
        ).lower()
        if body_part in haystack:
            matched.append(day_name)
    return matched


def build_recovery_proposal(db, injury) -> dict:
    """
    What resolving this injury would change. Read-only.

    Always returns a proposal — even with nothing to rebuild, resolving the
    injury is worth a card (it unblocks future planning).
    """
    from backend.models.database import WeeklyPlan

    today = get_local_today()
    start_of_week = today - timedelta(days=today.weekday())

    rebuild_days = []
    plan_record = db.query(WeeklyPlan).filter(
        WeeklyPlan.week_start == start_of_week
    ).order_by(WeeklyPlan.id.desc()).first()
    if plan_record:
        plan_json = normalize_plan(plan_record.plan_json)
        rebuild_days = _injury_rest_days(plan_json.get("days") or {}, injury, today)

    return {
        "type": "recovery_proposal",
        "injury": {
            "id": injury.id,
            "body_part": injury.body_part,
            "date_reported": str(injury.date_reported) if injury.date_reported else None,
            "affected_sports": [
                s.strip() for s in (injury.affected_sports or "").split(",") if s.strip()
            ],
            "severity": injury.severity,
        },
        "rebuild_days": rebuild_days,
    }


def apply_recovery(db, injury_id: int, rebuild: bool = True) -> dict:
    """
    Commit a confirmed recovery: resolve the injury, rebuild its rest days.

    The resolve and the rebuild are deliberately not atomic: the athlete IS
    recovered whether or not the LLM can build new sessions right now. A failed
    rebuild leaves the plan untouched and reports `rebuild_error` — the athlete
    can replan later; their injury is not resurrected.
    """
    from sqlalchemy.orm.attributes import flag_modified

    from backend.agents.data_agent import DataAgent
    from backend.agents.response_agent import ResponseAgent
    from backend.models.database import Athlete, InjuryLog, WeeklyPlan
    from backend.services.periodization_engine import PeriodizationEngine

    injury = db.query(InjuryLog).filter(InjuryLog.id == injury_id).first()
    if not injury:
        raise ValueError(f"No injury with id {injury_id}")
    if injury.status not in ("Active", "Recovering"):
        # Double-tap or already resolved by hand — nothing to do, not an error.
        return {"status": "already_resolved", "injury_id": injury.id,
                "body_part": injury.body_part, "rebuilt_days": [], "violations": []}

    today = get_local_today()
    start_of_week = today - timedelta(days=today.weekday())

    # 1. Resolve first, and commit: the athlete's word is the fact here. The
    #    rebuild below reads get_active_injuries and must not see this row.
    injury.status = "Resolved"
    injury.expected_recovery_date = today  # record when it actually cleared
    db.commit()

    result = {
        "status": "resolved",
        "injury_id": injury.id,
        "body_part": injury.body_part,
        "rebuilt_days": [],
        "violations": [],
    }

    if not rebuild:
        return result

    plan_record = db.query(WeeklyPlan).filter(
        WeeklyPlan.week_start == start_of_week
    ).order_by(WeeklyPlan.id.desc()).first()
    if not plan_record:
        return result

    plan_json = normalize_plan(plan_record.plan_json)
    days_dict = plan_json.get("days") or {}
    rebuild_days = _injury_rest_days(days_dict, injury, today)
    if not rebuild_days:
        return result

    athlete = db.query(Athlete).first()

    # 2. Rebuild through the normal replan path — the coach sees the injury is
    #    gone (resolved above) and whatever it returns clears the enforcer.
    try:
        engine = PeriodizationEngine()
        training_context = engine.compute_context(db)
        profile = {
            "race_name": athlete.race_name,
            "race_distance": athlete.race_distance,
            "race_date": str(athlete.race_date) if athlete.race_date else None,
            "weekly_hours_target": athlete.weekly_hours_target or 8.0,
            "swim_days": athlete.swim_days,
            "bike_days": athlete.bike_days,
            "run_days": athlete.run_days,
            "strength_days": athlete.strength_days,
        }
        summary_lines = [
            f"The athlete has RECOVERED from: {injury.body_part} "
            f"(reported {injury.date_reported}, now resolved).",
            f"These days are currently rest only because of that injury: "
            f"{', '.join(rebuild_days)}.",
            "Rebuild them as normal training days for this phase. "
            "Ease the first session back — nothing maximal on day one after an injury.",
        ]
        new_days = ResponseAgent().generate_remaining_days(
            athlete_summary=DataAgent(db).summarize(),
            profile=profile,
            training_context=training_context,
            completed_days_summary="\n".join(summary_lines),
            days_to_plan=rebuild_days,
        ).get("days") or {}
    except Exception as e:
        print(f"⚠️ Recovery rebuild failed, plan left as-is: {e}")
        result["rebuild_error"] = str(e)
        result["rebuild_days_pending"] = rebuild_days
        return result

    regenerated = []
    for day_name, new_day in new_days.items():
        if day_name not in rebuild_days or not isinstance(new_day, dict):
            continue
        new_day.pop("adaptation", None)
        new_day.pop("original_workouts", None)
        days_dict[day_name] = new_day
        regenerated.append(day_name)

    plan_json["days"] = days_dict
    plan_json = normalize_plan(plan_json)
    from backend.services.constraint_enforcer import get_travel_day_names
    plan_json, violations = enforce_constraints(
        plan_json,
        availability={
            "swim_days": athlete.swim_days,
            "bike_days": athlete.bike_days,
            "run_days": athlete.run_days,
            "strength_days": athlete.strength_days,
            "travel_day_names": get_travel_day_names(db, athlete.id, start_of_week),
        },
        active_injuries=get_active_injuries(db, athlete.id),
        days=regenerated,
    )
    if violations:
        print(f"🚧 Stripped {len(violations)} violation(s) after recovery rebuild")

    plan_record.plan_json = plan_json
    plan_record.last_adapted = None
    flag_modified(plan_record, "plan_json")
    db.commit()

    result["rebuilt_days"] = sorted(regenerated)
    result["violations"] = violations
    return result


# ─── Travel triage ───────────────────────────────────────────────────────────
#
# "I'm traveling Friday and Saturday" -> those days become rest, the rest of
# the week is rebuilt around them. Same three-step shape as injuries:
#
#   1. detect   cheap Python keyword filter, then one LLM extraction call
#   2. propose  Python reads what those days hold; NOTHING is written
#   3. apply    athlete confirms -> TravelDay rows written, week rebuilt
#
# THE PRIORITY RULE (Alex, 2026-08-19): weekly RUN kilometers are the protected
# quantity. When days disappear, runs — the long run above all — move to open
# days; strength and cycling are the sessions that get dropped. The rule lives
# in three places with three strengths: the rebuild prompt (suggestion), the
# run-km comparison in the apply result (report), and the enforcer stripping
# travel days (guarantee). Only the last one is load-bearing for safety; the
# first is what actually shapes the week.
#
# V1 SCOPE: current week only (today -> Sunday). "Traveling in October" is
# conversation, not a proposal — the extractor rejects days it can't map to a
# date in the remaining week.

_TRAVEL_PATTERN = re.compile(
    r"\b("
    r"travel(l?ing|s)?|trip|flight|flying|fly(ing)? (out|home|back)|"
    r"out of town|on the road|away (for|this|next|on|until)|"
    r"won'?t be (here|home|around|able to train)|"
    # Spanish — same athlete, same code-switching as the other filters.
    r"viaj\w*|vuelo|fuera de (la )?ciudad|no (voy a )?estar (aqu[ií]|en casa)"
    r")\b",
    re.IGNORECASE,
)


def looks_like_travel(message: str) -> bool:
    """Pure-Python gate — travel talk is rare enough that one extraction call
    on a likely hit is fine, and false positives die in the extractor."""
    if not message:
        return False
    return bool(_TRAVEL_PATTERN.search(message))


def extract_travel(message: str) -> Optional[list]:
    """
    Ask the LLM which remaining days of THIS week the athlete will be away.

    Returns a sorted list of full day names ("Friday"), or None. None on any
    failure — chat must never break because travel triage failed.
    """
    from backend.core.llm_client import chat_completion

    today = get_local_today()
    remaining = []
    for offset in range(today.weekday(), 7):
        day_date = today + timedelta(days=offset - today.weekday())
        remaining.append(f"  {VALID_DAYS[offset]} = {day_date.isoformat()}")

    system = (
        "You are a triage assistant for a triathlon coaching app.\n\n"
        f"Today is {VALID_DAYS[today.weekday()]}, {today.isoformat()}. "
        "The remaining days of the athlete's current training week are:\n"
        + "\n".join(remaining) + "\n\n"
        "Decide whether the athlete is saying they will be TRAVELING or "
        "otherwise completely unavailable to train on specific days from this "
        "list.\n\n"
        "NOT travel-for-this-week: trips beyond this week (\"in October\", "
        "\"next month\"), past trips, questions or hypotheticals (\"what if I "
        "travel?\"), or being busy but still able to train. If they name days "
        "outside the list above, exclude them.\n\n"
        "Respond ONLY with JSON:\n"
        '{"is_travel": true, "days": ["Friday", "Saturday"]}\n'
        'or {"is_travel": false}'
    )

    try:
        raw = chat_completion(
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": message},
            ],
            json_mode=True,
        )
        data = json.loads(raw)
    except Exception as e:
        print(f"⚠️ Travel extraction failed (falling back to normal chat): {e}")
        return None

    if not isinstance(data, dict) or not data.get("is_travel"):
        return None
    valid = {VALID_DAYS[o] for o in range(today.weekday(), 7)}
    days = [d for d in (data.get("days") or []) if d in valid]
    return sorted(set(days), key=VALID_DAYS.index) or None


def build_travel_proposal(db, day_names: list) -> dict:
    """
    What blocking these days would change. Read-only.

    Lists what each travel day currently holds so the card can show what is
    displaced, and which open days would be rebuilt around the trip.
    """
    from backend.models.database import WeeklyPlan

    today = get_local_today()
    start_of_week = today - timedelta(days=today.weekday())

    affected = []
    plan_record = db.query(WeeklyPlan).filter(
        WeeklyPlan.week_start == start_of_week
    ).order_by(WeeklyPlan.id.desc()).first()
    days_dict = {}
    if plan_record:
        days_dict = normalize_plan(plan_record.plan_json).get("days") or {}

    displaced_runs = 0
    for day_name in day_names:
        day = days_dict.get(day_name) or {}
        titles = []
        for w in day.get("workouts") or []:
            sport = map_sport(w.get("sport", ""))
            if sport == "rest":
                continue
            titles.append(w.get("title") or sport)
            if sport == "running":
                displaced_runs += 1
        affected.append({"day": day_name, "workouts": titles})

    rebuild_days = [
        VALID_DAYS[o] for o in range(today.weekday(), 7)
        if VALID_DAYS[o] not in day_names
    ]

    return {
        "type": "travel_proposal",
        "days": day_names,
        "dates": [
            (start_of_week + timedelta(days=VALID_DAYS.index(d))).isoformat()
            for d in day_names
        ],
        "affected_days": affected,
        "displaced_runs": displaced_runs,
        "rebuild_days": rebuild_days,
        "priority_note": (
            "Run volume is protected: displaced runs move to open days; "
            "strength and cycling are dropped first."
            if displaced_runs else
            "No runs are displaced; the open days are rebalanced around the trip."
        ),
    }


def apply_travel(db, dates: list, note: str = "") -> dict:
    """
    Commit confirmed travel: write TravelDay rows, rest the blocked days,
    rebuild the remaining open days around them.

    Same non-atomicity as recovery, same reason: the athlete IS traveling
    whether or not the LLM can rebuild the week right now. A failed rebuild
    still blocks the days (rows written, blocked days rested, enforcer armed)
    and reports `rebuild_error`; only the rebalancing waits for a replan.
    """
    from sqlalchemy.orm.attributes import flag_modified

    from backend.agents.data_agent import DataAgent
    from backend.agents.response_agent import ResponseAgent
    from backend.models.database import Athlete, TravelDay, WeeklyPlan
    from backend.services.constraint_enforcer import get_travel_day_names
    from backend.services.periodization_engine import PeriodizationEngine

    today = get_local_today()
    start_of_week = today - timedelta(days=today.weekday())
    week_end = start_of_week + timedelta(days=6)

    athlete = db.query(Athlete).first()
    if not athlete:
        raise ValueError("No athlete profile found")

    parsed = []
    for raw in dates:
        d = raw if not isinstance(raw, str) else date.fromisoformat(raw)
        if today <= d <= week_end:
            parsed.append(d)
    if not parsed:
        raise ValueError("No dates in the remaining week to apply")

    # 1. Write the rows first, and commit: the trip is the fact here. The
    #    rebuild below reads them back through get_travel_day_names.
    existing = {
        row.date for row in db.query(TravelDay).filter(
            TravelDay.athlete_id == athlete.id,
            TravelDay.date.in_(parsed),
        ).all()
    }
    for d in parsed:
        if d not in existing:
            db.add(TravelDay(athlete_id=athlete.id, date=d, note=note or None))
    db.commit()

    travel_day_names = get_travel_day_names(db, athlete.id, start_of_week)
    blocked = [
        VALID_DAYS[d.weekday()] for d in sorted(set(parsed))
    ]

    result = {
        "status": "applied",
        "travel_days": blocked,
        "dates": [d.isoformat() for d in sorted(set(parsed))],
        "rebuilt_days": [],
        "violations": [],
    }

    plan_record = db.query(WeeklyPlan).filter(
        WeeklyPlan.week_start == start_of_week
    ).order_by(WeeklyPlan.id.desc()).first()
    if not plan_record:
        return result

    plan_json = normalize_plan(plan_record.plan_json)
    days_dict = plan_json.get("days") or {}

    # 2. Rest the blocked days deterministically — Python, not the LLM. What
    #    the athlete gets on a plane is not a coaching decision.
    displaced = []
    for day_name in blocked:
        day = days_dict.get(day_name) or {}
        for w in day.get("workouts") or []:
            sport = map_sport(w.get("sport", ""))
            if sport == "running":
                displaced.append(w.get("title") or "run")
        days_dict[day_name] = {
            "summary": f"Rest — traveling{f' ({note})' if note else ''}",
            "workouts": [{
                "sport": "rest",
                "title": "Rest (traveling)",
                "steps": [],
                "total_time": "0:00",
                "enforced_reason": f"Traveling on {day_name}",
            }],
            "rationale": "Athlete confirmed travel via chat triage.",
            "coach_note": "",
        }

    # 3. Rebuild the open remaining days around the trip. The context already
    #    carries travel_day_names (written above), so the prompt states the
    #    block and the run-km priority; the enforcer below guarantees it.
    rebuild_days = [
        VALID_DAYS[o] for o in range(today.weekday(), 7)
        if VALID_DAYS[o] not in blocked
    ]
    regenerated = []
    if rebuild_days:
        try:
            engine = PeriodizationEngine()
            training_context = engine.compute_context(db)
            profile = {
                "race_name": athlete.race_name,
                "race_distance": athlete.race_distance,
                "race_date": str(athlete.race_date) if athlete.race_date else None,
                "weekly_hours_target": athlete.weekly_hours_target or 8.0,
                "swim_days": athlete.swim_days,
                "bike_days": athlete.bike_days,
                "run_days": athlete.run_days,
                "strength_days": athlete.strength_days,
            }
            summary_lines = [
                f"The athlete is TRAVELING on: {', '.join(blocked)} — those days "
                "are now rest and must stay rest.",
                (
                    f"Displaced from the travel days: {', '.join(displaced)}. "
                    "Weekly run kilometers are the protected quantity — refit "
                    "the displaced running (the long run above all) onto the "
                    "open days, dropping strength or cycling to make room."
                    if displaced else
                    "No running was displaced; rebalance the open days sensibly."
                ),
                "Do not raise total weekly load — this reshuffles the week, "
                "it does not add to it.",
            ]
            new_days = ResponseAgent().generate_remaining_days(
                athlete_summary=DataAgent(db).summarize(),
                profile=profile,
                training_context=training_context,
                completed_days_summary="\n".join(summary_lines),
                days_to_plan=rebuild_days,
            ).get("days") or {}
            for day_name, new_day in new_days.items():
                if day_name not in rebuild_days or not isinstance(new_day, dict):
                    continue
                new_day.pop("adaptation", None)
                new_day.pop("original_workouts", None)
                days_dict[day_name] = new_day
                regenerated.append(day_name)
        except Exception as e:
            print(f"⚠️ Travel rebuild failed, blocked days still rested: {e}")
            result["rebuild_error"] = str(e)

    plan_json["days"] = days_dict
    plan_json = normalize_plan(plan_json)
    plan_json, violations = enforce_constraints(
        plan_json,
        availability={
            "swim_days": athlete.swim_days,
            "bike_days": athlete.bike_days,
            "run_days": athlete.run_days,
            "strength_days": athlete.strength_days,
            "travel_day_names": travel_day_names,
        },
        active_injuries=get_active_injuries(db, athlete.id),
        days=blocked + regenerated,
    )
    if violations:
        print(f"🚧 Stripped {len(violations)} violation(s) after travel rebuild")

    plan_record.plan_json = plan_json
    plan_record.last_adapted = None
    flag_modified(plan_record, "plan_json")
    db.commit()

    result["rebuilt_days"] = sorted(regenerated, key=VALID_DAYS.index)
    result["violations"] = violations
    return result
