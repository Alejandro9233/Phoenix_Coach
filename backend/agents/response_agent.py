"""
Response Agent — Generates coaching recommendations using Ollama LLM + RAG knowledge.

The LLM is the COACH — it makes real coaching decisions:
- Which workouts to assign to which days
- How to sequence the week (hard/easy alternation)
- How to handle missed sessions and adapt
- Post-workout analysis and feedback
- Weekly review and continuity

The PeriodizationEngine provides the CONTEXT — phase, volume references,
workout menu, recovery status. The LLM decides within those guardrails.
"""
import json
from datetime import datetime

from backend.core.llm_client import chat_completion

from backend.core.knowledge_base import KnowledgeBase

# Structured output runs cold. 0.3 cuts run-to-run variance in workout
# selection and schema adherence; the fixed seed lets the provider's
# best-effort reproducibility do what it can (see llm_client docstring for
# what that does NOT guarantee). Free-form chat stays at 0.7 — not here.
PLAN_TEMPERATURE = 0.3
PLAN_SEED = 1042


def _availability_text(value) -> str:
    """Render an availability day-list the way an LLM can actually obey.

    An empty string means the athlete disabled the sport. It used to render as
    a blank after a colon ("Swimming ONLY on: "), which reads as *no*
    restriction — the LLM scheduled a swim, the enforcer stripped it, and the
    day shipped thin (2026-08-18 Tuesday). The enforcer stays the hard gate;
    this just stops the LLM from spending a session slot on a doomed workout.
    """
    if value is None:
        return "any day"
    days = [d.strip() for d in value.split(",") if d.strip()]
    if not days:
        return "NEVER — the athlete has disabled this sport, do not schedule it"
    return f"ONLY on {', '.join(days)} — all other days are forbidden"


def build_constraint_block(ctx: dict) -> str:
    """The single source of constraint prose for BOTH generation prompts.

    Full-plan and partial-plan generation must state identical constraints —
    the partial prompt used to omit travel and the protected-run-km rule, so
    every mid-week rebuild (replan, injury, travel, recovery) lost the two
    most important lines. test_context_injection.py pins the two prompts to
    this block by string equality; extend it here, never inline.

    travel_day_names arrives via compute_context's availability, so travel
    coverage is automatic for every caller — no per-path plumbing.

    The VOLUME BUDGET line states the exact numbers the volume gate will
    grade the plan on (volume_gate.compute_budget — same source, so prompt
    and gate can never disagree). Omitted per-axis when a number is missing.
    """
    from backend.services.volume_gate import compute_budget

    avail = (ctx or {}).get("availability") or {}
    travel = ", ".join(avail.get("travel_day_names") or []) or "none this week"

    budget = compute_budget(ctx or {}) or {}
    budget_bits = []
    if budget.get("run_km_target") is not None:
        cap = budget.get("run_km_hard_cap")
        cap_txt = f" (hard cap {cap} km)" if cap is not None else ""
        budget_bits.append(f"running {budget['run_km_target']} km this week{cap_txt}")
    if budget.get("hours_low") is not None:
        budget_bits.append(
            f"{budget['hours_low']:g}-{budget['hours_high']:g} hours excluding strength"
        )
    if budget.get("max_quality") is not None:
        budget_bits.append(f"at most {budget['max_quality']} quality (hard) sessions")
    budget_line = (
        f"\n- VOLUME BUDGET (checked by the system): {'; '.join(budget_bits)}."
        if budget_bits else ""
    )

    return f"""CONSTRAINTS YOU MUST RESPECT (violations are removed by the system after you answer):
- Swimming: {_availability_text(avail.get('swim_days'))}
- Cycling: {_availability_text(avail.get('bike_days'))}
- Running: {_availability_text(avail.get('run_days'))}
- Strength: {_availability_text(avail.get('strength_days'))}
- Traveling (MUST be rest days, no training of any kind): {travel}
- Weekly RUN kilometers are the protected quantity. If days are unavailable, move run sessions (especially the long run) to open days and drop strength or cycling instead. Never delete the long run to keep a strength session.{budget_line}
- Copy workout titles VERBATIM from the AVAILABLE WORKOUTS menu — an off-menu title will be rejected.
- Every running/cycling/swimming workout MUST include "distance_km" (a number).
- For strength workouts, you MUST include a "muscle_groups" array selecting from: ["chest", "shoulders", "back", "legs", "arms"]."""


