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

    # Volume references
    vol = ctx.get("volume_references", {})
    lines.append(f"\nVolume References:")
    if vol.get("weekly_hours_target"):
        lines.append(f"  Athlete's target hours: {vol['weekly_hours_target']}h/week")
    lines.append(f"  Phase recommended hours: {vol.get('phase_hours_range', '?')}h/week")
    if vol.get("coros_tl_range"):
        lines.append(f"  COROS recommended training load: {vol['coros_tl_range']['min']}-{vol['coros_tl_range']['max']} (from watch)")
    lines.append(f"  Intensity distribution: {vol.get('intensity_split', '80/20')}")
    lines.append(f"  Max quality (hard) sessions: {vol.get('max_quality_sessions', 2)}")

    if vol.get("recovery_week_adjustment"):
        lines.append(f"  ⚠️ {vol['recovery_week_adjustment']}")

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

    # Last week
    lw = ctx.get("last_week", {})
    if lw.get("sessions_completed", 0) > 0:
        lines.append(f"\nLast Week Summary:")
        lines.append(f"  Sessions: {lw.get('sessions_completed', 0)} completed" +
                     (f" / {lw['sessions_planned']} planned" if lw.get('sessions_planned') else ""))
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
        lines.append(f"  Swimming: {avail.get('swim_days', 'any')}")
        lines.append(f"  Cycling: {avail.get('bike_days', 'any')}")
        lines.append(f"  Running: {avail.get('run_days', 'any')}")
        lines.append(f"  Strength: {avail.get('strength_days', 'any')}")

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

    def generate_recommendation(self, athlete_summary: str) -> dict:
        """
        Generate a coaching recommendation based on the athlete's current state.
        """
        # 1. Retrieve relevant coaching knowledge via RAG
        rag_chunks = self.kb.query(athlete_summary, n_results=3)
        rag_context = "\n\n---\n\n".join(rag_chunks) if rag_chunks else "No coaching knowledge available."

        # 2. Build the user prompt
        today = datetime.now().strftime("%A, %B %d, %Y")
        user_prompt = f"""Today is {today}.

Here is the athlete's current state:

{athlete_summary}

Here are relevant coaching principles to apply:

{rag_context}

Based on this data and these principles, what should the athlete do today? Remember to output valid JSON only."""

        # 3. Call LLM Client
        try:
            content = chat_completion(
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt}
                ],
                json_mode=True
            )
            return json.loads(content)

        except json.JSONDecodeError as e:
            print(f"Failed to parse LLM JSON: {e}")
            print(f"Raw response: {content[:500]}")
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
            content = chat_completion(
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user_prompt}
                ],
                json_mode=True
            )
            return json.loads(content)
        except Exception as e:
            print(f"Error analyzing activity: {e}")
            return {"analysis": f"Could not analyze activity: {str(e)}", "rating": "Error", "advice": "Try again later."}

    def generate_weekly_plan(self, athlete_summary: str, profile: dict,
                             training_context: dict = None) -> dict:
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
            return self._generate_plan_with_context(athlete_summary, profile, training_context)
        else:
            return self._generate_plan_legacy(athlete_summary, profile)

    def _generate_plan_with_context(self, athlete_summary: str, profile: dict,
                                     ctx: dict) -> dict:
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

CONSTRAINTS YOU MUST RESPECT:
- Swimming ONLY on: {ctx.get('availability', {}).get('swim_days', 'any')}
- Cycling on: {ctx.get('availability', {}).get('bike_days', 'any')}
- Running on: {ctx.get('availability', {}).get('run_days', 'any')}
- Strength on: {ctx.get('availability', {}).get('strength_days', 'any')}
- For strength workouts, you MUST include a "muscle_groups" array selecting from: ["chest", "shoulders", "back", "legs", "arms"] (e.g., ["legs"] or ["chest", "shoulders", "arms"]).

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

        try:
            content = chat_completion(
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": prompt}
                ],
                json_mode=True
            )
            return json.loads(content)
        except Exception as e:
            print(f"Error generating weekly plan: {e}")
            return self._fallback_weekly_plan(profile)

    def _generate_plan_legacy(self, athlete_summary: str, profile: dict) -> dict:
        """Legacy plan generation without training context (backward compatible)."""
        prompt = f"""You are Phoenix, an elite triathlon coach. You are generating a 7-day training plan (Monday to Sunday) for your athlete.

ATHLETE CURRENT STATE:
{athlete_summary}

ATHLETE CONSTRAINTS & OBJECTIVES:
- Race: {profile.get('race_name') or 'Not set'} ({profile.get('race_distance') or 'Not set'}) on {profile.get('race_date') or 'Not set'}
- Weekly target hours: {profile.get('weekly_hours_target') or 8.0} hours
- Swim availability: {profile.get('swim_days') or 'wed,sat,sun'}
- Bike availability: {profile.get('bike_days') or 'all'}
- Run availability: {profile.get('run_days') or 'all'}
- Strength availability: {profile.get('strength_days') or 'mon,wed,fri'}

COACHING RULES:
1. Respect the sport availability constraints: do NOT schedule a swim, bike, run, or strength session on a day not listed in the athlete's availability.
2. Follow the 80/20 intensity rule: mostly Zone 1-2 easy aerobic base training.
3. Include at least 1-2 rest days depending on fatigue.
4. Scale total session time to fit the weekly target hours.
5. If the race is approaching or is this week, apply tapering principles (short duration, light intensity, lots of rest).
6. You may schedule double-workout days (e.g. morning swim, evening strength) if it makes sense to hit the weekly target hours without violating the 80/20 rule or causing extreme fatigue. Let the weekly target hours and availability guide this decision.

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
            content = chat_completion(
                messages=[
                    {"role": "system", "content": "You are a professional triathlon coach that generates structured training plans in JSON format."},
                    {"role": "user", "content": prompt}
                ],
                json_mode=True
            )
            return json.loads(content)
        except Exception as e:
            print(f"Error generating weekly plan: {e}")
            return self._fallback_weekly_plan(profile)

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

        prompt = f"""You are Phoenix, an elite triathlon coach. You need to review today's PLANNED workout and decide if it needs to be adapted based on the athlete's actual RECOVERY metrics today.
{context_section}
PLANNED WORKOUT FOR TODAY:
{json.dumps(planned_workout, indent=2)}

