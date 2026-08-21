"""
Periodization Engine — Computes the TrainingContext for the LLM coach.

This is the "GPS" of the coaching system. It tells the LLM:
- WHERE we are (phase, weeks to race)
- WHAT tools are available (workout menu per phase)
- WHAT the guardrails are (volume references, intensity splits)
- WHAT happened recently (last week, recovery status)

It does NOT make coaching decisions — the LLM does that.

Supports multiple race distances:
- Running: 5k, 10k, Half Marathon, Marathon
- Triathlon: Sprint, Olympic, 70.3, Ironman (stubbed)
- Unknown distances fall back to foundation-only training.

Volume ceilings by distance (the numbers the LLM is not allowed to exceed):

    5k / 10k     25-35 km/wk,  5-7 h/wk   Speed Build -> Taper + Race
                 Enables VO2max intervals, tempo, reps.
                 Forbids marathon-pace long runs and any run over 15 km.
    Marathon     40-55 km/wk, 10-11 h/wk  Foundation -> Base -> Build -> Peak -> Taper
                 Heavy on marathon-pace long runs and aerobic volume.
    Sprint/Oly   dynamic, balances swim/bike/run plus strength.
    Unknown      FOUNDATION_ONLY_PROFILE (safety fallback).

WHY the distance profiles exist: a 5k goal used to be forced into marathon volume,
which confused the coach into emitting safe-fallback rest weeks. If a generated plan
comes back with wrong mileage, the bug is HERE or in the context injection — never in
the prompt. The LLM does not choose volume.

Sport availability (swim_days, bike_days, run_days, strength_days) is READ here into
`context["availability"]` and enforced in `constraint_enforcer.py`, which strips
violating workouts after generation. The prompt also states the constraint, but that
is a hint — the enforcer is what actually holds. Do not trust the prompt alone; this
docstring used to claim enforcement lived here and it did not, which shipped strength
sessions onto non-strength days for months.
"""
import copy
import re
from datetime import date, datetime, timedelta
from typing import Optional
from sqlalchemy.orm import Session
from sqlalchemy import func
from backend.models.database import Athlete, Activity, RecoverySnapshot, WeeklyPlan
from backend.services.constraint_enforcer import (
    SPORT_TO_AVAILABILITY_KEY, get_travel_day_names, parse_day_list,
)
from backend.services.pace_model import compute_pace_model
from backend.utils.timezone import get_local_today

# Weekly run-km progression (_get_weekly_run_target). Tunable in one place.
RUN_RAMP = 1.08            # next week = 8% over the best recent week
RUN_RAMP_HARD_CAP = 1.15   # never plan >15% above demonstrated volume,
                           # even when the phase floor asks for more
LONG_RUN_STEP_MIN = 12     # long run grows this many minutes per week
RUN_NOISE_FLOOR_KM = 5.0   # weeks at or under this are noise, not capacity


# ─── Shared workout menu building blocks ──────────────────────────────────────
# These are reused across multiple distance profiles to reduce duplication.

_FOUNDATION_MENU = {
    "allowed": {
        "running": ["Easy Run", "Long Run (Z1-Z2 only)", "Strides/Openers"],
        "cycling": ["Endurance Ride (Z2)"],
        "swimming": ["Technique Session", "CSS Threshold"],
        "strength": {
            "types": ["Hypertrophy Push/Pull/Legs", "Hypertrophy Upper/Lower"],
            "available_muscle_groups": ["chest", "shoulders", "back", "legs", "arms"],
            "note": "Coach decides the split. Always specify muscle_groups in output.",
        },
    },
    "forbidden": [
        "Tempo Run", "Cruise Intervals", "VO2max Intervals", "Marathon Pace Long Run",
        "Progressive Run", "Sweet Spot Intervals", "Threshold Intervals",
        "Sprint Intervals", "Sprint Set (swim)", "Brick Session",
    ],
    "reason": "Foundation phase — build aerobic base and consistency before adding intensity. 90% of work should be Zone 1-2.",
}

_TAPER_MENU = {
    "allowed": {
        "running": ["Easy Run (short)", "Strides/Openers"],
        "cycling": ["Endurance Ride (short, Z2)"],
        "swimming": ["Technique Session (short)"],
        "strength": [],
    },
    "forbidden": [
        "ALL intense or long sessions", "Tempo Run", "Cruise Intervals",
        "Marathon Pace Long Run", "VO2max Intervals", "Sweet Spot",
        "Threshold Intervals", "Long Run (full length)",
        "Strength (stop 7-10 days before race)",
    ],
    "reason": "Taper — reduce volume progressively, maintain short intensity touches (strides). Trust the process.",
}


# ─── Distance-specific profiles ──────────────────────────────────────────────
# Each profile defines:
#   - "phases": list of phase dicts (ordered by proximity to race, taper first)
#   - "workout_menus": dict keyed by phase ID → allowed/forbidden workout lists

