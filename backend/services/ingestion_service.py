import json
import os
from datetime import datetime, timedelta
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from pathlib import Path
from backend.models.database import Base, Athlete, Activity, RecoverySnapshot, ActivityRecord
from backend.services.fit_importer import parse_fit_file
from sqlalchemy import func

class IngestionService:
    def __init__(self, db_url=None):
        if not db_url:
            db_url = os.getenv("DATABASE_URL", "sqlite:///./phoenix_coach.db")
        
        self.engine = create_engine(db_url)
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)

    def ingest_coros_data(self, json_path):
        """Parses the COROS scrape JSON and populates the database."""
        if not Path(json_path).exists():
            print(f"Error: {json_path} not found.")
            return

        with open(json_path, 'r') as f:
            data = json.load(f)

        session = self.Session()
        try:
            # 1. Ensure at least one athlete exists
            athlete = session.query(Athlete).first()
            if not athlete:
                athlete = Athlete(name="Alejandro")
                session.add(athlete)
                session.flush()
            
            athlete_id = athlete.id

            # 2. Ingest Activities
            activities_data = data.get("activities", [])
            for act in activities_data:
                # Deduplication check: ID or Start Time
                start_dt = datetime.utcfromtimestamp(act["timestamp"])
                start_window_start = start_dt - timedelta(seconds=10)
                start_window_end = start_dt + timedelta(seconds=10)
                existing = session.query(Activity).filter(
                    (Activity.id == str(act["labelId"])) | 
                    ((Activity.start_time >= start_window_start) & (Activity.start_time <= start_window_end) & (Activity.sport == act.get("sportType")))
                ).first()
                
                if existing:
                    # Update existing with COROS-specific metrics if missing
                    if act.get("trainingLoad"):
                        existing.training_load = float(act["trainingLoad"])
                    continue
                
                # Convert pace (sec/km) to speed (m/s)
                # avgSpeed in JSON is actually pace in sec/km
                pace_sec_km = act.get("avgSpeed", 0)
                speed_ms = 1000 / pace_sec_km if pace_sec_km > 0 else 0

                # Map sport codes to strings
                sport_map = {
                    100: "running",
                    101: "running",       # Treadmill
                    102: "running",       # Trail running
                    104: "running",       # Ultra/trail
                    200: "cycling",       # Indoor cycling
                    201: "cycling",
                    300: "swimming",      # Pool
                    301: "swimming",      # Open water
                    402: "strength",
                    10000: "triathlon",
                }
                sport_str = sport_map.get(act.get("sportType"), "other")

                new_act = Activity(
                    id=str(act["labelId"]),
                    athlete_id=athlete_id,
                    sport=sport_str,
                    start_time=datetime.utcfromtimestamp(act["timestamp"]),
                    duration_sec=float(act["duration"]),
                    distance_m=float(act["distance"]),
                    avg_hr=act.get("avgHeartRate"),
                    avg_power_watts=float(act.get("avgPower", 0)),
                    avg_speed_ms=speed_ms,
                    total_ascent_m=float(act.get("totalElevation", 0)),
                    source="coros_scraper",
                    training_load=float(act.get("trainingLoad", 0)),
                    step_count=act.get("step"),
                    sets=act.get("sets"),
                    cadence=act.get("pitch"),
                    sport_code=act.get("sportType"),
                    sub_mode=act.get("subMode")
                )
                session.add(new_act)

            # 3. Ingest Recovery Snapshots (EvoLab Metrics)
            # From analyse_query -> dayList
            evolab_data = data.get("evolab", {})
            analyse_query = evolab_data.get("analyse_query", {})
            day_list = analyse_query.get("dayList", [])
            
            for day in day_list:
                # Convert 20260218 to date object
                date_str = str(day["happenDay"])
                date_obj = datetime.strptime(date_str, "%Y%m%d").date()
                
                snapshot = session.query(RecoverySnapshot).filter_by(date=date_obj, athlete_id=athlete_id).first()
                if not snapshot:
                    snapshot = RecoverySnapshot(date=date_obj, athlete_id=athlete_id)
                    session.add(snapshot)
                
                # Prioritize testRhr (manual/test) over rhr (automatic)
                rhr_val = day.get("testRhr") if day.get("testRhr", 0) > 0 else day.get("rhr")
                snapshot.resting_hr = rhr_val
                snapshot.hrv_ms = float(day.get("avgSleepHrv", 0)) if day.get("avgSleepHrv") else None
                snapshot.training_load = float(day.get("trainingLoad", 0))
                snapshot.vo2_max = float(day.get("vo2max", 0))
                snapshot.ati = float(day.get("ati", 0))
                snapshot.cti = float(day.get("cti", 0))
                snapshot.tib = float(day.get("tib", 0))
                snapshot.fatigue_pct = float(day.get("tiredRateNew", 0))
                snapshot.fatigue_state = day.get("tiredRateStateNew")
                snapshot.load_ratio = float(day.get("trainingLoadRatio", 0))
                snapshot.load_ratio_state = day.get("trainingLoadRatioState")
                snapshot.t7d_load = float(day.get("t7d", 0))
                snapshot.t28d_load = float(day.get("t28d", 0))
                snapshot.recommend_tl_max = float(day.get("recomendTlMax", 0))
                snapshot.recommend_tl_min = float(day.get("recomendTlMin", 0))
                snapshot.lthr = day.get("lthr")
                snapshot.ltsp = day.get("ltsp")
                snapshot.performance_index = float(day.get("staminaLevel", 0))
                snapshot.performance_score = day.get("performance")

            # 4. Ingest Detailed HRV Data
            # From dashboard_query -> summaryInfo -> sleepHrvData -> sleepHrvList
            dashboard_query = evolab_data.get("dashboard_query", {})
            summary_info = dashboard_query.get("summaryInfo", {})
            hrv_data = summary_info.get("sleepHrvData", {})
            hrv_list = hrv_data.get("sleepHrvList", [])
            for hrv_entry in hrv_list:
                date_str = str(hrv_entry["happenDay"])
                date_obj = datetime.strptime(date_str, "%Y%m%d").date()
                
                snapshot = session.query(RecoverySnapshot).filter_by(date=date_obj, athlete_id=athlete_id).first()
                if not snapshot:
                    snapshot = RecoverySnapshot(date=date_obj, athlete_id=athlete_id)
                    session.add(snapshot)
                
                if hrv_entry.get("avgSleepHrv"):
                    snapshot.hrv_ms = float(hrv_entry["avgSleepHrv"])
                snapshot.hrv_baseline = float(hrv_entry.get("sleepHrvBase", 0))
                snapshot.hrv_sd = float(hrv_entry.get("sleepHrvSd", 0))

            # 5. Update Athlete Profile with latest available data (searching backwards)
            if day_list:
                # Find latest non-zero markers
                latest_vo2 = next((d["vo2max"] for d in reversed(day_list) if d.get("vo2max", 0) and d.get("vo2max", 0) > 0), None)
                latest_stamina = next((d["staminaLevel"] for d in reversed(day_list) if d.get("staminaLevel", 0) and d.get("staminaLevel", 0) > 0), None)
                latest_rhr = next(( (d.get("testRhr") if d.get("testRhr", 0) > 0 else d.get("rhr")) for d in reversed(day_list) if (d.get("testRhr", 0) > 0 or d.get("rhr", 0) > 0)), None)
                latest_lthr = next((d["lthr"] for d in reversed(day_list) if d.get("lthr", 0) and d.get("lthr", 0) > 0), None)
                latest_ltsp = next((d["ltsp"] for d in reversed(day_list) if d.get("ltsp", 0) and d.get("ltsp", 0) > 0), None)

                print(f"Updating Athlete {athlete.name} (ID: {athlete.id})")
                print(f"  Latest VO2: {latest_vo2}")
                print(f"  Latest RHR: {latest_rhr}")

                if latest_vo2: athlete.vo2_max = float(latest_vo2)
                if latest_stamina: athlete.stamina_level = float(latest_stamina)
                if latest_rhr: athlete.hr_rest = latest_rhr
                if latest_lthr: athlete.hr_max = latest_lthr 
                if latest_ltsp: athlete.threshold_pace_min_km = latest_ltsp / 60.0
                
                if hrv_list:
                    latest_hrv_base = next((h["sleepHrvBase"] for h in reversed(hrv_list) if h.get("sleepHrvBase", 0) and h.get("sleepHrvBase", 0) > 0), None)
                    if latest_hrv_base: 
                        athlete.hrv_baseline = float(latest_hrv_base)
                        print(f"  Latest HRV Base: {latest_hrv_base}")

            session.commit()
            print("Successfully ingested COROS data into the database.")
        except Exception as e:
            session.rollback()
            print(f"Error during ingestion: {e}")
            raise e
        finally:
            session.close()

    def bulk_import_fit_directory(self, directory_path):
        """Imports all .fit files from a directory."""
        path = Path(directory_path)
        if not path.exists():
            print(f"Directory {directory_path} not found.")
            return

        session = self.Session()
        try:
            athlete = session.query(Athlete).first()
            if not athlete:
                athlete = Athlete(name="Alex")
                session.add(athlete)
                session.flush()
            
            athlete_id = athlete.id
            count = 0
            
            for fit_file in path.glob("*.fit"):
                # Deduplication check
                existing = session.query(Activity).filter_by(id=fit_file.name).first()
                if existing:
                    continue

                try:
                    activity, records = parse_fit_file(str(fit_file))
                    activity.athlete_id = athlete_id
                    
                    # Check for duplicate by start_time
                    start_window_start = activity.start_time - timedelta(seconds=10)
                    start_window_end = activity.start_time + timedelta(seconds=10)
                    duplicate = session.query(Activity).filter(
                        (Activity.start_time >= start_window_start) & (Activity.start_time <= start_window_end)
                    ).first()
                    
                    if not duplicate:
                        session.add(activity)
                        # Only add records for activities we import
                        for r in records:
                            r.activity_id = activity.id
                            session.add(r)
                        count += 1
                except Exception as e:
                    print(f"Failed to parse {fit_file.name}: {e}")

            session.commit()
            print(f"Successfully imported {count} FIT files.")
        finally:
            session.close()

if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()
    
    service = IngestionService()
    service.ingest_coros_data("coros_scraped_data.json")