def _format_training_context(ctx: dict) -> str:
    """Format the TrainingContext dict as human-readable text for the LLM prompt."""
    lines = []

    # Race & timeline
    lines.append(f"Today: {ctx.get('current_date', 'unknown')}")
    if ctx.get("race_name") and ctx.get("race_date"):
        lines.append(f"Goal Race: {ctx['race_name']} ({ctx.get('race_distance', '')}) on {ctx['race_date']}")
        lines.append(f"Weeks to race: {ctx.get('weeks_to_race', '?')}")
        if ctx.get("race_goals", {}).get("target_finish_time"):
            lines.append(f"Target Finish Time: {ctx['race_goals']['target_finish_time']}")

    # Phase
    lines.append(f"\nTraining Phase: {ctx.get('phase_name', 'Unknown')}")
    lines.append(f"Phase week: {ctx.get('phase_week', '?')} of {ctx.get('phase_total_weeks', '?')}")
    lines.append(f"Phase priorities: {ctx.get('phase_priorities', 'N/A')}")

    # Build/Recovery cycle
    lines.append(f"\nCycle: {ctx.get('recovery_note', 'Unknown')}")
    if ctx.get("is_recovery_week"):
        lines.append("⚠️ THIS IS A RECOVERY WEEK — reduce all volumes 20-25%")

    # Tune-up race. The race is prompted, never Python-injected — the LLM
    # schedules it like any other session; the volume target above is already
    # scaled for the race week.
    tu = ctx.get("tuneup")
    if tu:
        if tu.get("is_race_week"):
            target = f" Goal: {tu['target']}." if tu.get("target") else ""
            lines.append(
                f"\n🏁 TUNE-UP RACE WEEK: {tu['label']} on {tu['race_day_name']} "
                f"{tu['race_date']}.{target} Schedule the race as that day's only "
                f"workout — it replaces the week's long run. Days before it: "
                f"short, easy, tapering. Day after: recovery or rest."
            )
        else:
            lines.append(
                f"\nTune-up race ahead: {tu['label']} on {tu['race_date']} "
                f"({tu['days_away']} days away) — a fitness test, not the goal "
                f"race. Train through it normally until race week."
            )

    # Volume — the computed run target when the engine produced one (running
    # races), phase-range prose otherwise. The target numbers are enforced
    # post-generation; the LLM is told exactly what it will be graded on.
    vol = ctx.get("volume_references", {})
    vt = ctx.get("volume_targets")
    if vt:
        lines.append("\nTHIS WEEK'S RUN VOLUME (computed — the week's running MUST total within 10% of this):")
        lines.append(f"  Run km target: {vt['run_km_target']} km"
                     f" (hard cap {vt['run_km_hard_cap']} km — the system trims anything above)")
        if vt.get("long_run_minutes"):
            lines.append(f"  Long run: ~{vt['long_run_minutes']} min")
        lines.append(f"  Derivation: {vt['basis']}")
        lines.append(f"\nVolume References:")
    else:
        lines.append(f"\nVolume References:")
        run_note = vol.get("sport_sessions", {}).get("running", {}).get("volume_note")
        if run_note:
            lines.append(f"  Running: {run_note}")
    lines.append(f"  Phase recommended hours: {vol.get('phase_hours_range', '?')}h/week")
    if vol.get("coros_tl_range"):
        lines.append(f"  COROS recommended training load: {vol['coros_tl_range']['min']}-{vol['coros_tl_range']['max']} (from watch)")
    lines.append(f"  Intensity distribution: {vol.get('intensity_split', '80/20')}")
    lines.append(f"  Max quality (hard) sessions: {vol.get('max_quality_sessions', 2)}")

    if vol.get("recovery_week_adjustment"):
        lines.append(f"  ⚠️ {vol['recovery_week_adjustment']}")

    # Python-computed run paces (pace_model.py). The LLM copies these; it
    # never invents a pace. pace_enforcer stamps pace_target after
    # generation regardless, so drift here self-heals.
    pm = ctx.get("pace_model")
    if pm:
        lines.append("\nRUN PACES (computed from the watch's lactate threshold — use EXACTLY these):")
        for key, name in (("recovery", "Recovery"), ("easy", "Easy"),
                          ("long_run", "Long run"), ("marathon", "Marathon pace"),
                          ("tempo", "Tempo"), ("interval", "Interval")):
            band = (pm.get("bands") or {}).get(key)
            if band:
                lines.append(f"  {name}: {band['label']}")
        lines.append("  Every running workout MUST include a \"pace_target\" field "
                     "copied verbatim from the matching line. Do not write numeric "
                     "paces in prose; the pace_target field is the authority.")
        if pm.get("note"):
            lines.append(f"  ⚠️ {pm['note']}")

    # Sport sessions reference
    sport_sessions = vol.get("sport_sessions", {})
    if sport_sessions:
        lines.append(f"\nSport Session References:")
        for sport, info in sport_sessions.items():
            lines.append(f"  {sport.capitalize()}: {info.get('sessions', '?')}x/week — {info.get('volume_note', '')}")

    # Recovery status
    rec = ctx.get("recovery", {})
    status_emoji = {"green": "🟢", "yellow": "🟡", "red": "🔴"}.get(rec.get("status"), "⚪")
    lines.append(f"\nRecovery Status: {status_emoji} {rec.get('status', 'unknown').upper()}")
    lines.append(f"  {rec.get('detail', 'No data')}")
    if rec.get("hrv_vs_baseline") and rec["hrv_vs_baseline"] != "unknown":
        lines.append(f"  HRV vs baseline: {rec['hrv_vs_baseline']}")
    if rec.get("tib") is not None:
        lines.append(f"  TIB (form): {rec['tib']}")
    if rec.get("load_ratio") is not None:
        lines.append(f"  Load ratio: {rec['load_ratio']}")

    # Last week. Rendered when there is anything to say — including a bare
    # warning note: an incomplete-history caveat that never reaches the
    # prompt is how a hollow week reads as detraining.
    lw = ctx.get("last_week", {})
    if lw.get("sessions_completed", 0) > 0 or lw.get("note"):
        lines.append(f"\nLast Week Summary:")
        lines.append(f"  Sessions: {lw.get('sessions_completed', 0)} completed" +
                     (f" / {lw['sessions_planned']} planned" if lw.get('sessions_planned') else ""))
        if lw.get("compliance_pct") is None:
            lines.append("  Compliance: n/a — no plan was on record last week "
                         "(NOT non-compliance; do not treat as missed training)")
        else:
            lines.append(f"  Compliance: {lw['compliance_pct']}%")
        if lw.get("note"):
            lines.append(f"  Note: {lw['note']}")
        lines.append(f"  Hours: {lw.get('hours_done', 0)}h | Training Load: {lw.get('total_load', 0)}")
        if lw.get("long_run_km"):
            lines.append(f"  Longest run: {lw['long_run_km']} km")
        if lw.get("missed"):
            lines.append(f"  ❌ Missed: {', '.join(lw['missed'])}")
        if lw.get("sport_breakdown"):
            breakdown = ", ".join(f"{s}: {c}" for s, c in lw["sport_breakdown"].items())
            lines.append(f"  Sports: {breakdown}")

    # Availability
    avail = ctx.get("availability", {})
    if avail:
        lines.append(f"\nSport Availability:")
        lines.append(f"  Swimming: {_availability_text(avail.get('swim_days'))}")
        lines.append(f"  Cycling: {_availability_text(avail.get('bike_days'))}")
        lines.append(f"  Running: {_availability_text(avail.get('run_days'))}")
        lines.append(f"  Strength: {_availability_text(avail.get('strength_days'))}")
        if avail.get("travel_day_names"):
            lines.append(
                f"  TRAVELING (no training possible, plan rest): "
                f"{', '.join(avail['travel_day_names'])}"
            )

    return "\n".join(lines)


