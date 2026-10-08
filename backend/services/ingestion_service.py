import json
import os
from datetime import datetime, timedelta
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from pathlib import Path
from backend.models.database import Base, Athlete, Activity, RecoverySnapshot, ActivityRecord
from backend.utils.timezone import get_local_today
from backend.services.fit_importer import parse_fit_file
from sqlalchemy import func

class IngestionService:
    def __init__(self, db_url=None):
        if not db_url:
            db_url = os.getenv("DATABASE_URL", "sqlite:///./phoenix_coach.db")
        
        self.engine = create_engine(db_url)
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)

    def ingest_coros_data(self, data):
        """Populates the database from a COROS scrape — either the dict
        returned by CorosScraper.scrape_all or a path to its JSON dump
        (rebuild_db.py and the tests pass files).

        Returns the list of newly inserted Activity ids, so the caller can
        say what a refresh actually found (refresh_events)."""
        if isinstance(data, (str, Path)):
            if not Path(data).exists():
                print(f"Error: {data} not found.")
                return []
            with open(data, 'r') as f:
                data = json.load(f)

        session = self.Session()
        new_activity_ids = []
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
                # Deduplication check: ID or Start Time. The MCP path ships
                # startTimeLocal (athlete's wall clock); the scraper's
                # `timestamp` is midnight of happenDay and reads as a local
                # date through utcfromtimestamp only by accident.
                if act.get("startTimeLocal"):
                    start_dt = datetime.fromisoformat(act["startTimeLocal"])
                else:
                    start_dt = datetime.utcfromtimestamp(act["timestamp"])
                start_window_start = start_dt - timedelta(seconds=10)
                start_window_end = start_dt + timedelta(seconds=10)
                existing = session.query(Activity).filter(
                    (Activity.id == str(act["labelId"])) | 
                    ((Activity.start_time >= start_window_start) & (Activity.start_time <= start_window_end) & (Activity.sport_code == act.get("sportType")))
                ).first()
                
                if existing:
                    # Update existing with COROS-specific metrics if missing
                    if act.get("trainingLoad"):
                        existing.training_load = float(act["trainingLoad"])
                    continue
                
                # Convert pace (sec/km) to speed (m/s)
                # avgSpeed in JSON is actually pace in sec/km
                pace_sec_km = act.get("avgSpeed") or 0
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
                    start_time=start_dt,
                    duration_sec=float(act["duration"]),
                    distance_m=float(act["distance"]),
                    avg_hr=act.get("avgHeartRate"),
                    avg_power_watts=float(act.get("avgPower") or 0),
                    avg_speed_ms=speed_ms,
                    total_ascent_m=float(act.get("totalElevation") or 0),
                    source=act.get("source") or "coros_scraper",
                    training_load=float(act.get("trainingLoad") or 0),
                    step_count=act.get("step"),
                    sets=act.get("sets"),
                    cadence=act.get("pitch"),
                    sport_code=act.get("sportType"),
                    sub_mode=act.get("subMode"),
                    activity_name=act.get("name"),
                    calories=act.get("calories"),
                )
                session.add(new_act)
                new_activity_ids.append(new_act.id)

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
                
                # A key the payload doesn't carry leaves the column alone.
                # The scraper's dayList always carries them; the MCP path
                # omits what it couldn't parse, so a reworded COROS line can
                # never land as a 0 that reads "all clear" to the gates.
                def _set(attr, value, cast=float):
                    if value is None:
                        return
                    setattr(snapshot, attr, cast(value))

                # Prioritize testRhr (manual/test) over rhr (automatic)
                if day.get("testRhr") or day.get("rhr"):
                    _set("resting_hr", day.get("testRhr") if (day.get("testRhr") or 0) > 0 else day.get("rhr"), int)
                if day.get("avgSleepHrv"):
                    _set("hrv_ms", day["avgSleepHrv"])
                _set("training_load", day.get("trainingLoad"))
                _set("vo2_max", day.get("vo2max"))
                _set("ati", day.get("ati"))
                _set("cti", day.get("cti"))
                _set("tib", day.get("tib"))
                _set("fatigue_pct", day.get("tiredRateNew"))
                _set("fatigue_state", day.get("tiredRateStateNew"), int)
                _set("load_ratio", day.get("trainingLoadRatio"))
                _set("load_ratio_state", day.get("trainingLoadRatioState"), int)
                _set("t7d_load", day.get("t7d"))
                _set("t28d_load", day.get("t28d"))
                _set("recommend_tl_max", day.get("recomendTlMax"))
                _set("recommend_tl_min", day.get("recomendTlMin"))
                _set("lthr", day.get("lthr"), int)
                _set("ltsp", day.get("ltsp"), int)
                _set("performance_index", day.get("staminaLevel"))
                _set("performance_score", day.get("performance"), int)
                # New with the MCP path (docs/COROS_MCP.md): the columns that
                # were NULL for the scraper's whole life.
                if day.get("sleepDurationMin") is not None:
                    snapshot.sleep_duration_hr = round(float(day["sleepDurationMin"]) / 60, 2)
                _set("sleep_quality_score", day.get("sleepScore"))
                _set("stress_level", day.get("stressAvg"), int)
                # COROS's own HRV normal range. MCP sends it by name; the
                # scraper's sleepHrvIntervalList carries it at indexes 2 and 3
                # (verified equal to the MCP "Normal Range" on every day compared).
                _set("hrv_normal_low", day.get("sleepHrvNormalLow"))
                _set("hrv_normal_high", day.get("sleepHrvNormalHigh"))
                iv = day.get("sleepHrvIntervalList")
                if isinstance(iv, list) and len(iv) >= 4 and iv[2] and iv[3]:
                    snapshot.hrv_normal_low, snapshot.hrv_normal_high = float(iv[2]), float(iv[3])

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
                if hrv_entry.get("sleepHrvBase") is not None:
                    snapshot.hrv_baseline = float(hrv_entry["sleepHrvBase"])
                if hrv_entry.get("sleepHrvSd") is not None:
                    snapshot.hrv_sd = float(hrv_entry["sleepHrvSd"])
                iv = hrv_entry.get("sleepHrvIntervalList")
                if isinstance(iv, list) and len(iv) >= 4 and iv[2] and iv[3]:
                    snapshot.hrv_normal_low, snapshot.hrv_normal_high = float(iv[2]), float(iv[3])

            # 4b. COROS's own recovery percentage (summaryInfo.recoveryPct)
            # feeds the previously-dead recovery_score column. Advisory prose
            # only — the deterministic adaptation gate in main.py does not
            # read it. Annotate-only, NEVER create the row: summaryInfo is
            # undated, and minting a today-dated row from it would make the
            # adapt-today staleness guard read a partial scrape (dashboard
            # captured, analyse_query missing) as fresh recovery data — a
            # bare row with every gate metric NULL, sitting where the guard
            # looks. If today's row doesn't exist, the signal is dropped.
            recovery_pct = summary_info.get("recoveryPct")
            if recovery_pct is not None:
                snapshot = session.query(RecoverySnapshot).filter_by(
                    date=get_local_today(), athlete_id=athlete_id).first()
                if snapshot:
                    snapshot.recovery_score = float(recovery_pct)

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
                if latest_lthr: athlete.lthr = latest_lthr
                if latest_ltsp: athlete.threshold_pace_min_km = latest_ltsp / 60.0
                
                if hrv_list:
                    latest_hrv_base = next((h["sleepHrvBase"] for h in reversed(hrv_list) if h.get("sleepHrvBase", 0) and h.get("sleepHrvBase", 0) > 0), None)
                    if latest_hrv_base: 
                        athlete.hrv_baseline = float(latest_hrv_base)
                        print(f"  Latest HRV Base: {latest_hrv_base}")

            # 6. Extract personal zones and profile data (ftp, weight, zones)
            # Iterate through all captured endpoints to find these keys
            for endpoint_name, endpoint_data in evolab_data.items():
                if not isinstance(endpoint_data, dict):
                    continue
                
                # Check at the root of the payload
                if "lthrZone" in endpoint_data: athlete.hr_zones = endpoint_data["lthrZone"]
                if "ltspZone" in endpoint_data: athlete.pace_zones = endpoint_data["ltspZone"]
                if "cyclePowerZone" in endpoint_data: athlete.cycle_power_zones = endpoint_data["cyclePowerZone"]
                if "ftp" in endpoint_data: athlete.ftp_watts = float(endpoint_data["ftp"])
                
                # Sometimes it's inside zoneData
                zone_data = endpoint_data.get("zoneData")
                if zone_data:
                    if "lthrZone" in zone_data: athlete.hr_zones = zone_data["lthrZone"]
                    if "ltspZone" in zone_data: athlete.pace_zones = zone_data["ltspZone"]
                    if "cyclePowerZone" in zone_data: athlete.cycle_power_zones = zone_data["cyclePowerZone"]
                    if "ftp" in zone_data: athlete.ftp_watts = float(zone_data["ftp"])
                
                # The watch owns weight, always — Alex maintains it in one place
                # (COROS) and the scrape propagates it. A Profile edit is a
                # temporary value until the next scrape, by choice (2026-08-21).
                if "weight" in endpoint_data:
                    try:
                        athlete.weight_kg = float(endpoint_data["weight"])
                    except (ValueError, TypeError):
                        pass
                
                if "headPic" in endpoint_data:
                    athlete.head_pic_url = endpoint_data["headPic"]
                    
                # Check inside summaryInfo (where it appeared in our debug dump)
                si = endpoint_data.get("summaryInfo", {})
                if si:
                    if "lthrZone" in si: athlete.hr_zones = si["lthrZone"]
                    if "ltspZone" in si: athlete.pace_zones = si["ltspZone"]
                    if "cyclePowerZone" in si: athlete.cycle_power_zones = si["cyclePowerZone"]
                    if "ftp" in si: athlete.ftp_watts = float(si["ftp"])

            session.commit()
            print("Successfully ingested COROS data into the database.")
            return new_activity_ids
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