TODAY'S ACTUAL RECOVERY METRICS:
{today_metrics}

RULES FOR ADAPTATION:
1. If the athlete's metrics show severe fatigue (extremely low HRV, elevated RHR, negative TIB, or high Load Ratio > 1.4), you MUST downgrade the session to "rest" or "active recovery" (very easy swim or spin in Zone 1 for < 20 min).
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
            content = chat_completion(
                messages=[
                    {"role": "system", "content": "You are a professional triathlon coach that adapts training sessions based on recovery data in JSON format."},
                    {"role": "user", "content": prompt}
                ],
                json_mode=True
            )
            return json.loads(content)
        except Exception as e:
            print(f"Error adapting workout: {e}")
            adapted = planned_workout.copy()
            adapted["adaptation"] = None
            return adapted

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
            content = chat_completion(
                messages=[
                    {"role": "system", "content": "You are an elite triathlon coach providing a weekly training review in JSON format."},
                    {"role": "user", "content": prompt}
                ],
                json_mode=True
            )
            return json.loads(content)
        except Exception as e:
            print(f"Error generating weekly review: {e}")
            return {
                "went_well": "Unable to generate review.",
                "needs_attention": str(e),
                "next_week_impact": "Continue as planned.",
                "motivation": "Keep going!",
                "grade": "N/A"
            }

    def _fallback_weekly_plan(self, profile: dict) -> dict:
        """Rule-based fallback weekly plan generator when LLM is unavailable."""
        swim_days = [d.strip().capitalize() for d in (profile.get('swim_days') or 'wed,sat,sun').split(',')]
        bike_days = [d.strip().capitalize() for d in (profile.get('bike_days') or 'tue,thu,sat').split(',')]
        run_days = [d.strip().capitalize() for d in (profile.get('run_days') or 'mon,wed,fri').split(',')]
        strength_days = [d.strip().capitalize() for d in (profile.get('strength_days') or 'mon,fri').split(',')]

        days_names = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

        plan = {
            "week_summary": {
                "focus": "Base Building",
                "rationale": "Standard rule-based plan focusing on consistency across your available days.",
                "expected_total_hours": profile.get('weekly_hours_target') or 8.0,
                "expected_run_km": 20.0
            },
            "days": {}
        }

        for day in days_names:
            day_abbr = day[:3].capitalize()

            # Simple sport matching
            sport = "rest"
            title = "Rest Day"
            steps = []
            time = "0 min"
            hr = "N/A"

            if day_abbr in swim_days:
                sport = "swimming"
                title = "Easy Technical Swim"
                steps = [{"type": "main", "duration": "30:00", "zone": 2, "description": "Focus on high elbow and core rotation"}]
                time = "30 min"
                hr = "Easy pace"
            elif day_abbr in bike_days:
                sport = "cycling"
                title = "Aerobic Base Ride"
                steps = [{"type": "main", "duration": "45:00", "zone": 2, "description": "Keep cadence 85-95 rpm"}]
                time = "45 min"
                hr = "110-130 bpm"
            elif day_abbr in run_days:
                sport = "running"
                title = "Conversational Base Run"
                steps = [{"type": "main", "duration": "30:00", "zone": 2, "description": "Very relaxed running"}]
                time = "30 min"
                hr = "120-140 bpm"
            elif day_abbr in strength_days:
                sport = "strength"
                title = "General Core & Hip Stability"
                steps = [{"type": "main", "duration": "20:00", "zone": 1, "description": "Planks, squats, and single-leg bridges"}]
                time = "20 min"
                hr = "N/A"

            plan["days"][day] = {
                "summary": f"{title} scheduled.",
                "workouts": [{
                    "sport": sport,
                    "title": title,
                    "steps": steps,
                    "total_time": time,
                    "hr_target": hr
                }],
                "rationale": "Scheduled base training day.",
                "coach_note": "Consistency is king. Get it done!"
            }

        return plan

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