def _format_workout_menu(ctx: dict) -> str:
    """Format the workout menu as a readable list for the prompt."""
    menu = ctx.get("workout_menu", {})
    forbidden = ctx.get("forbidden_workouts", [])
    reason = ctx.get("forbidden_reason", "")

    lines = ["AVAILABLE WORKOUTS (select from these ONLY):"]
    for sport, workouts in menu.items():
        if not workouts:
            lines.append(f"  {sport.capitalize()}: None available this phase")
        elif isinstance(workouts, dict):
            # Detailed dictionary format (e.g. for strength)
            w_types = workouts.get("types", [])
            w_groups = workouts.get("available_muscle_groups", [])
            w_note = workouts.get("note", "")
            details = f"{', '.join(w_types)} | Targeted groups: {', '.join(w_groups)}"
            if w_note:
                details += f" ({w_note})"
            lines.append(f"  {sport.capitalize()}: {details}")
        else:
            lines.append(f"  {sport.capitalize()}: {', '.join(workouts)}")

    if forbidden:
        lines.append(f"\n🚫 FORBIDDEN this phase: {', '.join(forbidden)}")
        lines.append(f"   Reason: {reason}")

    return "\n".join(lines)


# ─── System Prompt ────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """You are Phoenix, an elite endurance coach. You are coaching a single athlete and your job is to analyze their current state and recommend today's training session.

You adapt your coaching approach based on the athlete's race type and distance. For running events (5k, 10k, Half Marathon, Marathon), prioritize running-specific training. For triathlon events (Sprint, Olympic, 70.3, Ironman), balance all three disciplines (swim, bike, run) plus strength.

RULES:
1. Always base your decisions on the athlete's data and the coaching principles provided
2. Never prescribe high-intensity work when the athlete shows signs of fatigue (negative TIB, elevated RHR, low HRV)
3. Follow the 80/20 rule: most training should be easy (Zone 1-2)
4. Never schedule 3 consecutive hard days
5. If load ratio is >1.5, prescribe only recovery
6. Be concise and direct — the athlete wants clear instructions, not essays

OUTPUT FORMAT:
You MUST respond with valid JSON in this exact format:
{
  "summary": "One-sentence overview of today's recommendation",
  "workouts": [
    {
      "sport": "running|cycling|swimming|strength|rest",
      "title": "Short workout title",
      "steps": [
        {"type": "warmup|main|recovery|cooldown", "duration": "MM:SS", "zone": 1, "description": "Brief instruction"}
      ],
      "total_time": "XX min",
      "hr_target": "XXX-XXX bpm"
    }
  ],
  "rationale": "2-3 sentences explaining why this workout based on the athlete's data",
  "adaptation": "null or a string explaining what was changed from the normal plan and why",
  "coach_note": "Optional motivational or tactical tip"
}

