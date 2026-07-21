from backend.main import SessionLocal
from backend.models.database import WeeklyPlan

db = SessionLocal()
plan = db.query(WeeklyPlan).first()
if plan:
    print("Plan is in DB!")
else:
    print("No plan in DB.")
