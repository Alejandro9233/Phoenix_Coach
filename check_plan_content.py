from backend.main import SessionLocal
from backend.models.database import WeeklyPlan
import json

db = SessionLocal()
plan = db.query(WeeklyPlan).first()
if plan:
    print(json.dumps(plan.plan_json, indent=2)[:500])