If prescribing a REST day, set sport to "rest" and steps to an empty array for the single workout in the array.
"""


class ResponseAgent:
    def __init__(self):
        self.kb = KnowledgeBase.get_instance()

    def _chat_json_with_retry(self, messages: list[dict],
                              temperature: float = PLAN_TEMPERATURE,
                              seed: int | None = PLAN_SEED) -> dict:
        """chat_completion in JSON mode, with one retry on malformed JSON.

        Two failure shapes retry once, both meaning "the model fumbled the
        JSON, a resample is cheap": a local decode error, and Groq's
        server-side json_validate_failed 400 (its json_object validator
        rejecting the output — seen 2026-08-24 when reasoning starved the
        completion budget). Every other API error (dead model, auth, rate
        limit) propagates immediately so the caller can fail the request.
        """
        def _is_json_validate_failed(exc: Exception) -> bool:
            return "json_validate_failed" in str(exc)

        try:
            content = chat_completion(messages=messages, json_mode=True,
                                      temperature=temperature, seed=seed)
        except Exception as e:
            if not _is_json_validate_failed(e):
                raise
            print(f"Groq rejected plan JSON ({e}); retrying once...")
            content = chat_completion(messages=messages, json_mode=True,
                                      temperature=temperature, seed=seed)
            return json.loads(content)
        try:
            return json.loads(content)
        except json.JSONDecodeError as e:
            print(f"Malformed plan JSON ({e}); retrying once...")
            content = chat_completion(messages=messages, json_mode=True,
                                      temperature=temperature, seed=seed)
            return json.loads(content)

    def generate_recommendation(self, athlete_summary: str) -> dict:
        """
        Generate a coaching recommendation based on the athlete's current state.
        """
        # 1. Retrieve relevant coaching knowledge via RAG
        rag_chunks = self.kb.query(athlete_summary, n_results=3)
        rag_context = "\n\n---\n\n".join(rag_chunks) if rag_chunks else "No coaching knowledge available."

        # 2. Build the user prompt
        from backend.utils.timezone import get_local_now
        today = get_local_now().strftime("%A, %B %d, %Y")
        user_prompt = f"""Today is {today}.

Here is the athlete's current state:

{athlete_summary}

Here are relevant coaching principles to apply:

{rag_context}

Based on this data and these principles, what should the athlete do today? Remember to output valid JSON only."""

        # 3. Call LLM Client
        try:
            return self._chat_json_with_retry(
                [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt}
                ],
                temperature=PLAN_TEMPERATURE, seed=None,
            )
        except json.JSONDecodeError as e:
            print(f"Failed to parse LLM JSON: {e}")
            return self._fallback_recommendation(athlete_summary)
        except Exception as e:
            print(f"Ollama error: {e}")
            return self._fallback_recommendation(athlete_summary)

    def analyze_activity(self, activity_data: dict, planned_workout: dict = None,
                         compliance: dict = None, training_context: dict = None) -> dict:
        """
        Analyze a specific activity and provide coach feedback.
        Enhanced with plan context and phase awareness when available.
        """
        # Build the analysis prompt
        prompt_parts = ["Please analyze this specific training session and provide a 'Coach's Take'."]

        # Add phase context if available
        if training_context:
            phase = training_context.get("phase_name", "")
            priorities = training_context.get("phase_priorities", "")
            prompt_parts.append(f"\nTRAINING PHASE: {phase}")
            prompt_parts.append(f"Phase priorities: {priorities}")

        prompt_parts.append(f"\nACTUAL ACTIVITY DATA:\n{json.dumps(activity_data, indent=2)}")

        # Add planned vs actual if available
        if planned_workout:
            prompt_parts.append(f"\nPLANNED WORKOUT:\n{json.dumps(planned_workout, indent=2)}")
            prompt_parts.append("Compare what was planned vs what was executed.")

        if compliance:
            prompt_parts.append(f"\nCOMPLIANCE DATA:\n{json.dumps(compliance, indent=2)}")

        prompt_parts.append("""
Provide a short, 2-3 sentence analysis focusing on:
1. Was the effort appropriate for the training phase and plan?
2. What went well or needs adjustment?
3. One tactical tip for next time.

