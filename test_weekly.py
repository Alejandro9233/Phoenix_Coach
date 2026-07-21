import sys
import json
import asyncio
from backend.main import SessionLocal
from backend.models.database import Athlete
from backend.agents.data_agent import DataAgent
from backend.agents.response_agent import ResponseAgent

db = SessionLocal()
data_agent = DataAgent(db)
summary = data_agent.summarize()
athlete = db.query(Athlete).first()
profile = {
    "race_name": athlete.race_name,
    "race_distance": athlete.race_distance,
    "race_date": str(athlete.race_date) if athlete.race_date else None,
    "weekly_hours_target": athlete.weekly_hours_target or 8.0,
    "swim_days": athlete.swim_days or "wed,sat,sun",
    "bike_days": athlete.bike_days or "mon,tue,wed,thu,fri,sat,sun",
    "run_days": athlete.run_days or "mon,tue,wed,thu,fri,sat,sun",
    "strength_days": athlete.strength_days or "mon,wed,fri"
} if athlete else {}

agent = ResponseAgent()
plan = agent.generate_weekly_plan(summary, profile)
print(json.dumps(plan, indent=2))