DISTANCE_PROFILES = {

    # ══════════════════════════════════════════════════════════════════════════
    # RUNNING — 5K
    # ══════════════════════════════════════════════════════════════════════════
    "5k": {
        "phases": [
            {
                "id": "taper",
                "name": "Phase 3: Taper + Race",
                "weeks_range": (0, 1),
                "total_weeks": 1,
                "priorities": "Reduce volume, maintain neuromuscular speed touches, rest legs for race day",
                "hours_range": "3-4",
                "intensity_split": "80/20",
                "max_quality_sessions": 1,
                "sport_sessions": {
                    "running": {"sessions": 3, "volume_note": "15-20 km/week, strides + openers only"},
                    "swimming": {"sessions": 1, "volume_note": "Easy technique 20 min for blood flow"},
                    "cycling": {"sessions": 0, "volume_note": "None — full running taper"},
                    "strength": {"sessions": 0, "volume_note": "Stop all lifting 5-7 days before race"},
                },
            },
            {
                "id": "speed_build",
                "name": "Phase 2: 5k Speed Build",
                "weeks_range": (2, 5),
                "total_weeks": 4,
                "priorities": "VO2max intervals, race-pace repeats, sharpen speed and running economy",
                "hours_range": "5-7",
                "intensity_split": "75/25",
                "max_quality_sessions": 3,
                "sport_sessions": {
                    "running": {"sessions": 4, "volume_note": "25-35 km/week, VO2max + repetitions + tempo"},
                    "swimming": {"sessions": 1, "volume_note": "1 easy cross-training session"},
                    "cycling": {"sessions": 1, "volume_note": "30-45 min Z2 recovery spin"},
                    "strength": {"sessions": 2, "volume_note": "Power + plyometrics focus, low volume"},
                },
            },
            {
                "id": "foundation",
                "name": "Phase 1: Foundation",
                "weeks_range": (6, 999),
                "total_weeks": 8,
                "priorities": "Build consistency, establish aerobic base, introduce strides for speed prep",
                "hours_range": "5-6",
                "intensity_split": "90/10",
                "max_quality_sessions": 1,
                "sport_sessions": {
                    "running": {"sessions": 3, "volume_note": "20-28 km/week, easy runs, build gradually (10% rule)"},
                    "swimming": {"sessions": 2, "volume_note": "2-3 km/session, technique drills"},
                    "cycling": {"sessions": 2, "volume_note": "60-75 min/session, Z2 endurance"},
                    "strength": {"sessions": 3, "volume_note": "Hypertrophy focus — best window for muscle building"},
                },
            },
        ],
        "workout_menus": {
            "foundation": _FOUNDATION_MENU,
            "speed_build": {
                "allowed": {
                    "running": [
                        "Easy Run", "Strides/Openers", "Tempo Run",
                        "VO2max Intervals", "Repetitions (R)", "Progressive Run",
                    ],
                    "cycling": ["Endurance Ride (Z2)"],
                    "swimming": ["Technique Session"],
                    "strength": {
                        "types": ["Power + Plyometrics", "Maintenance (2 sets, heavy compounds)"],
                        "available_muscle_groups": ["legs", "arms", "back"],
                        "note": "Focus on explosive power and running economy. Low volume.",
                    },
                },
                "forbidden": [
                    "Marathon Pace Long Run", "Long Run (>15 km)", "Cruise Intervals",
                    "Endurance Swim", "Sweet Spot Intervals", "Brick Session",
                    "Race Simulation Brick",
                ],
                "reason": "5k Speed Build — sharpen VO2max and race-pace speed. Short intervals, not marathon volume.",
            },
            "taper": _TAPER_MENU,
        },
    },

    # ══════════════════════════════════════════════════════════════════════════
    # RUNNING — 10K
    # ══════════════════════════════════════════════════════════════════════════
    "10k": {
        "phases": [
            {
                "id": "taper",
                "name": "Phase 4: Taper + Race",
                "weeks_range": (0, 1),
                "total_weeks": 1,
                "priorities": "Reduce volume, maintain intensity touches, trust the taper",
                "hours_range": "3-5",
                "intensity_split": "80/20",
                "max_quality_sessions": 1,
                "sport_sessions": {
                    "running": {"sessions": 3, "volume_note": "20-25 km/week, short easy runs + strides"},
                    "swimming": {"sessions": 1, "volume_note": "1 easy technique session"},
                    "cycling": {"sessions": 0, "volume_note": "None"},
                    "strength": {"sessions": 0, "volume_note": "Stop all lifting 5-7 days before race"},
                },
            },
            {
                "id": "speed_build",
                "name": "Phase 3: 10k Speed Build",
                "weeks_range": (2, 5),
                "total_weeks": 4,
                "priorities": "VO2max intervals, cruise intervals at threshold, race-pace sharpening",
                "hours_range": "6-8",
                "intensity_split": "80/20",
                "max_quality_sessions": 3,
                "sport_sessions": {
                    "running": {"sessions": 4, "volume_note": "30-40 km/week, VO2max + cruise intervals + tempo"},
                    "swimming": {"sessions": 1, "volume_note": "1 easy cross-training session"},
                    "cycling": {"sessions": 1, "volume_note": "45-60 min Z2 recovery spin"},
                    "strength": {"sessions": 2, "volume_note": "Maintenance + power — 2 sets, heavy compounds"},
                },
            },
            {
                "id": "base",
                "name": "Phase 2: 10k Base",
                "weeks_range": (6, 9),
                "total_weeks": 4,
                "priorities": "Build running volume safely, add long run, introduce 1 tempo/week",
                "hours_range": "6-8",
                "intensity_split": "80/20",
                "max_quality_sessions": 2,
                "sport_sessions": {
                    "running": {"sessions": 4, "volume_note": "28-35 km/week, long run 60-75 min, 1 tempo/week"},
                    "swimming": {"sessions": 2, "volume_note": "2-3 km/session, CSS intervals"},
                    "cycling": {"sessions": 1, "volume_note": "60-75 min Z2 endurance"},
                    "strength": {"sessions": 2, "volume_note": "Hypertrophy transitioning to power — reduce volume if running suffers"},
                },
            },
            {
                "id": "foundation",
                "name": "Phase 1: Foundation",
                "weeks_range": (10, 999),
                "total_weeks": 8,
                "priorities": "Build consistency, establish aerobic base, fix technique issues",
                "hours_range": "5-7",
                "intensity_split": "90/10",
                "max_quality_sessions": 1,
                "sport_sessions": {
                    "running": {"sessions": 3, "volume_note": "20-28 km/week, easy runs, build gradually (10% rule)"},
                    "swimming": {"sessions": 2, "volume_note": "2-3 km/session, technique drills, CSS test"},
                    "cycling": {"sessions": 2, "volume_note": "60-75 min/session, Z2 endurance"},
                    "strength": {"sessions": 3, "volume_note": "Hypertrophy focus — best window for muscle building"},
                },
            },
        ],
        "workout_menus": {
            "foundation": _FOUNDATION_MENU,
            "base": {
                "allowed": {
                    "running": ["Easy Run", "Long Run", "Strides/Openers", "Tempo Run", "Cruise Intervals"],
                    "cycling": ["Endurance Ride (Z2)", "Sweet Spot Intervals"],
                    "swimming": ["Technique Session", "CSS Threshold", "Endurance Swim"],
                    "strength": {
                        "types": ["Hypertrophy Push/Pull/Legs", "Hypertrophy Upper/Lower"],
                        "available_muscle_groups": ["chest", "shoulders", "back", "legs", "arms"],
                        "note": "Coach decides the split. Always specify muscle_groups in output.",
                    },
                },
                "forbidden": [
                    "VO2max Intervals", "Marathon Pace Long Run", "Race Simulation Brick",
                    "Sprint Intervals", "Threshold Intervals (bike)",
                ],
                "reason": "10k Base — building volume with 1 quality run/week. No race-specific intensity yet.",
            },
            "speed_build": {
                "allowed": {
                    "running": [
                        "Easy Run", "Strides/Openers", "Tempo Run",
                        "Cruise Intervals", "VO2max Intervals", "Progressive Run",
                        "Repetitions (R)",
                    ],
                    "cycling": ["Endurance Ride (Z2)"],
                    "swimming": ["Technique Session"],
                    "strength": {
                        "types": ["Maintenance (2 sets, heavy compounds)", "Power + Plyometrics"],
                        "available_muscle_groups": ["legs", "back", "arms"],
                        "note": "Maintenance only. Coach picks which groups.",
                    },
                },
                "forbidden": [
                    "Marathon Pace Long Run", "Long Run (>20 km)",
                    "Endurance Swim", "Sweet Spot Intervals", "Brick Session",
                ],
                "reason": "10k Speed Build — VO2max + threshold sharpening. Keep long runs moderate.",
            },
            "taper": _TAPER_MENU,
        },
    },

    # ══════════════════════════════════════════════════════════════════════════
    # RUNNING — HALF MARATHON
    # ══════════════════════════════════════════════════════════════════════════
    "Half Marathon": {
        "phases": [
            {
                "id": "taper",
                "name": "Phase 5: Taper + Race",
                "weeks_range": (0, 2),
                "total_weeks": 2,
                "priorities": "Reduce volume progressively, maintain short intensity touches, trust the taper",
                "hours_range": "4-5",
                "intensity_split": "80/20",
                "max_quality_sessions": 1,
                "sport_sessions": {
                    "running": {"sessions": 3, "volume_note": "20-25 km/week, short easy + strides, last long run 2 weeks out"},
                    "swimming": {"sessions": 1, "volume_note": "1 easy technique session for blood flow"},
                    "cycling": {"sessions": 1, "volume_note": "1 short easy spin"},
                    "strength": {"sessions": 0, "volume_note": "Stop all lifting 7-10 days before race"},
                },
            },
            {
                "id": "peak",
                "name": "Phase 4: Half Marathon Peak",
                "weeks_range": (3, 5),
                "total_weeks": 3,
                "priorities": "Hold volume, sharpen race-pace fitness, final key workouts",
                "hours_range": "8-10",
                "intensity_split": "80/20",
                "max_quality_sessions": 2,
                "sport_sessions": {
                    "running": {"sessions": 4, "volume_note": "35-45 km/week, HM-pace + cruise intervals"},
                    "swimming": {"sessions": 1, "volume_note": "3 km/session, maintain CSS work"},
                    "cycling": {"sessions": 1, "volume_note": "60 min easy Z2 — recovery only"},
                    "strength": {"sessions": 1, "volume_note": "Maintenance — 2 sets, heavy compounds, no accessories"},
                },
            },
            {
                "id": "build",
                "name": "Phase 3: Half Marathon Build",
                "weeks_range": (6, 10),
                "total_weeks": 5,
                "priorities": "HM-specific fitness, race-pace work, long run to 24-28 km",
                "hours_range": "8-10",
                "intensity_split": "80/20",
                "max_quality_sessions": 2,
                "sport_sessions": {
                    "running": {"sessions": 4, "volume_note": "35-50 km/week, long run + HM-pace + tempo"},
                    "swimming": {"sessions": 2, "volume_note": "3-4 km/session, maintain — don't increase"},
                    "cycling": {"sessions": 1, "volume_note": "60-75 min easy Z2 — recovery cross-training"},
                    "strength": {"sessions": 2, "volume_note": "Shift to maintenance — 2 sets, keep compounds, drop accessories"},
                },
            },
            {
                "id": "base",
                "name": "Phase 2: Half Marathon Base",
                "weeks_range": (11, 16),
                "total_weeks": 6,
                "priorities": "Build running volume safely, add long run, introduce 1 tempo/week",
                "hours_range": "7-9",
                "intensity_split": "80/20",
                "max_quality_sessions": 2,
                "sport_sessions": {
                    "running": {"sessions": 4, "volume_note": "28-40 km/week, add long run 60-90 min"},
                    "swimming": {"sessions": 2, "volume_note": "3-4 km/session, CSS intervals"},
                    "cycling": {"sessions": 2, "volume_note": "75-90 min/session, Z2-Z3 + 1x sweet spot"},
                    "strength": {"sessions": 3, "volume_note": "Hypertrophy but watch total fatigue — reduce sets if running suffers"},
                },
            },
            {
                "id": "foundation",
                "name": "Phase 1: Foundation",
                "weeks_range": (17, 999),
                "total_weeks": 8,
                "priorities": "Build consistency, establish aerobic base, fix technique issues",
                "hours_range": "6-7",
                "intensity_split": "90/10",
                "max_quality_sessions": 1,
                "sport_sessions": {
                    "running": {"sessions": 3, "volume_note": "20-28 km/week, easy runs, build gradually (10% rule)"},
                    "swimming": {"sessions": 2, "volume_note": "2-3 km/session, technique drills, CSS test"},
                    "cycling": {"sessions": 2, "volume_note": "60-75 min/session, Z2 endurance"},
                    "strength": {"sessions": 3, "volume_note": "Hypertrophy focus — best window for muscle building"},
                },
            },
        ],
        "workout_menus": {
            "foundation": _FOUNDATION_MENU,
            "base": {
                "allowed": {
                    "running": ["Easy Run", "Long Run", "Strides/Openers", "Tempo Run", "Cruise Intervals"],
                    "cycling": ["Endurance Ride (Z2)", "Sweet Spot Intervals"],
                    "swimming": ["Technique Session", "CSS Threshold", "Endurance Swim"],
                    "strength": {
                        "types": ["Hypertrophy Push/Pull/Legs", "Hypertrophy Upper/Lower"],
                        "available_muscle_groups": ["chest", "shoulders", "back", "legs", "arms"],
                        "note": "Coach decides the split. Always specify muscle_groups in output.",
                    },
                },
                "forbidden": [
                    "VO2max Intervals", "Marathon Pace Long Run", "Race Simulation Brick",
                    "Sprint Intervals", "Threshold Intervals (bike)",
                ],
                "reason": "HM Base — building volume with 1 quality run/week. No race-specific intensity yet.",
            },
            "build": {
                "allowed": {
                    "running": [
                        "Easy Run", "Long Run", "Strides/Openers", "Tempo Run",
                        "Cruise Intervals", "Progressive Run",
                        "HM-Pace Long Run (last 8-12 km at half-marathon goal pace)",
                    ],
                    "cycling": ["Endurance Ride (Z2)", "Sweet Spot Intervals"],
                    "swimming": ["Technique Session", "CSS Threshold", "Endurance Swim"],
                    "strength": {
                        "types": ["Maintenance (2 sets, heavy compounds)"],
                        "available_muscle_groups": ["chest", "shoulders", "back", "legs", "arms"],
                        "note": "Maintenance only. Coach picks which groups.",
                    },
                },
                "forbidden": [
                    "Marathon Pace Long Run", "VO2max Intervals (run)",
                    "Sprint Set (swim)", "Race Simulation Brick",
                ],
                "reason": "HM Build — race-specific work begins. HM-pace long runs, tempo, cruise intervals.",
            },
            "peak": {
                "allowed": {
                    "running": [
                        "Easy Run", "Long Run", "Strides/Openers", "Tempo Run",
                        "Cruise Intervals", "Progressive Run",
                        "HM-Pace Long Run (last 8-12 km at half-marathon goal pace)",
                        "VO2max Intervals (careful — only if well-recovered)",
                    ],
                    "cycling": ["Endurance Ride (Z2)"],
                    "swimming": ["Technique Session", "CSS Threshold"],
                    "strength": {
                        "types": ["Maintenance (2 sets, heavy compounds)"],
                        "available_muscle_groups": ["chest", "shoulders", "back", "legs", "arms"],
                        "note": "Maintenance only. Coach picks which groups.",
                    },
                },
                "forbidden": [
                    "Marathon Pace Long Run", "Sprint Set (swim)", "Race Simulation Brick",
                ],
                "reason": "HM Peak — sharpening fitness. VO2max intervals allowed if recovery permits.",
            },
            "taper": _TAPER_MENU,
        },
    },

    # ══════════════════════════════════════════════════════════════════════════
    # RUNNING — MARATHON
    # ══════════════════════════════════════════════════════════════════════════
    "Marathon": {
        "phases": [
            {
                "id": "taper",
                "name": "Phase 5: Taper + Race",
                "weeks_range": (0, 3),
                "total_weeks": 3,
                "priorities": "Reduce volume progressively, maintain intensity touches, trust the taper",
                "hours_range": "4-6",
                "intensity_split": "80/20",
                "max_quality_sessions": 1,
                "run_km_range": (15, 30),
                "long_run_cap_min": 90,
                "sport_sessions": {
                    "running": {"sessions": 3, "volume_note": "Short easy runs + strides, last long run 3 weeks out"},
                    "swimming": {"sessions": 1, "volume_note": "1 easy technique session for blood flow"},
                    "cycling": {"sessions": 1, "volume_note": "1 short easy spin"},
                    "strength": {"sessions": 0, "volume_note": "Stop all lifting 7-10 days before race"},
                },
            },
            {
                "id": "peak",
                "name": "Phase 4: Marathon Peak",
                "weeks_range": (4, 6),
                "total_weeks": 3,
                "priorities": "Hold volume, sharpen race-pace fitness, final key workouts",
                "hours_range": "10-11",
                "intensity_split": "80/20",
                "max_quality_sessions": 2,
                "run_km_range": (40, 55),
                "long_run_cap_min": 150,
                "sport_sessions": {
                    "running": {"sessions": 4, "volume_note": "40-55 km/week, M-pace + cruise intervals"},
                    "swimming": {"sessions": 2, "volume_note": "3-4 km/session, maintain CSS work"},
                    "cycling": {"sessions": 1, "volume_note": "60-75 min easy Z2 — recovery only"},
                    "strength": {"sessions": 2, "volume_note": "Maintenance — 2 sets, heavy compounds, no accessories"},
                },
            },
            {
                "id": "build",
                "name": "Phase 3: Marathon Build",
                "weeks_range": (7, 14),
                "total_weeks": 8,
                "priorities": "Marathon-specific fitness, race-pace work, long run to 28-32 km",
                "hours_range": "10-12",
                "intensity_split": "80/20",
                "max_quality_sessions": 2,
                "run_km_range": (40, 55),
                "long_run_cap_min": 160,
                "sport_sessions": {
                    "running": {"sessions": 4, "volume_note": "40-55 km/week, long run + M-pace + tempo"},
                    "swimming": {"sessions": 2, "volume_note": "3-4 km/session, maintain — don't increase"},
                    "cycling": {"sessions": 1, "volume_note": "60-75 min easy Z2 — recovery cross-training"},
                    "strength": {"sessions": 2, "volume_note": "Shift to maintenance — 2 sets, keep compounds, drop accessories"},
                },
            },
            {
                "id": "base",
                "name": "Phase 2: Marathon Base",
                "weeks_range": (15, 22),
                "total_weeks": 8,
                "priorities": "Build running volume safely, add long run, introduce 1 tempo/week",
                "hours_range": "9-10",
                "intensity_split": "80/20",
                "max_quality_sessions": 2,
                "run_km_range": (28, 40),
                "long_run_cap_min": 110,
                "sport_sessions": {
                    "running": {"sessions": 4, "volume_note": "28-40 km/week, add long run 60-90 min"},
                    "swimming": {"sessions": 2, "volume_note": "3-4 km/session, CSS intervals"},
                    "cycling": {"sessions": 2, "volume_note": "75-90 min/session, Z2-Z3 + 1x sweet spot"},
                    "strength": {"sessions": 3, "volume_note": "Hypertrophy but watch total fatigue — reduce sets if running suffers"},
                },
            },
            {
                "id": "foundation",
                "name": "Phase 1: Foundation",
                "weeks_range": (23, 999),
                "total_weeks": 8,
                "priorities": "Build consistency, establish the aerobic base, grow run volume gradually (10% rule)",
                "hours_range": "7-8",
                "intensity_split": "90/10",
                "max_quality_sessions": 1,
                "run_km_range": (20, 28),
                "long_run_cap_min": 90,
                "sport_sessions": {
                    "running": {"sessions": 3, "volume_note": "20-28 km/week, easy runs, build gradually (10% rule)"},
                    "swimming": {"sessions": 2, "volume_note": "2-3 km/session, technique drills, CSS test"},
                    "cycling": {"sessions": 2, "volume_note": "60-75 min/session, Z2 endurance"},
                    "strength": {"sessions": 3, "volume_note": "Hypertrophy focus — best window for muscle building"},
                },
            },
        ],
        "workout_menus": {
            "foundation": _FOUNDATION_MENU,
            "base": {
                "allowed": {
                    "running": ["Easy Run", "Long Run", "Strides/Openers", "Tempo Run", "Cruise Intervals"],
                    "cycling": ["Endurance Ride (Z2)", "Sweet Spot Intervals"],
                    "swimming": ["Technique Session", "CSS Threshold", "Endurance Swim"],
                    "strength": {
                        "types": ["Hypertrophy Push/Pull/Legs", "Hypertrophy Upper/Lower"],
                        "available_muscle_groups": ["chest", "shoulders", "back", "legs", "arms"],
                        "note": "Coach decides the split. Always specify muscle_groups in output.",
                    },
                },
                "forbidden": [
                    "VO2max Intervals", "Marathon Pace Long Run", "Race Simulation Brick",
                    "Sprint Intervals", "Threshold Intervals (bike)",
                ],
                "reason": "Marathon Base — building volume with 1 quality run/week. No race-specific intensity yet.",
            },
            "build": {
                "allowed": {
                    "running": [
                        "Easy Run", "Long Run", "Strides/Openers", "Tempo Run",
                        "Cruise Intervals", "Marathon Pace Long Run", "Progressive Run",
                    ],
                    "cycling": ["Endurance Ride (Z2)", "Sweet Spot Intervals", "Threshold Intervals"],
                    "swimming": ["Technique Session", "CSS Threshold", "Endurance Swim"],
                    "strength": {
                        "types": ["Maintenance (2 sets, heavy compounds)"],
                        "available_muscle_groups": ["chest", "shoulders", "back", "legs", "arms"],
                        "note": "Maintenance only. Coach picks which groups.",
                    },
                },
                "forbidden": [
                    "VO2max Intervals (run)", "Sprint Set (swim)", "Race Simulation Brick",
                ],
                "reason": "Marathon Build — race-specific work begins. M-pace long runs, tempo, cruise intervals.",
            },
            "peak": {
                "allowed": {
                    "running": [
                        "Easy Run", "Long Run", "Strides/Openers", "Tempo Run",
                        "Cruise Intervals", "Marathon Pace Long Run", "Progressive Run",
                        "VO2max Intervals (careful — only if well-recovered)",
                    ],
                    "cycling": ["Endurance Ride (Z2)", "Sweet Spot Intervals", "Threshold Intervals"],
                    "swimming": ["Technique Session", "CSS Threshold", "Endurance Swim"],
                    "strength": {
                        "types": ["Maintenance (2 sets, heavy compounds)"],
                        "available_muscle_groups": ["chest", "shoulders", "back", "legs", "arms"],
                        "note": "Maintenance only. Coach picks which groups.",
                    },
                },
                "forbidden": ["Race Simulation Brick", "Sprint Set (swim)"],
                "reason": "Peak phase — sharpening fitness. VO2max intervals allowed if recovery permits.",
            },
            "taper": _TAPER_MENU,
        },
    },

    # ══════════════════════════════════════════════════════════════════════════
    # TRIATHLON — SPRINT (750m swim / 20km bike / 5km run)
    # Stub — functional but may need tuning with real athlete feedback.
    # ══════════════════════════════════════════════════════════════════════════
    "Sprint": {
        "phases": [
            {
                "id": "taper",
                "name": "Phase 3: Sprint Tri Taper + Race",
                "weeks_range": (0, 1),
                "total_weeks": 1,
                "priorities": "Short taper, maintain speed touches, practice transitions mentally",
                "hours_range": "3-4",
                "intensity_split": "80/20",
                "max_quality_sessions": 1,
                "sport_sessions": {
                    "running": {"sessions": 2, "volume_note": "15-20 km/week, strides only"},
                    "swimming": {"sessions": 2, "volume_note": "1-1.5 km/session, technique + race-pace 750m"},
                    "cycling": {"sessions": 1, "volume_note": "30 min easy spin"},
                    "strength": {"sessions": 0, "volume_note": "Stop all lifting"},
                },
            },
            {
                "id": "build",
                "name": "Phase 2: Sprint Tri Build",
                "weeks_range": (2, 7),
                "total_weeks": 6,
                "priorities": "Race-specific speed across all 3 sports, introduce brick sessions, practice transitions",
                "hours_range": "6-8",
                "intensity_split": "75/25",
                "max_quality_sessions": 3,
                "sport_sessions": {
                    "running": {"sessions": 3, "volume_note": "20-30 km/week, tempo + VO2max intervals + brick runs"},
                    "swimming": {"sessions": 3, "volume_note": "2-3 km/session, CSS intervals + race-pace sets"},
                    "cycling": {"sessions": 2, "volume_note": "60-90 min, threshold intervals + sweet spot"},
                    "strength": {"sessions": 2, "volume_note": "Maintenance — 2 sets, compounds + core"},
                },
            },
            {
                "id": "foundation",
                "name": "Phase 1: Foundation",
                "weeks_range": (8, 999),
                "total_weeks": 8,
                "priorities": "Build consistency across all 3 sports, establish aerobic base, fix swim technique",
                "hours_range": "5-7",
                "intensity_split": "90/10",
                "max_quality_sessions": 1,
                "sport_sessions": {
                    "running": {"sessions": 3, "volume_note": "20-28 km/week, easy runs + strides"},
                    "swimming": {"sessions": 2, "volume_note": "2-3 km/session, technique drills, CSS test"},
                    "cycling": {"sessions": 2, "volume_note": "60-75 min/session, Z2 endurance"},
                    "strength": {"sessions": 3, "volume_note": "Hypertrophy focus — best window for muscle building"},
                },
            },
        ],
        "workout_menus": {
            "foundation": _FOUNDATION_MENU,
            "build": {
                "allowed": {
                    "running": [
                        "Easy Run", "Strides/Openers", "Tempo Run",
                        "VO2max Intervals", "Repetitions (R)", "Progressive Run",
                    ],
                    "cycling": ["Endurance Ride (Z2)", "Sweet Spot Intervals", "Threshold Intervals", "Sprint Intervals"],
                    "swimming": ["Technique Session", "CSS Threshold", "Sprint Set"],
                    "strength": {
                        "types": ["Maintenance (2 sets, heavy compounds)"],
                        "available_muscle_groups": ["legs", "back", "arms"],
                        "note": "Maintenance only. Focus on running economy and core stability.",
                    },
                },
                "forbidden": [
                    "Marathon Pace Long Run", "Long Run (>15 km)",
                    "Endurance Swim (>3 km)", "Race Simulation Brick (save for Olympic+)",
                ],
                "reason": "Sprint Tri Build — speed across all 3 sports. Short, fast efforts.",
            },
            "taper": _TAPER_MENU,
        },
    },

    # ══════════════════════════════════════════════════════════════════════════
    # TRIATHLON — OLYMPIC (1500m swim / 40km bike / 10km run)
    # Stub — functional but may need tuning with real athlete feedback.
    # ══════════════════════════════════════════════════════════════════════════
    "Olympic": {
        "phases": [
            {
                "id": "taper",
                "name": "Phase 4: Olympic Tri Taper + Race",
                "weeks_range": (0, 2),
                "total_weeks": 2,
                "priorities": "Reduce volume, maintain race-pace touches, practice transitions",
                "hours_range": "4-5",
                "intensity_split": "80/20",
                "max_quality_sessions": 1,
                "sport_sessions": {
                    "running": {"sessions": 3, "volume_note": "20-25 km/week, easy + strides"},
                    "swimming": {"sessions": 2, "volume_note": "2 km/session, race-pace 1500m"},
                    "cycling": {"sessions": 1, "volume_note": "45 min easy spin"},
                    "strength": {"sessions": 0, "volume_note": "Stop all lifting 7-10 days before race"},
                },
            },
            {
                "id": "build",
                "name": "Phase 3: Olympic Tri Build",
                "weeks_range": (3, 7),
                "total_weeks": 5,
                "priorities": "Race-specific fitness, threshold work, brick sessions, transition practice",
                "hours_range": "8-10",
                "intensity_split": "80/20",
                "max_quality_sessions": 3,
                "sport_sessions": {
                    "running": {"sessions": 3, "volume_note": "30-40 km/week, tempo + cruise intervals + brick runs"},
                    "swimming": {"sessions": 3, "volume_note": "3-4 km/session, CSS + race-pace sets"},
                    "cycling": {"sessions": 3, "volume_note": "75-120 min, threshold + sweet spot + endurance"},
                    "strength": {"sessions": 2, "volume_note": "Maintenance — 2 sets, compounds, core stability"},
                },
            },
            {
                "id": "base",
                "name": "Phase 2: Olympic Tri Base",
                "weeks_range": (8, 13),
                "total_weeks": 6,
                "priorities": "Build volume across all 3 sports, establish sport-specific endurance",
                "hours_range": "7-9",
                "intensity_split": "80/20",
                "max_quality_sessions": 2,
                "sport_sessions": {
                    "running": {"sessions": 3, "volume_note": "25-35 km/week, long run 60-75 min, 1 tempo/week"},
                    "swimming": {"sessions": 3, "volume_note": "3-4 km/session, CSS intervals, build 1500m continuous"},
                    "cycling": {"sessions": 2, "volume_note": "75-90 min, Z2-Z3 + 1x sweet spot"},
                    "strength": {"sessions": 2, "volume_note": "Hypertrophy → maintenance transition"},
                },
            },
            {
                "id": "foundation",
                "name": "Phase 1: Foundation",
                "weeks_range": (14, 999),
                "total_weeks": 8,
                "priorities": "Build consistency across all 3 sports, fix swim technique, establish aerobic base",
                "hours_range": "6-8",
                "intensity_split": "90/10",
                "max_quality_sessions": 1,
                "sport_sessions": {
                    "running": {"sessions": 3, "volume_note": "20-28 km/week, easy runs + strides"},
                    "swimming": {"sessions": 2, "volume_note": "2-3 km/session, technique drills, CSS test"},
                    "cycling": {"sessions": 2, "volume_note": "60-75 min/session, Z2 endurance"},
                    "strength": {"sessions": 3, "volume_note": "Hypertrophy focus — best window for muscle building"},
                },
            },
        ],
        "workout_menus": {
            "foundation": _FOUNDATION_MENU,
            "base": {
                "allowed": {
                    "running": ["Easy Run", "Long Run", "Strides/Openers", "Tempo Run", "Cruise Intervals"],
                    "cycling": ["Endurance Ride (Z2)", "Sweet Spot Intervals"],
                    "swimming": ["Technique Session", "CSS Threshold", "Endurance Swim"],
                    "strength": {
                        "types": ["Hypertrophy Push/Pull/Legs", "Hypertrophy Upper/Lower"],
                        "available_muscle_groups": ["chest", "shoulders", "back", "legs", "arms"],
                        "note": "Coach decides the split. Always specify muscle_groups in output.",
                    },
                },
                "forbidden": [
                    "VO2max Intervals", "Marathon Pace Long Run", "Sprint Set (swim)",
                    "Race Simulation Brick", "Sprint Intervals (bike)",
                ],
                "reason": "Olympic Tri Base — building volume across 3 sports. No race-specific intensity yet.",
            },
            "build": {
                "allowed": {
                    "running": [
                        "Easy Run", "Long Run", "Strides/Openers", "Tempo Run",
                        "Cruise Intervals", "VO2max Intervals", "Progressive Run",
                    ],
                    "cycling": ["Endurance Ride (Z2)", "Sweet Spot Intervals", "Threshold Intervals"],
                    "swimming": ["Technique Session", "CSS Threshold", "Endurance Swim", "Sprint Set"],
                    "strength": {
                        "types": ["Maintenance (2 sets, heavy compounds)"],
                        "available_muscle_groups": ["chest", "shoulders", "back", "legs", "arms"],
                        "note": "Maintenance only. Coach picks which groups.",
                    },
                },
                "forbidden": ["Marathon Pace Long Run", "Race Simulation Brick"],
                "reason": "Olympic Tri Build — race-specific intensity. Threshold + VO2max across all sports.",
            },
            "taper": _TAPER_MENU,
        },
    },

    # ══════════════════════════════════════════════════════════════════════════
    # TRIATHLON — 70.3 HALF IRONMAN (1.9km swim / 90km bike / 21.1km run)
    # Stub — functional but may need tuning with real athlete feedback.
    # ══════════════════════════════════════════════════════════════════════════
    "70.3": {
        "phases": [
            {
                "id": "taper",
                "name": "Phase 5: 70.3 Taper + Race",
                "weeks_range": (0, 2),
                "total_weeks": 2,
                "priorities": "2-week taper, reduce volume 40-50%, maintain race-pace touches, practice nutrition",
                "hours_range": "5-7",
                "intensity_split": "80/20",
                "max_quality_sessions": 1,
                "sport_sessions": {
                    "running": {"sessions": 3, "volume_note": "20-25 km/week, easy + strides"},
                    "swimming": {"sessions": 2, "volume_note": "2-3 km/session, race-pace 1900m"},
                    "cycling": {"sessions": 2, "volume_note": "60-90 min easy, 1 short race-pace effort"},
                    "strength": {"sessions": 0, "volume_note": "Stop all lifting 10 days before race"},
                },
            },
            {
                "id": "peak",
                "name": "Phase 4: 70.3 Peak",
                "weeks_range": (3, 6),
                "total_weeks": 4,
                "priorities": "Race simulation bricks, hold volume, sharpen race-day execution",
                "hours_range": "10-13",
                "intensity_split": "80/20",
                "max_quality_sessions": 2,
                "sport_sessions": {
                    "running": {"sessions": 3, "volume_note": "35-45 km/week, brick runs + HM-pace segments"},
                    "swimming": {"sessions": 3, "volume_note": "3-4 km/session, CSS + race-pace 1900m"},
                    "cycling": {"sessions": 3, "volume_note": "4-5 hrs/week, long ride 2.5-3h + threshold"},
                    "strength": {"sessions": 1, "volume_note": "Maintenance — 2 sets max, compounds only"},
                },
            },
            {
                "id": "build",
                "name": "Phase 3: 70.3 Build",
                "weeks_range": (7, 13),
                "total_weeks": 7,
                "priorities": "Race-specific fitness, introduce bricks, build long bike, HM-pace running",
                "hours_range": "10-12",
                "intensity_split": "80/20",
                "max_quality_sessions": 2,
                "sport_sessions": {
                    "running": {"sessions": 3, "volume_note": "35-45 km/week, long run + tempo + brick runs"},
                    "swimming": {"sessions": 3, "volume_note": "3-4 km/session, CSS threshold + endurance"},
                    "cycling": {"sessions": 3, "volume_note": "4-6 hrs/week, long ride 2-2.5h + sweet spot + threshold"},
                    "strength": {"sessions": 2, "volume_note": "Shift to maintenance — 2 sets, keep compounds, drop accessories"},
                },
            },
            {
                "id": "base",
                "name": "Phase 2: 70.3 Base",
                "weeks_range": (14, 20),
                "total_weeks": 7,
                "priorities": "Build volume across all 3 sports, establish sport-specific endurance",
                "hours_range": "8-10",
                "intensity_split": "80/20",
                "max_quality_sessions": 2,
                "sport_sessions": {
                    "running": {"sessions": 3, "volume_note": "28-40 km/week, long run 60-90 min, 1 tempo/week"},
                    "swimming": {"sessions": 3, "volume_note": "3-4 km/session, CSS intervals, build 1900m continuous"},
                    "cycling": {"sessions": 2, "volume_note": "3-4 hrs/week, Z2-Z3 + 1x sweet spot, build toward 90 km"},
                    "strength": {"sessions": 3, "volume_note": "Hypertrophy but watch total fatigue — reduce sets if main sports suffer"},
                },
            },
            {
                "id": "foundation",
                "name": "Phase 1: Foundation",
                "weeks_range": (21, 999),
                "total_weeks": 8,
                "priorities": "Build consistency across all 3 sports, fix swim technique, establish aerobic base",
                "hours_range": "7-8",
                "intensity_split": "90/10",
                "max_quality_sessions": 1,
                "sport_sessions": {
                    "running": {"sessions": 3, "volume_note": "20-28 km/week, easy runs, build gradually (10% rule)"},
                    "swimming": {"sessions": 2, "volume_note": "2-3 km/session, technique drills, CSS test"},
                    "cycling": {"sessions": 2, "volume_note": "60-75 min/session, Z2 endurance"},
                    "strength": {"sessions": 3, "volume_note": "Hypertrophy focus — best window for muscle building"},
                },
            },
        ],
        "workout_menus": {
            "foundation": _FOUNDATION_MENU,
            "base": {
                "allowed": {
                    "running": ["Easy Run", "Long Run", "Strides/Openers", "Tempo Run", "Cruise Intervals"],
                    "cycling": ["Endurance Ride (Z2)", "Sweet Spot Intervals"],
                    "swimming": ["Technique Session", "CSS Threshold", "Endurance Swim"],
                    "strength": {
                        "types": ["Hypertrophy Push/Pull/Legs", "Hypertrophy Upper/Lower"],
                        "available_muscle_groups": ["chest", "shoulders", "back", "legs", "arms"],
                        "note": "Coach decides the split. Always specify muscle_groups in output.",
                    },
                },
                "forbidden": [
                    "VO2max Intervals", "Marathon Pace Long Run", "Race Simulation Brick",
                    "Sprint Intervals", "Threshold Intervals (bike)",
                ],
                "reason": "70.3 Base — building volume across 3 sports. No race-specific intensity yet.",
            },
            "build": {
                "allowed": {
                    "running": [
                        "Easy Run", "Long Run", "Strides/Openers", "Tempo Run",
                        "Cruise Intervals", "Progressive Run",
                    ],
                    "cycling": ["Endurance Ride (Z2)", "Sweet Spot Intervals", "Threshold Intervals"],
                    "swimming": ["Technique Session", "CSS Threshold", "Endurance Swim"],
                    "strength": {
                        "types": ["Maintenance (2 sets, heavy compounds)"],
                        "available_muscle_groups": ["chest", "shoulders", "back", "legs", "arms"],
                        "note": "Maintenance only. Coach picks which groups.",
                    },
                },
                "forbidden": [
                    "Marathon Pace Long Run", "VO2max Intervals (run)", "Sprint Set (swim)",
                ],
                "reason": "70.3 Build — race-specific endurance. Long bike, threshold work, introduce bricks.",
            },
            "peak": {
                "allowed": {
                    "running": [
                        "Easy Run", "Long Run", "Strides/Openers", "Tempo Run",
                        "Cruise Intervals", "Progressive Run",
                    ],
                    "cycling": ["Endurance Ride (Z2)", "Sweet Spot Intervals", "Threshold Intervals"],
                    "swimming": ["Technique Session", "CSS Threshold", "Endurance Swim"],
                    "strength": {
                        "types": ["Maintenance (2 sets, heavy compounds)"],
                        "available_muscle_groups": ["chest", "shoulders", "back", "legs", "arms"],
                        "note": "Maintenance only. Coach picks which groups.",
                    },
                },
                "forbidden": ["Sprint Set (swim)", "Sprint Intervals (bike)"],
                "reason": "70.3 Peak — race simulation bricks, sharpen race execution. Final key workouts.",
            },
            "taper": _TAPER_MENU,
        },
    },

    # ══════════════════════════════════════════════════════════════════════════
    # TRIATHLON — IRONMAN (3.8km swim / 180km bike / 42.2km run)
    # Stub — functional but may need tuning with real athlete feedback.
    # ══════════════════════════════════════════════════════════════════════════
    "Ironman": {
        "phases": [
            {
                "id": "taper",
                "name": "Phase 5: Ironman Taper + Race",
                "weeks_range": (0, 3),
                "total_weeks": 3,
                "priorities": "3-week taper, reduce volume progressively, maintain easy pace touches, race nutrition rehearsal",
                "hours_range": "6-8",
                "intensity_split": "80/20",
                "max_quality_sessions": 1,
                "sport_sessions": {
                    "running": {"sessions": 3, "volume_note": "20-30 km/week, easy + strides"},
                    "swimming": {"sessions": 2, "volume_note": "2-3 km/session, easy technique"},
                    "cycling": {"sessions": 2, "volume_note": "60-90 min easy, practice nutrition"},
                    "strength": {"sessions": 0, "volume_note": "Stop all lifting 10 days before race"},
                },
            },
            {
                "id": "peak",
                "name": "Phase 4: Ironman Peak",
                "weeks_range": (4, 7),
                "total_weeks": 4,
                "priorities": "Race simulation bricks, final long sessions, practiced race-day nutrition",
                "hours_range": "14-18",
                "intensity_split": "80/20",
                "max_quality_sessions": 2,
                "sport_sessions": {
                    "running": {"sessions": 3, "volume_note": "40-55 km/week, long run 2.5-3h + MP segments"},
                    "swimming": {"sessions": 3, "volume_note": "4-5 km/session, race-pace 3800m continuous"},
                    "cycling": {"sessions": 3, "volume_note": "6-8 hrs/week, long ride 4-5h + race-pace bricks"},
                    "strength": {"sessions": 1, "volume_note": "Minimal maintenance, stop 2 weeks before race"},
                },
            },
            {
                "id": "build",
                "name": "Phase 3: Ironman Build",
                "weeks_range": (8, 15),
                "total_weeks": 8,
                "priorities": "Race-specific endurance, long sessions, bricks, race nutrition practice",
                "hours_range": "12-16",
                "intensity_split": "80/20",
                "max_quality_sessions": 2,
                "sport_sessions": {
                    "running": {"sessions": 4, "volume_note": "40-55 km/week, long run 2-2.5h + tempo + brick runs"},
                    "swimming": {"sessions": 3, "volume_note": "4-5 km/session, CSS + endurance sets"},
                    "cycling": {"sessions": 3, "volume_note": "5-7 hrs/week, long ride 3-4h + sweet spot + threshold"},
                    "strength": {"sessions": 2, "volume_note": "Maintenance — 2 sets, compounds only"},
                },
            },
            {
                "id": "base",
                "name": "Phase 2: Ironman Base",
                "weeks_range": (16, 23),
                "total_weeks": 8,
                "priorities": "Build massive aerobic volume, long rides and runs, swim endurance",
                "hours_range": "10-14",
                "intensity_split": "80/20",
                "max_quality_sessions": 2,
                "sport_sessions": {
                    "running": {"sessions": 4, "volume_note": "30-45 km/week, long run 90-120 min, 1 tempo/week"},
                    "swimming": {"sessions": 3, "volume_note": "3-5 km/session, CSS + endurance, build 3800m continuous"},
                    "cycling": {"sessions": 3, "volume_note": "4-6 hrs/week, long ride 2-3h, Z2-Z3 + sweet spot"},
                    "strength": {"sessions": 2, "volume_note": "Hypertrophy → maintenance transition, watch fatigue"},
                },
            },
            {
                "id": "foundation",
                "name": "Phase 1: Foundation",
                "weeks_range": (24, 999),
                "total_weeks": 8,
                "priorities": "Build consistency across all 3 sports, fix swim technique, establish aerobic base",
                "hours_range": "7-8",
                "intensity_split": "90/10",
                "max_quality_sessions": 1,
                "sport_sessions": {
                    "running": {"sessions": 3, "volume_note": "20-28 km/week, easy runs, build gradually (10% rule)"},
                    "swimming": {"sessions": 2, "volume_note": "2-3 km/session, technique drills, CSS test"},
                    "cycling": {"sessions": 2, "volume_note": "60-75 min/session, Z2 endurance"},
                    "strength": {"sessions": 3, "volume_note": "Hypertrophy focus — best window for muscle building"},
                },
            },
        ],
        "workout_menus": {
            "foundation": _FOUNDATION_MENU,
            "base": {
                "allowed": {
                    "running": ["Easy Run", "Long Run", "Strides/Openers", "Tempo Run", "Cruise Intervals"],
                    "cycling": ["Endurance Ride (Z2)", "Sweet Spot Intervals"],
                    "swimming": ["Technique Session", "CSS Threshold", "Endurance Swim"],
                    "strength": {
                        "types": ["Hypertrophy Push/Pull/Legs", "Hypertrophy Upper/Lower"],
                        "available_muscle_groups": ["chest", "shoulders", "back", "legs", "arms"],
                        "note": "Coach decides the split. Always specify muscle_groups in output.",
                    },
                },
                "forbidden": [
                    "VO2max Intervals", "Marathon Pace Long Run", "Sprint Set (swim)",
                    "Race Simulation Brick", "Sprint Intervals (bike)",
                ],
                "reason": "Ironman Base — massive aerobic volume building across 3 sports.",
            },
            "build": {
                "allowed": {
                    "running": [
                        "Easy Run", "Long Run", "Strides/Openers", "Tempo Run",
                        "Cruise Intervals", "Marathon Pace Long Run", "Progressive Run",
                    ],
                    "cycling": ["Endurance Ride (Z2)", "Sweet Spot Intervals", "Threshold Intervals"],
                    "swimming": ["Technique Session", "CSS Threshold", "Endurance Swim"],
                    "strength": {
                        "types": ["Maintenance (2 sets, heavy compounds)"],
                        "available_muscle_groups": ["chest", "shoulders", "back", "legs", "arms"],
                        "note": "Maintenance only. Coach picks which groups.",
                    },
                },
                "forbidden": ["Sprint Set (swim)", "Sprint Intervals (bike)"],
                "reason": "Ironman Build — race-specific endurance. Long sessions, bricks, race nutrition practice.",
            },
            "peak": {
                "allowed": {
                    "running": [
                        "Easy Run", "Long Run", "Strides/Openers", "Tempo Run",
                        "Cruise Intervals", "Marathon Pace Long Run", "Progressive Run",
                    ],
                    "cycling": ["Endurance Ride (Z2)", "Sweet Spot Intervals", "Threshold Intervals"],
                    "swimming": ["Technique Session", "CSS Threshold", "Endurance Swim"],
                    "strength": {
                        "types": ["Maintenance (2 sets, heavy compounds)"],
                        "available_muscle_groups": ["legs", "back"],
                        "note": "Minimal maintenance. Stop 2 weeks before race.",
                    },
                },
                "forbidden": ["Sprint Set (swim)", "Sprint Intervals (bike)"],
                "reason": "Ironman Peak — race simulation bricks, final long sessions. Trust your fitness.",
            },
            "taper": _TAPER_MENU,
        },
    },
}