Respond in valid JSON:
{
  "analysis": "Your 2-3 sentence analysis here",
  "rating": "A-F grade for session execution",
  "advice": "Short tactical tip"
}""")

        user_prompt = "\n".join(prompt_parts)

        system = "You are Phoenix, an elite triathlon coach analyzing a specific training file."
        if training_context:
            phase = training_context.get("phase_name", "")
            system += f" The athlete is in {phase}."

        try:
            return self._chat_json_with_retry(
                [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user_prompt}
                ],
                temperature=PLAN_TEMPERATURE, seed=None,
            )
        except Exception as e:
            print(f"Error analyzing activity: {e}")
            return {"analysis": f"Could not analyze activity: {str(e)}", "rating": "Error", "advice": "Try again later."}

    def generate_weekly_plan(self, athlete_summary: str, profile: dict,
                             training_context: dict = None,
                             feedback: str = None) -> dict:
        """
        Generate a full 7-day training plan.

        When training_context is provided (from PeriodizationEngine), the LLM gets:
        - Exact phase, weeks to race, build/recovery cycle
        - Workout menu (allowed + forbidden per phase)
        - Volume references (phase hours, COROS TL range, intensity split)
        - Recovery status and last week's summary
        - Sport availability constraints

        The LLM decides: which workouts, what sequence, how to handle gaps, coaching notes.
        """
        if training_context:
            return self._generate_plan_with_context(
                athlete_summary, profile, training_context, feedback=feedback
            )
        else:
            return self._generate_plan_legacy(athlete_summary, profile)

    def _generate_plan_with_context(self, athlete_summary: str, profile: dict,
                                     ctx: dict, feedback: str = None) -> dict:
        """Phase-aware weekly plan generation."""
        # Format the training context as readable text
        context_text = _format_training_context(ctx)
        menu_text = _format_workout_menu(ctx)

        # Query RAG with phase-specific terms for better knowledge retrieval
        phase_name = ctx.get("phase_name", "Foundation")
        priorities = ctx.get("phase_priorities", "base building")
        rag_query = f"{phase_name} {priorities} weekly training plan"
        rag_chunks = self.kb.query(rag_query, n_results=3)
        rag_context = "\n\n---\n\n".join(rag_chunks) if rag_chunks else ""

        # Build the system prompt with long-term journey context
        weeks = ctx.get("weeks_to_race", "?")
        race = ctx.get("race_name", "the race")
        race_type = ctx.get("race_type", "Triathlon")
        race_dist = ctx.get("race_distance", "Marathon")
        coach_style = "running" if race_type == "Running" else "triathlon"
        system = f"""You are Phoenix, an elite {coach_style} coach. You are coaching an athlete toward {race} ({race_dist}) in {weeks} weeks.

You are generating a 7-day training plan (Monday to Sunday). You make the COACHING DECISIONS:
- Which workouts from the available menu to assign to which days
- How to sequence the week intelligently (hard/easy alternation, double days)
- How to address last week's results (missed sessions, compliance gaps)
- Step-by-step details for each workout (warmup, main set, cooldown)
- Coaching rationale for each day's prescription
- Motivational notes that reference the athlete's journey

You are a real coach, not a template. Make decisions based on the athlete's data, recovery status, and what happened last week.

IMPORTANT: Only prescribe workouts from the AVAILABLE WORKOUTS list. Do NOT prescribe anything from the FORBIDDEN list."""

        # Build the user prompt
        prompt = f"""=== TRAINING CONTEXT ===
{context_text}

=== COACHING KNOWLEDGE ===
{rag_context}

=== {menu_text} ===

=== ATHLETE CURRENT STATE ===
{athlete_summary}

