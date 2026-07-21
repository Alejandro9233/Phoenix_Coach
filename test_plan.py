import sys
import json
from backend.agents.response_agent import ResponseAgent
agent = ResponseAgent()
profile = {"weekly_hours_target": 10.0, "swim_days": "mon", "bike_days": "tue", "run_days": "wed", "strength_days": "thu"}
plan = agent._fallback_weekly_plan(profile)
print(json.dumps(plan, indent=2))
