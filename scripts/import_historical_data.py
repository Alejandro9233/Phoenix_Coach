import os
import sys
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from dotenv import load_dotenv

# Add project root to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.models.database import Base, Activity, ActivityRecord, Athlete
from backend.services.fit_importer import parse_fit_file

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./phoenix_coach.db")
engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

def main():
    print(f"Initializing database at {DATABASE_URL}...")
    Base.metadata.create_all(bind=engine)
    
    db = SessionLocal()
    
    # Ensure athlete exists
    athlete = db.query(Athlete).first()
    if not athlete:
        print("Creating default athlete profile...")
        athlete = Athlete(name="Alex", age=30)
        db.add(athlete)
        db.commit()
        db.refresh(athlete)

    fit_dir = "fit_examples"
    if not os.path.exists(fit_dir):
        print(f"Error: Directory {fit_dir} not found.")
        return

    fit_files = [f for f in os.listdir(fit_dir) if f.endswith(".fit")]
    print(f"Found {len(fit_files)} FIT files in {fit_dir}.")

    for filename in fit_files:
        if db.query(Activity).filter(Activity.id == filename).first():
            print(f"  Skipping {filename} (already imported)")
            continue
            
        file_path = os.path.join(fit_dir, filename)
        print(f"  Importing {filename}...")
        try:
            activity, records = parse_fit_file(file_path)
            activity.athlete_id = athlete.id
            db.add(activity)
            db.add_all(records)
            print(f"    Success: {activity.sport} activity, {len(records)} records.")
        except Exception as e:
            print(f"    Error importing {filename}: {e}")

    db.commit()
    db.close()
    print("\nBulk import complete.")

if __name__ == "__main__":
    main()