=== YOUR COACHING TASK ===
Design this week's 7-day plan (Monday to Sunday). You decide:
1. Which workouts from the available menu to schedule on which days
2. How to sequence them (don't put 2 hard days back-to-back)
3. Whether to schedule double-workout days (e.g., morning swim + evening strength)
4. How to address any missed sessions or gaps from last week
5. Include 1-2 rest or active recovery days
6. Write step-by-step details for each workout
7. For STRENGTH workouts: decide the split (Push/Pull/Legs, Upper/Lower, etc.) and include the targeted muscle groups.

{build_constraint_block(ctx)}
{(chr(10) + feedback + chr(10)) if feedback else ""}
OUTPUT FORMAT — respond ONLY with valid JSON matching the exact schema below.
CRITICAL: You MUST use the exact keys "week_summary" and "days". Do NOT output a flat list under keys like "workout_plan" or "schedule". The "days" dictionary MUST contain the keys "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday", and each day MUST have "summary", "workouts" (an array), "rationale", and "coach_note".

{{
  "week_summary": {{
    "focus": "e.g., Foundation Base Building, Recovery Week",
    "rationale": "Why this week is designed this way — reference the phase and athlete's situation",
    "expected_total_hours": 8.0,
    "expected_run_km": 25.0
  }},
  "days": {{
    "Monday": {{
      "summary": "Brief overview of the day",
      "workouts": [
        {{
          "sport": "running|cycling|swimming|strength|rest",
          "title": "Workout Title",
          "steps": [
            {{"type": "warmup|main|recovery|cooldown", "duration": "MM:SS", "zone": 1, "description": "Brief instruction"}}
          ],
          "total_time": "XX min",
          "hr_target": "XXX-XXX bpm",
          "distance_km": 10.0,
          "pace_target": "4:26-4:34/km",
          "muscle_groups": ["chest", "shoulders", "back", "legs", "arms"]
        }}
      ],
      "rationale": "Why these specific workouts today",
      "coach_note": "Coaching tip or motivation"
    }},
    "Tuesday": {{ ... }},
    "Wednesday": {{ ... }},
    "Thursday": {{ ... }},
    "Friday": {{ ... }},
    "Saturday": {{ ... }},
    "Sunday": {{ ... }}
  }}
}}
Respond ONLY with the JSON block. Do not write introductory or concluding conversational text.
"""

        # A failed generation must raise — the caller persists whatever this
        # returns, and 2026-08-17 proved a silent fallback here becomes the
        # athlete's actual week (a template plan shipped as a Marathon Base
        # recovery week). Same rule as generate_remaining_days.
        try:
            return self._chat_json_with_retry([
                {"role": "system", "content": system},
                {"role": "user", "content": prompt}
            ])
        except Exception as e:
            print(f"Error generating weekly plan: {e}")
            raise

    def _generate_plan_legacy(self, athlete_summary: str, profile: dict) -> dict:
        """Legacy plan generation without training context (backward compatible)."""
        prompt = f"""You are Phoenix, an elite triathlon coach. You are generating a 7-day training plan (Monday to Sunday) for your athlete.

ATHLETE CURRENT STATE:
{athlete_summary}

ATHLETE CONSTRAINTS & OBJECTIVES:
- Race: {profile.get('race_name') if profile.get('race_name') is not None else 'Not set'} ({profile.get('race_distance') if profile.get('race_distance') is not None else 'Not set'}) on {profile.get('race_date') if profile.get('race_date') is not None else 'Not set'}
- Swim availability: {_availability_text(profile.get('swim_days', 'wed,sat,sun'))}
- Bike availability: {_availability_text(profile.get('bike_days'))}
- Run availability: {_availability_text(profile.get('run_days'))}
- Strength availability: {_availability_text(profile.get('strength_days', 'mon,wed,fri'))}

COACHING RULES:
1. Respect the sport availability constraints: do NOT schedule a swim, bike, run, or strength session on a day not listed in the athlete's availability.
2. Follow the 80/20 intensity rule: mostly Zone 1-2 easy aerobic base training.
3. Include at least 1-2 rest days depending on fatigue.
4. Scale total session time to fit the phase recommended hours range.
5. If the race is approaching or is this week, apply tapering principles (short duration, light intensity, lots of rest).
6. You may schedule double-workout days (e.g. morning swim, evening strength) if it makes sense to hit the recommended hours without violating the 80/20 rule or causing extreme fatigue. Let the recommended hours and availability guide this decision.

OUTPUT FORMAT:
You MUST respond with valid JSON in this exact structure:
{{
  "week_summary": {{
    "focus": "e.g., Base Building, Tapering",
    "rationale": "Why this week is designed this way based on the athlete's phase",
    "expected_total_hours": 8.5,
    "expected_run_km": 35.0
  }},
  "days": {{
    "Monday": {{
      "summary": "Brief overview",
      "workouts": [
        {{
          "sport": "running|cycling|swimming|strength|rest",
          "title": "Workout Title",
          "steps": [
            {{"type": "warmup|main|recovery|cooldown", "duration": "MM:SS", "zone": 1, "description": "Brief description"}}
          ],
          "total_time": "XX min",
          "hr_target": "XXX-XXX bpm"
        }}
      ],
      "rationale": "Why these workouts were chosen",
      "coach_note": "Motivational tip"
    }},
    "Tuesday": {{ ... }},
    "Wednesday": {{ ... }},
    "Thursday": {{ ... }},
    "Friday": {{ ... }},
    "Saturday": {{ ... }},
    "Sunday": {{ ... }}
  }}
}}
Respond ONLY with the JSON block. Do not write introductory or concluding conversational text.
"""
        try:
            return self._chat_json_with_retry([
                {"role": "system", "content": "You are a professional triathlon coach that generates structured training plans in JSON format."},
                {"role": "user", "content": prompt}
            ])
        except Exception as e:
            print(f"Error generating weekly plan: {e}")
            raise

    def adapt_daily(self, planned_workout: dict, today_metrics: str,
                    training_context: dict = None) -> dict:
        """
        Adapt today's planned workout based on fresh recovery metrics.
        Enhanced with phase context and surrounding-day awareness.
        """
        # Build context section
        context_section = ""
        if training_context:
            rec = training_context.get("recovery", {})
            phase = training_context.get("phase_name", "")
            context_section = f"""
TRAINING PHASE: {phase}
Phase priorities: {training_context.get('phase_priorities', '')}
Cycle: {training_context.get('recovery_note', '')}

Recovery Status: {rec.get('status', 'unknown').upper()}
{rec.get('detail', '')}
HRV vs baseline: {rec.get('hrv_vs_baseline', 'unknown')}
TIB (form): {rec.get('tib', 'N/A')}
Load ratio: {rec.get('load_ratio', 'N/A')}
"""
            avail = training_context.get("availability", {})
            if avail:
                context_section += f"""
SPORT AVAILABILITY (hard rule — a workout in a disallowed sport/day is removed after you answer):
  Swimming: {_availability_text(avail.get('swim_days'))}
  Cycling: {_availability_text(avail.get('bike_days'))}
  Running: {_availability_text(avail.get('run_days'))}
  Strength: {_availability_text(avail.get('strength_days'))}
"""

        prompt = f"""You are Phoenix, an elite triathlon coach. You need to review today's PLANNED workout and decide if it needs to be adapted based on the athlete's actual RECOVERY metrics today.
{context_section}
PLANNED WORKOUT FOR TODAY:
{json.dumps(planned_workout, indent=2)}

TODAY'S ACTUAL RECOVERY METRICS:
{today_metrics}

RULES FOR ADAPTATION:
1. If the athlete's metrics show severe fatigue (extremely low HRV, elevated RHR, negative TIB, or high Load Ratio > 1.4), you MUST downgrade the session to "rest" or "active recovery" (a very easy Zone 1 session under 20 min, in a sport allowed today).
2. If they have minor fatigue, you may reduce the duration or intensity of the main set.
3. If they are well-recovered (normal/high HRV, stable RHR, positive TIB), keep the planned workout exactly as is.
4. If you adapt the session, set the "adaptation" field to explain exactly why and what was changed. If no change is made, set "adaptation" to null.

OUTPUT FORMAT:
You MUST respond with valid JSON in this exact format:
{{
  "summary": "One-sentence overview of today's recommendation",
  "workouts": [
    {{
      "sport": "running|cycling|swimming|strength|rest",
      "title": "Workout Title",
      "steps": [
        {{"type": "warmup|main|recovery|cooldown", "duration": "MM:SS", "zone": 1, "description": "Brief description"}}
      ],
      "total_time": "XX min",
      "hr_target": "XXX-XXX bpm"
    }}
  ],
  "rationale": "2-3 sentences explaining your coaching logic",
  "adaptation": "Explanation of change or null",
  "coach_note": "Motivational tip"
}}
Respond ONLY with the JSON block. Do not write introductory or concluding conversational text.
"""
        try:
            return self._chat_json_with_retry(
                [
                    {"role": "system", "content": "You are a professional triathlon coach that adapts training sessions based on recovery data in JSON format."},
                    {"role": "user", "content": prompt}
                ],
                temperature=PLAN_TEMPERATURE, seed=None,
            )
        except Exception as e:
            print(f"Error adapting workout: {e}")
            adapted = planned_workout.copy()
            adapted["adaptation"] = None
            return adapted

    def generate_remaining_days(self, athlete_summary: str, profile: dict,
                                 training_context: dict,
                                 completed_days_summary: str,
                                 days_to_plan: list[str],
                                 reason: str = "You are replanning the remaining days of an in-progress week.",
                                 feedback: str = None) -> dict:
        """
        Generate a partial weekly plan for only the remaining days.

        Receives a summary of what the athlete already completed this week
        and generates only the specified remaining days, ensuring proper
        volume balancing and no missed key sessions.

        `reason` tells the LLM WHY it is replanning (mid-week replan, injury,
        travel, recovery). The old hardcoded line claimed "an earlier system
        error corrupted some days" for every caller — a lie that skewed the
        coaching tone on every rebuild.
        """
        context_text = _format_training_context(training_context)
        menu_text = _format_workout_menu(training_context)

        # RAG context
        phase_name = training_context.get("phase_name", "Foundation")
        priorities = training_context.get("phase_priorities", "base building")
        rag_query = f"{phase_name} {priorities} weekly training plan"
        rag_chunks = self.kb.query(rag_query, n_results=3)
        rag_context = "\n\n---\n\n".join(rag_chunks) if rag_chunks else ""

        weeks = training_context.get("weeks_to_race", "?")
        race = training_context.get("race_name", "the race")
        race_type = training_context.get("race_type", "Triathlon")
        race_dist = training_context.get("race_distance", "Marathon")
        coach_style = "running" if race_type == "Running" else "triathlon"

        days_list = ", ".join(days_to_plan)
        days_json_template = "\n".join([f'    "{d}": {{ ... }}' for d in days_to_plan])

        system = f"""You are Phoenix, an elite {coach_style} coach. You are coaching an athlete toward {race} ({race_dist}) in {weeks} weeks.

You are generating a PARTIAL training plan to fill in the remaining days of the current week.
The athlete has already completed some training this week. You MUST account for the volume, 
load, and stimulus already delivered when planning the remaining days.

IMPORTANT: Only prescribe workouts from the AVAILABLE WORKOUTS list. Do NOT prescribe anything from the FORBIDDEN list.
IMPORTANT: {reason} Make sure key sessions (tempo runs, long runs, quality swim/bike sessions) the week still needs are included."""

        prompt = f"""=== TRAINING CONTEXT ===
{context_text}

=== COACHING KNOWLEDGE ===
{rag_context}

=== {menu_text} ===

=== ATHLETE CURRENT STATE ===
{athlete_summary}

=== WORK ALREADY COMPLETED THIS WEEK ===
{completed_days_summary}

=== YOUR COACHING TASK ===
Generate training plans for ONLY these remaining days: {days_list}

Account for the training already done. You must:
1. Balance the remaining volume to hit the weekly target without overloading
2. Include key quality sessions that may have been missed earlier in the week
3. Follow hard/easy alternation relative to what was already done
4. Use only workouts from the available menu
5. For STRENGTH workouts: include the "muscle_groups" array

{build_constraint_block(training_context)}
{(chr(10) + feedback + chr(10)) if feedback else ""}
OUTPUT FORMAT — respond ONLY with valid JSON containing ONLY the days listed above:
{{
  "days": {{
{days_json_template}
  }}
}}

Each day must have: "summary", "workouts" (array), "rationale", "coach_note".
Each workout: "sport", "title", "steps" (array), "total_time", "hr_target", "distance_km" (for running/cycling/swimming), "pace_target" (for running, copied from RUN PACES), "muscle_groups" (for strength).
Each step: "type" (warmup|main|recovery|cooldown), "duration" (MM:SS), "zone" (1-5), "description".

Respond ONLY with the JSON block. Do not write introductory or concluding conversational text.
"""

        try:
            return self._chat_json_with_retry([
                {"role": "system", "content": system},
                {"role": "user", "content": prompt}
            ])
        except Exception as e:
            # No placeholder fallback here: the caller persists whatever this
            # returns, and on 2026-08-17 a retired Groq model turned that into
            # "Error generating plan" overwriting all 7 days of a real week.
            # A failed replan must fail the request and leave the plan alone.
            print(f"Error generating remaining days plan: {e}")
            raise

    def generate_weekly_review(self, compliance_data: dict, training_context: dict) -> dict:
        """
        Generate a weekly review — end-of-week coaching analysis.
        Called when generating the next week's plan.
        """
        context_text = _format_training_context(training_context)

        prompt = f"""You are Phoenix, an elite triathlon coach reviewing your athlete's past week of training.

=== TRAINING CONTEXT ===
{context_text}

=== WEEK COMPLIANCE DATA ===
{json.dumps(compliance_data, indent=2)}

Based on this data, provide a weekly review. Be specific — reference actual numbers.

Respond in valid JSON:
{{
  "went_well": "What the athlete did well this week (specific, reference data)",
  "needs_attention": "What needs improvement or attention (specific)",
  "next_week_impact": "How this week's results should influence next week's plan",
  "motivation": "One sentence of genuine coaching encouragement",
  "grade": "A-F grade for the week overall"
}}"""

        try:
            return self._chat_json_with_retry(
                [
                    {"role": "system", "content": "You are an elite triathlon coach providing a weekly training review in JSON format."},
                    {"role": "user", "content": prompt}
                ],
                temperature=PLAN_TEMPERATURE, seed=None,
            )
        except Exception as e:
            print(f"Error generating weekly review: {e}")
            return {
                "went_well": "Unable to generate review.",
                "needs_attention": str(e),
                "next_week_impact": "Continue as planned.",
                "motivation": "Keep going!",
                "grade": "N/A"
            }

    # _fallback_weekly_plan is gone on purpose (2026-08-18). It let a dead
    # model ship a template week as if the coach had planned it. A failed
    # generation raises; the endpoints return 502 and persist nothing.

    def _fallback_recommendation(self, summary: str) -> dict:
        """Fallback recommendation when LLM is unavailable."""
        is_fatigued = "ALERT" in summary or "negative" in summary.lower()

        if is_fatigued:
            return {
                "summary": "Recovery day recommended — signs of fatigue detected.",
                "workouts": [{
                    "sport": "rest",
                    "title": "Active Recovery",
                    "steps": [
                        {"type": "main", "duration": "20:00", "zone": 1, "description": "Very easy walk or light stretching"}
                    ],
                    "total_time": "20 min",
                    "hr_target": "< 120 bpm"
                }],
                "rationale": "Fatigue signals detected in your data. Prioritizing recovery today to prevent overtraining.",
                "adaptation": "Reduced from normal training to recovery due to fatigue markers.",
                "coach_note": "Rest is training. Trust the process."
            }
        else:
            return {
                "summary": "Easy aerobic session — building your base.",
                "workouts": [{
                    "sport": "running",
                    "title": "Easy Aerobic Run",
                    "steps": [
                        {"type": "warmup", "duration": "05:00", "zone": 1, "description": "Walk to easy jog"},
                        {"type": "main", "duration": "30:00", "zone": 2, "description": "Easy conversational pace"},
                        {"type": "cooldown", "duration": "05:00", "zone": 1, "description": "Walk"}
                    ],
                    "total_time": "40 min",
                    "hr_target": "120-145 bpm"
                }],
                "rationale": "No concerning fatigue markers. Prescribing an easy aerobic session to maintain base fitness.",
                "adaptation": None,
                "coach_note": "Keep this easy — you should be able to hold a conversation the entire time."
            }


if __name__ == "__main__":
    import os, sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from backend.agents.data_agent import DataAgent

    engine = create_engine("sqlite:///./phoenix_coach.db")
    Session = sessionmaker(bind=engine)
    session = Session()

    # Get athlete summary
    data_agent = DataAgent(session)
    summary = data_agent.summarize()
    print("=== ATHLETE SUMMARY ===")
    print(summary)
    print()

    # Generate recommendation
    response_agent = ResponseAgent()
    rec = response_agent.generate_recommendation(summary)
    print("=== COACHING RECOMMENDATION ===")
    print(json.dumps(rec, indent=2))

    session.close()
