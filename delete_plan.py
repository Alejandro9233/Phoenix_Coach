from backend.main import SessionLocal
from backend.models.database import WeeklyPlan

db = SessionLocal()
db.query(WeeklyPlan).delete()
db.commit()
print("Deleted cached weekly plans.")