# Fallback for unknown distances — foundation-only training is safest for the LLM.
FOUNDATION_ONLY_PROFILE = {
    "phases": [
        {
            "id": "foundation",
            "name": "Phase 1: Foundation",
            "weeks_range": (0, 999),
            "total_weeks": 52,
            "priorities": "Build consistency, establish aerobic base, general fitness",
            "hours_range": "6-8",
            "intensity_split": "90/10",
            "max_quality_sessions": 1,
            "sport_sessions": {
                "running": {"sessions": 3, "volume_note": "20-28 km/week, easy runs, build gradually (10% rule)"},
                "swimming": {"sessions": 2, "volume_note": "2-3 km/session, technique drills, CSS test"},
                "cycling": {"sessions": 2, "volume_note": "60-75 min/session, Z2 endurance"},
                "strength": {"sessions": 3, "volume_note": "Hypertrophy focus — best window for muscle building"},
            },
        },
    ],
    "workout_menus": {
        "foundation": _FOUNDATION_MENU,
    },
}


class PeriodizationEngine:
    """Computes the TrainingContext — structured data for the LLM coach."""

    def _get_profile(self, race_distance: str) -> dict:
        """Get the distance-specific profile configuration.

        Falls back to FOUNDATION_ONLY_PROFILE for unknown distances.
        """
        return DISTANCE_PROFILES.get(race_distance, FOUNDATION_ONLY_PROFILE)

    def compute_context(self, db: Session) -> dict:
        """
        Main public method. Computes the full TrainingContext dict.
        This is a lightweight computation (DB queries + date math, no LLM).
        """
        athlete = db.query(Athlete).first()
        if not athlete:
            return self._empty_context("No athlete profile found")

        today = get_local_today()

        # Weeks to race
        if athlete.race_date:
            days_to_race = (athlete.race_date - today).days
            weeks_to_race = max(0, days_to_race // 7)
        else:
            weeks_to_race = 99  # No race set → default to foundation

        # Distance-aware profile
        race_distance = athlete.race_distance or "Marathon"
        profile = self._get_profile(race_distance)

        # Phase
        phase_info = self._determine_phase(weeks_to_race, race_distance)

        # Build/Recovery cycle
        cycle_info = self._get_cycle_week(athlete.training_start_date, today)

        # Workout menu — from distance-specific profile, minus sports the
        # athlete has disabled (empty availability string = never)
        menus = profile["workout_menus"]
        menu_info = menus.get(phase_info["phase"], menus.get("foundation", _FOUNDATION_MENU))
        menu_allowed = self._filter_by_availability(menu_info["allowed"], athlete)

        # Volume references
        volume_refs = self._get_volume_references(
            phase_info, cycle_info["is_recovery_week"], athlete, db,
            race_distance=race_distance
        )

        # THE weekly run-km target — actuals-derived, ramp-capped. The volume
        # gate enforces these numbers; the phase range above is only prose.
        volume_targets = self._get_weekly_run_target(
            db, self._phase_def(profile, phase_info),
            cycle_info["is_recovery_week"], today,
        )

        # Python-derived training paces from the watch's LT pace (running
        # races only). None degrades every consumer to HR-zone guidance.
        pace_model = compute_pace_model(
            athlete.threshold_pace_min_km, athlete.target_finish_time,
            race_distance,
        )

        # Recovery status
        recovery = self._get_recovery_status(db)

        # Last week summary
        last_week = self._get_last_week_summary(db)

        # Sport availability
        availability = {
            "swim_days": athlete.swim_days if athlete.swim_days is not None else "wed,sat,sun",
            "bike_days": athlete.bike_days if athlete.bike_days is not None else "mon,tue,wed,thu,fri,sat,sun",
            "run_days": athlete.run_days if athlete.run_days is not None else "mon,tue,wed,thu,fri,sat,sun",
            "strength_days": athlete.strength_days if athlete.strength_days is not None else "mon,wed,fri",
        }

        # Travel days for the week being planned (context is always computed
        # for the current week). Carried inside availability so every
        # context-fed enforce_constraints call inherits the block unchanged.
        start_of_week = today - timedelta(days=today.weekday())
        travel_day_names = get_travel_day_names(db, athlete.id, start_of_week)
        if travel_day_names:
            availability["travel_day_names"] = travel_day_names

        return {
            # Where are we?
            "current_date": today.isoformat(),
            "race_date": athlete.race_date.isoformat() if athlete.race_date else None,
            "race_name": athlete.race_name or "Not set",
            "race_type": athlete.race_type or "Not set",
            "race_distance": athlete.race_distance or "Not set",
            "weeks_to_race": weeks_to_race,

            # What phase?
            "phase": phase_info["phase"],
            "phase_name": phase_info["phase_name"],
            "phase_week": phase_info["phase_week"],
            "phase_total_weeks": phase_info["phase_total_weeks"],
            "phase_priorities": phase_info["phase_priorities"],

            # Build or recovery week?
            "cycle_week": cycle_info["cycle_week"],
            "is_recovery_week": cycle_info["is_recovery_week"],
            "recovery_note": cycle_info["recovery_note"],

            # Workout toolbox
            "workout_menu": menu_allowed,
            "forbidden_workouts": menu_info["forbidden"],
            "forbidden_reason": menu_info["reason"],

            # Volume references (NOT hard limits — reference data for the LLM)
            "volume_references": volume_refs,

            # Computed weekly run target (hard numbers — the volume gate
            # enforces run_km_hard_cap). None for profiles without a run range.
            "volume_targets": volume_targets,

            # How is the body?
            "recovery": recovery,

            # What happened last week?
            "last_week": last_week,

            # Sport availability
            "availability": availability,

            # Athlete profile snapshot
            "athlete": {
                "name": athlete.name,
                "hr_max": athlete.hr_max,  # honestly null until a real max-HR source exists
                "lthr": athlete.lthr,
                "hr_rest": athlete.hr_rest,
                "vo2_max": athlete.vo2_max,
                "hrv_baseline": athlete.hrv_baseline,
            },
            "race_goals": {
                "target_finish_time": athlete.target_finish_time,
            },

            # Training paces computed from measured threshold (pace_model.py).
            "pace_model": pace_model,
        }

    def compute_block_calendar(self, db: Session) -> dict:
        """
        Returns all weeks from training_start_date to race_date,
        each annotated with phase, cycle position, and plan summary if stored.
        """
        from datetime import date, timedelta, datetime
        from backend.models.database import Athlete, WeeklyPlan, Activity
        
        athlete = db.query(Athlete).first()
        if not athlete:
            return {
                "total_weeks": 0,
                "training_start_date": "",
                "race_date": "",
                "race_name": "",
                "current_week_number": 0,
                "weeks": []
            }
            
        today = get_local_today()
        race_distance = athlete.race_distance or "Marathon"
        profile = self._get_profile(race_distance)

        # Default start date if not set (today's Monday)
        t_start = athlete.training_start_date or (today - timedelta(days=today.weekday()))
        # Default race date if not set (12 weeks from today's Sunday)
        t_race = athlete.race_date or (today + timedelta(weeks=12) + timedelta(days=6 - today.weekday()))
        
        # Align to Monday of start week and Sunday of race week
        start_monday = t_start - timedelta(days=t_start.weekday())
        race_sunday = t_race + timedelta(days=6 - t_race.weekday())
        race_monday = t_race - timedelta(days=t_race.weekday())
        
        total_days = (race_sunday - start_monday).days + 1
        total_weeks = max(1, total_days // 7)
        
        # Load all weekly plans for this athlete to avoid N+1 queries
        plans = db.query(WeeklyPlan).filter(WeeklyPlan.athlete_id == athlete.id).all()
        plans_by_start = {p.week_start: p for p in plans}
        
        # Pre-load all activities in the training block for training load calculation
        all_activities = db.query(Activity).filter(
            Activity.start_time >= datetime.combine(start_monday, datetime.min.time()),
            Activity.start_time <= datetime.combine(race_sunday, datetime.max.time()),
        ).all()
        
        # Bucket activities by their week's Monday date
        activities_by_week = {}
        for act in all_activities:
            act_date = act.start_time.date() if isinstance(act.start_time, datetime) else act.start_time
            act_monday = act_date - timedelta(days=act_date.weekday())
            if act_monday not in activities_by_week:
                activities_by_week[act_monday] = []
            activities_by_week[act_monday].append(act)
        
        weeks_list = []
        current_week_number = 1
        today_monday = today - timedelta(days=today.weekday())
        
        for i in range(total_weeks):
            week_start = start_monday + timedelta(weeks=i)
            week_end = week_start + timedelta(days=6)
            
            # Weeks to race from this week's start
            w_to_race = (race_monday - week_start).days // 7
            
            # Determine phase & cycle week — distance-aware
            phase_info = self._determine_phase(w_to_race, race_distance)
            cycle_info = self._get_cycle_week(t_start, week_start)
            
            is_current = (week_start == today_monday)
            if is_current:
                current_week_number = i + 1
                
            plan_rec = plans_by_start.get(week_start)
            has_plan = plan_rec is not None
            plan_summary = None
            expected_total_hours = "0"
            expected_run_km = "0"
            workouts_list = []
            
            phase_def = self._phase_def(profile, phase_info)
            
            if plan_rec and plan_rec.plan_json:
                ws = plan_rec.plan_json.get("week_summary", {})
                plan_summary = ws.get("focus") or "Plan Active"
                
                hours_val = ws.get("expected_total_hours")
                expected_total_hours = f"{hours_val:.1f}" if isinstance(hours_val, (int, float)) else str(hours_val or "0")
                
                run_val = ws.get("expected_run_km")
                expected_run_km = f"{run_val:.1f}" if isinstance(run_val, (int, float)) else str(run_val or "0")
                
                # Fetch workouts list
                days_dict = plan_rec.plan_json.get("days", {})
                for day_name in ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]:
                    day_data = days_dict.get(day_name, {})
                    day_workouts = day_data.get("workouts", [])
                    for w in day_workouts:
                        raw_steps = w.get("steps", [])
                        steps_data = []
                        for s in raw_steps:
                            steps_data.append({
                                "type": s.get("type", "main"),
                                "duration": str(s.get("duration", "0")),
                                "zone": s.get("zone"),
                                "description": s.get("description")
                            })
                        workouts_list.append({
                            "day": day_name,
                            "sport": w.get("sport", "rest"),
                            "title": w.get("title", "Rest Day"),
                            "total_time": w.get("total_time") or "0 min",
                            "hr_target": w.get("hr_target") or "--",
                            "muscle_groups": w.get("muscle_groups", []),
                            "steps_count": len(raw_steps),
                            "steps": steps_data
                        })
            else:
                # If no plan exists yet, provide projected target values from the phase
                hours_range = phase_def["hours_range"]
                # Apply recovery week adjustment if it is a recovery week
                if cycle_info["is_recovery_week"]:
                    hours_parts = hours_range.split("-")
                    if len(hours_parts) == 2:
                        try:
                            low = float(hours_parts[0]) * 0.75
                            high = float(hours_parts[1]) * 0.75
                            hours_range = f"{low:.0f}-{high:.0f}"
                        except Exception:
                            pass
                expected_total_hours = hours_range
                
                # Try to parse running volume note from phase definition
                run_info = phase_def["sport_sessions"].get("running", {})
                vol_note = run_info.get("volume_note", "")
                match = re.search(r"(\d+-\d+|\d+)\s*km", vol_note)
                if match:
                    expected_run_km = match.group(1)
                else:
                    expected_run_km = "20-28"  # fallback default
                
            # Compute actual training load from recorded activities for this week
            week_activities = activities_by_week.get(week_start, [])
            actual_training_load = sum((a.training_load or 0) for a in week_activities)
            actual_training_load = round(actual_training_load, 1) if actual_training_load > 0 else None
            
            weeks_list.append({
                "week_number": i + 1,
                "week_start": week_start.isoformat(),
                "week_end": week_end.isoformat(),
                "phase": phase_info["phase"],
                "phase_name": phase_info["phase_name"],
                "cycle_week": cycle_info["cycle_week"],
                "is_recovery_week": cycle_info["is_recovery_week"],
                "is_current_week": is_current,
                "has_plan": has_plan,
                "plan_summary": plan_summary,
                "expected_total_hours": expected_total_hours,
                "expected_run_km": expected_run_km,
                "actual_training_load": actual_training_load,
                "workouts": workouts_list
            })
            
        return {
            "total_weeks": total_weeks,
            "training_start_date": t_start.isoformat(),
            "race_date": t_race.isoformat() if athlete.race_date else None,
            "race_name": athlete.race_name or "My Goal Race",
            "current_week_number": current_week_number,
            "weeks": weeks_list
        }

    # ─── Internal methods ─────────────────────────────────────────────────

    def _determine_phase(self, weeks_to_race: int, race_distance: str = "Marathon") -> dict:
        """Map weeks-to-race to a training phase using distance-specific phase tables."""
        profile = self._get_profile(race_distance)
        phases = profile["phases"]

        for phase in phases:
            low, high = phase["weeks_range"]
            if low <= weeks_to_race <= high:
                # Compute which week within this phase we're on
                # e.g., if foundation is weeks 23-30, and we're at week 29,
                # that means we're in week 2 of foundation (30 - 29 + 1 = 2)
                phase_week = min(
                    phase["total_weeks"],
                    weeks_to_race - low + 1
                )
                # Invert so week 1 is the first week of the phase
                phase_week = phase["total_weeks"] - phase_week + 1

                return {
                    "phase": phase["id"],
                    "phase_name": phase["name"],
                    "phase_week": phase_week,
                    "phase_total_weeks": phase["total_weeks"],
                    "phase_priorities": phase["priorities"],
                }

        # Fallback to foundation
        fallback = phases[-1]  # Last phase is always foundation
        return {
            "phase": fallback["id"],
            "phase_name": fallback["name"],
            "phase_week": 1,
            "phase_total_weeks": fallback["total_weeks"],
            "phase_priorities": fallback["priorities"],
        }

    def _get_cycle_week(self, training_start_date: Optional[date], current_date: date) -> dict:
        """Compute position in the 3:1 build/recovery cycle."""
        if not training_start_date:
            return {
                "cycle_week": 1,
                "is_recovery_week": False,
                "recovery_note": "Build week 1/3 (training start date not set)",
            }

        days_elapsed = (current_date - training_start_date).days
        weeks_elapsed = days_elapsed // 7
        cycle_week = (weeks_elapsed % 4) + 1  # 1-indexed: 1, 2, 3, 4

        is_recovery = cycle_week == 4

        if is_recovery:
            note = "🔄 Recovery week — volume drops 25%, max 1 quality session (strides/openers), prioritize sleep and nutrition"
        elif cycle_week == 3:
            note = "Build week 3/3 — next week is recovery"
        else:
            note = f"Build week {cycle_week}/3"

        return {
            "cycle_week": cycle_week,
            "is_recovery_week": is_recovery,
            "recovery_note": note,
        }

    def _filter_by_availability(self, mapping: dict, athlete: Athlete) -> dict:
        """
        Filter a copy of a phase's per-sport mapping (sport_sessions or a
        workout menu's "allowed" dict) by the athlete's sport availability.

        WHY: the phase tables in DISTANCE_PROFILES were written for the generic
        athlete. Without this, one prompt says "Swimming: 2x/week" (from
        sport_sessions) and "Swimming: NEVER" (from availability) — the enforcer
        strips the swims afterward, but the LLM wastes slots planning them. An
        empty availability string means "never" (sport removed); fewer available
        days than advertised sessions caps the session count; None means "no
        constraint recorded" and is left alone — the same semantics as
        constraint_enforcer.parse_day_list.

        The input is deep-copied before editing: DISTANCE_PROFILES is
        module-level shared state, and mutating it would poison every later
        request in the process.
        """
        filtered = copy.deepcopy(mapping)

        for sport in list(filtered.keys()):
            availability_key = SPORT_TO_AVAILABILITY_KEY.get(sport)
            if availability_key is None:
                continue  # no availability column for this sport
            allowed = parse_day_list(getattr(athlete, availability_key, None))
            if allowed is None:
                continue  # no constraint recorded
            if not allowed:
                del filtered[sport]  # explicitly disabled
                continue
            info = filtered[sport]
            if (isinstance(info, dict) and isinstance(info.get("sessions"), int)
                    and info["sessions"] > len(allowed)):
                info["sessions"] = len(allowed)
                info["volume_note"] = (
                    f"{info.get('volume_note', '')} "
                    f"(capped — only {len(allowed)} day(s) available)"
                ).strip()

        return filtered

    @staticmethod
    def _phase_def(profile: dict, phase_info: dict) -> dict:
        """The profile's phase definition for phase_info, falling back to the
        last phase (foundation) when the id is unknown."""
        for p in profile["phases"]:
            if p["id"] == phase_info["phase"]:
                return p
        return profile["phases"][-1]

    def _get_volume_references(
        self, phase_info: dict, is_recovery_week: bool,
        athlete: Athlete, db: Session,
        race_distance: str = "Marathon"
    ) -> dict:
        """
        Compute volume reference data for the LLM.
        These are NOT hard limits — they're reference points backed by:
        - distance-specific phase guidelines
        - COROS recommended training load range (from the watch)
        """
        profile = self._get_profile(race_distance)
        phase_def = self._phase_def(profile, phase_info)

        # COROS recommended TL range from latest recovery snapshot
        latest_snapshot = db.query(RecoverySnapshot).order_by(
            RecoverySnapshot.date.desc()
        ).first()

        coros_tl_range = None
        if latest_snapshot and latest_snapshot.recommend_tl_min and latest_snapshot.recommend_tl_max:
            coros_tl_range = {
                "min": round(latest_snapshot.recommend_tl_min),
                "max": round(latest_snapshot.recommend_tl_max),
            }

        # Volume references — sport_sessions filtered to what the athlete's
        # availability actually permits (a filtered copy, never the profile dict)
        sport_sessions = self._filter_by_availability(
            phase_def["sport_sessions"], athlete
        )
        refs = {
            "phase_hours_range": phase_def["hours_range"],
            "coros_tl_range": coros_tl_range,
            "intensity_split": phase_def["intensity_split"],
            "max_quality_sessions": phase_def["max_quality_sessions"],
            "sport_sessions": sport_sessions,
        }

        # Recovery week adjustment
        if is_recovery_week:
            refs["recovery_week_adjustment"] = (
                "Recovery week: reduce all volumes 20-25%. "
                "Max 1 quality session (strides or openers only). "
                "Keep training frequency but shorten sessions."
            )
            refs["max_quality_sessions"] = 1
            # Adjust hours range
            hours_parts = phase_def["hours_range"].split("-")
            if len(hours_parts) == 2:
                low = float(hours_parts[0]) * 0.75
                high = float(hours_parts[1]) * 0.75
                refs["phase_hours_range"] = f"{low:.0f}-{high:.0f}"
        else:
            refs["recovery_week_adjustment"] = None

        return refs

    def _get_weekly_run_target(
        self, db: Session, phase_def: dict,
        is_recovery_week: bool, today: date, tuneup_week: bool = False,
    ) -> Optional[dict]:
        """THE weekly run-km target, derived from what the athlete actually ran.

        RUN_RAMP x the best of the last 3 weeks' actual run km, clamped to the
        phase floor/ceiling — but a hard ramp cap of RUN_RAMP_HARD_CAP x
        demonstrated volume beats the phase floor, so thin history (post-wipe,
        post-break) rebuilds gradually instead of cliff-jumping to the phase
        range. Best-of-3 rather than mean: one missed week can't crater the
        target (the false-detraining signal _get_last_week_summary warns
        about must never shrink a plan).

        Returns None when the phase has no run range (triathlon stubs without
        km in their volume_note) — callers and the prompt degrade to the
        phase-range prose. The volume gate (B1) enforces run_km_hard_cap;
        this method only computes it.
        """
        rng = phase_def.get("run_km_range")
        if not rng:
            note = phase_def.get("sport_sessions", {}).get("running", {}).get("volume_note", "")
            m = re.search(r"(\d+)\s*-\s*(\d+)\s*km", note)
            if not m:
                return None
            rng = (float(m.group(1)), float(m.group(2)))
        floor, ceiling = float(rng[0]), float(rng[1])
        long_run_cap = phase_def.get("long_run_cap_min")

        monday = today - timedelta(days=today.weekday())
        window_start = monday - timedelta(days=21)
        activities = db.query(Activity).filter(
            Activity.start_time >= datetime.combine(window_start, datetime.min.time()),
            Activity.start_time < datetime.combine(monday, datetime.min.time()),
        ).all()

        week_km: dict[int, float] = {}
        longest_run_min = 0.0
        for a in activities:
            if (a.sport or "") != "running" or not a.start_time:
                continue
            km = (a.distance_m or 0) / 1000
            wk = (a.start_time.date() - window_start).days // 7
            week_km[wk] = week_km.get(wk, 0.0) + km
            if km >= 8 and a.duration_sec:
                longest_run_min = max(longest_run_min, a.duration_sec / 60)

        weeks_with_data = [v for v in week_km.values() if v > RUN_NOISE_FLOOR_KM]

        if not weeks_with_data:
            ramp_base = 0.0
            target = floor
            hard_cap = floor * 1.10
            rebuild_mode = False
            basis = (
                f"no run history in the last 3 weeks — starting at the "
                f"phase floor ({floor:g} km)"
            )
        else:
            ramp_base = max(weeks_with_data)
            ramp_cap = ramp_base * RUN_RAMP_HARD_CAP
            target = min(ceiling, max(floor, ramp_base * RUN_RAMP), ramp_cap)
            rebuild_mode = ramp_cap < floor
            hard_cap = min(ceiling * 1.05, ramp_cap)
            basis = (
                f"{RUN_RAMP:g}x best of last {len(weeks_with_data)} week(s) "
                f"({ramp_base:.1f} km), clamped to phase {floor:g}-{ceiling:g} km"
            )
            if rebuild_mode:
                basis += (
                    f" — rebuild mode: ramp cap {ramp_cap:.1f} km is below the "
                    f"phase floor, building back gradually"
                )

        if longest_run_min > 0:
            long_run = longest_run_min + LONG_RUN_STEP_MIN
            if long_run_cap:
                long_run = min(long_run_cap, long_run)
        else:
            long_run = min(long_run_cap, 70) if long_run_cap else 70

        if is_recovery_week:
            target *= 0.75
            hard_cap *= 0.80
            long_run *= 0.7
        if tuneup_week:
            # C7 hook: race week — the tune-up race IS the long run.
            target *= 0.6
            hard_cap *= 0.65
            long_run = 0

        return {
            "run_km_target": round(target, 1),
            "run_km_floor": floor,
            "run_km_ceiling": ceiling,
            "run_km_hard_cap": round(hard_cap, 1),
            "long_run_minutes": int(round(long_run)),
            "history_weeks": len(weeks_with_data),
            "ramp_base_km": round(ramp_base, 1),
            "rebuild_mode": rebuild_mode,
            "basis": basis,
        }

    def _get_recovery_status(self, db: Session) -> dict:
        """Assess current recovery status from recent snapshots."""
        snapshots = db.query(RecoverySnapshot).order_by(
            RecoverySnapshot.date.desc()
        ).limit(7).all()

        if not snapshots:
            return {
                "hrv_vs_baseline": "unknown",
                "rhr_trend": "unknown",
                "tib": None,
                "load_ratio": None,
                "status": "unknown",
                "detail": "No recovery data available. Train conservatively.",
            }

        latest = snapshots[0]
        concerns = []

        # HRV vs baseline
        hrv_vs_baseline = "unknown"
        if latest.hrv_ms and latest.hrv_baseline and latest.hrv_baseline > 0:
            hrv_pct = ((latest.hrv_ms - latest.hrv_baseline) / latest.hrv_baseline) * 100
            hrv_vs_baseline = f"{hrv_pct:+.0f}%"

            # Check multi-day trend
            recent_hrv = [s.hrv_ms for s in snapshots[:3] if s.hrv_ms]
            if len(recent_hrv) >= 2 and latest.hrv_baseline:
                avg_recent = sum(recent_hrv) / len(recent_hrv)
                avg_pct = ((avg_recent - latest.hrv_baseline) / latest.hrv_baseline) * 100
                if avg_pct < -10:
                    concerns.append(f"HRV {avg_pct:.0f}% below baseline for {len(recent_hrv)} days — significant fatigue signal")
                elif avg_pct < -5:
                    concerns.append(f"HRV {avg_pct:.0f}% below baseline — mild fatigue signal")

        # RHR trend
        rhr_values = [s.resting_hr for s in snapshots[:7] if s.resting_hr]
        rhr_trend = "unknown"
        if len(rhr_values) >= 3:
            avg_rhr = sum(rhr_values) / len(rhr_values)
            if rhr_values[0]:
                diff = rhr_values[0] - avg_rhr
                if diff > 5:
                    rhr_trend = "elevated"
                    concerns.append(f"RHR {rhr_values[0]} bpm — {diff:.0f} bpm above 7-day average")
                elif diff > 3:
                    rhr_trend = "slightly_elevated"
                    concerns.append(f"RHR slightly elevated ({diff:.0f} bpm above average)")
                else:
                    rhr_trend = "stable"

        # TIB (form)
        tib = latest.tib
        if tib is not None:
            if tib < -20:
                concerns.append(f"TIB at {tib:.0f} — deep fatigue territory, consider reducing load")
            elif tib < -15:
                concerns.append(f"TIB at {tib:.0f} — accumulated fatigue building")

        # Load ratio
        load_ratio = latest.load_ratio
        if load_ratio is not None:
            if load_ratio > 1.5:
                concerns.append(f"Load ratio {load_ratio:.2f} — HIGH injury risk, reduce training")
            elif load_ratio > 1.3:
                concerns.append(f"Load ratio {load_ratio:.2f} — approaching overreach, monitor carefully")

        # Determine status color
        if len(concerns) >= 2 or any("HIGH" in c or "significant" in c or "deep fatigue" in c for c in concerns):
            status = "red"
            detail = "Multiple fatigue signals detected. Prioritize recovery. " + " ".join(concerns)
        elif len(concerns) >= 1:
            status = "yellow"
            detail = "Minor concern detected. Adjust intensity if needed. " + " ".join(concerns)
        else:
            status = "green"
            detail = "All recovery metrics look normal. Good to train as planned."

        return {
            "hrv_vs_baseline": hrv_vs_baseline,
            "rhr_trend": rhr_trend,
            "tib": round(tib, 1) if tib is not None else None,
            "load_ratio": round(load_ratio, 2) if load_ratio is not None else None,
            "status": status,
            "detail": detail,
        }

    def _get_last_week_summary(self, db: Session) -> dict:
        """Summarize last week's training for continuity."""
        today = get_local_today()
        # Last week = Monday to Sunday before the current week
        current_monday = today - timedelta(days=today.weekday())
        last_monday = current_monday - timedelta(days=7)
        last_sunday = current_monday - timedelta(days=1)

        activities = db.query(Activity).filter(
            Activity.start_time >= datetime.combine(last_monday, datetime.min.time()),
            Activity.start_time <= datetime.combine(last_sunday, datetime.max.time()),
        ).all()

        # Completeness guard: the scraper's list page only reaches back ~7
        # activities, so a fresh or recently-wiped DB under-reports last week
        # and reads as false detraining. If the DB's earliest activity is
        # later than last Monday, the window can't be trusted. (start_time is
        # UTC vs local last_monday — a boundary activity can misclassify by a
        # day, irrelevant at this granularity.)
        earliest = db.query(func.min(Activity.start_time)).scalar()
        history_complete = earliest is not None and earliest.date() <= last_monday

        # Plan lookup happens before the no-activities return: a week where a
        # plan existed but zero activities synced is real non-compliance (0%),
        # not "no plan on record".
        last_plan = db.query(WeeklyPlan).filter(
            WeeklyPlan.week_start == last_monday
        ).first()

        sessions_planned = 0
        missed = []
        if last_plan and last_plan.plan_json:
            days_dict = last_plan.plan_json.get("days", {})
            for day_name, day_data in days_dict.items():
                workouts = day_data.get("workouts", [])
                for w in workouts:
                    if w.get("sport", "").lower() != "rest":
                        sessions_planned += 1

            # Simple missed detection: days with planned non-rest workouts but no activities
            day_names_ordered = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
            for i, day_name in enumerate(day_names_ordered):
                day_date = last_monday + timedelta(days=i)
                day_data = days_dict.get(day_name, {})
                workouts = day_data.get("workouts", [])
                has_planned = any(w.get("sport", "").lower() != "rest" for w in workouts)

                if has_planned:
                    # Check if any activity exists on this date
                    day_acts = [a for a in activities
                                if a.start_time and a.start_time.date() == day_date]
                    if not day_acts:
                        # Find the sport that was planned
                        planned_sport = workouts[0].get("sport", "workout") if workouts else "workout"
                        missed.append(f"{day_name} {planned_sport}")

        total_duration = sum((a.duration_sec or 0) for a in activities) / 3600
        total_load = sum((a.training_load or 0) for a in activities)

        # Sport breakdown
        sport_breakdown = {}
        for a in activities:
            sport = a.sport or "other"
            sport_breakdown[sport] = sport_breakdown.get(sport, 0) + 1

        # Find longest run
        runs = [a for a in activities if a.sport == "running"]
        long_run_km = max((a.distance_m or 0) / 1000 for a in runs) if runs else 0

        # None, not 0: "no plan to compare against" must never read as
        # "athlete missed everything" (false detraining signal in prompts).
        # A planned week with zero activities does read as 0 — that one is real.
        compliance_pct = None
        if sessions_planned > 0:
            compliance_pct = min(100, round((len(activities) / sessions_planned) * 100))

        summary = {
            "sessions_completed": len(activities),
            "sessions_planned": sessions_planned,
            "hours_done": round(total_duration, 1),
            "total_load": round(total_load),
            "long_run_km": round(long_run_km, 1),
            "missed": missed,
            "compliance_pct": compliance_pct,
            "sport_breakdown": sport_breakdown,
            "data_complete": history_complete,
        }
        if sessions_planned == 0:
            summary["note"] = (
                "No plan on record last week — compliance not measurable, "
                "do not treat as missed training."
            )
        elif not activities:
            summary["note"] = "No activities recorded last week."
        if not history_complete:
            start_txt = earliest.date().isoformat() if earliest else "empty"
            incomplete_note = (
                f"Activity history may be incomplete (local DB starts {start_txt}) — "
                "treat low totals as missing data, not zero training."
            )
            summary["note"] = (
                f"{summary['note']} {incomplete_note}" if summary.get("note")
                else incomplete_note
            )
        return summary

    def _empty_context(self, reason: str) -> dict:
        """Return a minimal context when data is unavailable."""
        return {
            "current_date": get_local_today().isoformat(),
            "phase": "foundation",
            "phase_name": "Phase 1: Foundation",
            "phase_priorities": "Build consistency and aerobic base",
            "weeks_to_race": None,
            "recovery": {"status": "unknown", "detail": reason},
            "error": reason,
        }
