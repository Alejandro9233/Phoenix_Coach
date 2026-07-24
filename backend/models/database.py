from sqlalchemy import Column, Integer, Float, String, DateTime, Date, ForeignKey, JSON, Boolean, Text
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship

Base = declarative_base()

class Athlete(Base):
    __tablename__ = "athletes"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, default="Athlete")
    age = Column(Integer, nullable=True)
    weight_kg = Column(Float, nullable=True)
    ftp_watts = Column(Float, nullable=True)
    threshold_pace_min_km = Column(Float, nullable=True)
    swim_css_sec_100m = Column(Float, nullable=True)
    race_date = Column(Date, nullable=True)
    race_name = Column(String, nullable=True)           # e.g. "Monterrey 70.3"
    race_type = Column(String, nullable=True)           # e.g. "Triathlon", "Running"
    race_distance = Column(String, nullable=True)        # e.g. "Olympic", "70.3", "Full Ironman"
    weekly_hours_target = Column(Float, nullable=True)
    hr_max = Column(Integer, nullable=True)
    hr_rest = Column(Integer, nullable=True)
    hrv_baseline = Column(Float, nullable=True)
    vo2_max = Column(Float, nullable=True)
    stamina_level = Column(Float, nullable=True)
    # Sport availability — comma-separated day abbreviations: "mon,tue,wed,thu,fri,sat,sun"
    swim_days = Column(String, default="wed,sat,sun")
    bike_days = Column(String, default="mon,tue,wed,thu,fri,sat,sun")
    run_days = Column(String, default="mon,tue,wed,thu,fri,sat,sun")
    strength_days = Column(String, default="mon,wed,fri")
    training_start_date = Column(Date, nullable=True)  # Anchors 3:1 build/recovery cycle
    target_finish_time = Column(String, nullable=True)  # "3:45:00" (HH:MM:SS)

class Activity(Base):
    __tablename__ = "activities"
    id = Column(String, primary_key=True)  # FIT filename or unique ID
    athlete_id = Column(Integer, ForeignKey("athletes.id"))
    sport = Column(String)  # running, cycling, swimming, training, generic
    sub_sport = Column(String, nullable=True)
    start_time = Column(DateTime, index=True)
    duration_sec = Column(Float)
    distance_m = Column(Float, nullable=True)
    avg_hr = Column(Integer, nullable=True)
    max_hr = Column(Integer, nullable=True)
    calories = Column(Integer, nullable=True)
    avg_speed_ms = Column(Float, nullable=True)
    max_speed_ms = Column(Float, nullable=True)
    avg_power_watts = Column(Float, nullable=True)
    total_ascent_m = Column(Float, nullable=True)
    total_descent_m = Column(Float, nullable=True)
    pool_length_m = Column(Float, nullable=True)
    total_lengths = Column(Integer, nullable=True)
    source = Column(String)  # fit_import, coros_scraper
    training_load = Column(Float, nullable=True)
    step_count = Column(Integer, nullable=True)
    sets = Column(Integer, nullable=True)
    cadence = Column(Integer, nullable=True)
    sport_code = Column(Integer, nullable=True)
    sub_mode = Column(Integer, nullable=True)
    hr_zone_distribution = Column(JSON, nullable=True)  # {"z1": sec, "z2": sec, ...}
    # Enriched fields from COROS activity detail pages
    max_hr_scraped = Column(Integer, nullable=True)
    calories_scraped = Column(Integer, nullable=True)
    avg_cadence_scraped = Column(Integer, nullable=True)
    activity_name = Column(String, nullable=True)  # User-set name in COROS
    lap_data = Column(JSON, nullable=True)  # [{lap_no, duration, distance, avg_hr, avg_pace}, ...]
    detail_data = Column(JSON, nullable=True)  # Raw detail JSON for future use
    
    records = relationship("ActivityRecord", back_populates="activity", cascade="all, delete-orphan")
    feedback = relationship("AthleteFeedback", back_populates="activity", uselist=False)

class ActivityRecord(Base):
    __tablename__ = "activity_records"
    id = Column(Integer, primary_key=True, index=True)
    activity_id = Column(String, ForeignKey("activities.id"))
    timestamp = Column(DateTime)
    heart_rate = Column(Integer, nullable=True)
    speed_ms = Column(Float, nullable=True)
    cadence = Column(Integer, nullable=True)
    power_watts = Column(Float, nullable=True)
    altitude_m = Column(Float, nullable=True)
    lat = Column(Float, nullable=True)
    lon = Column(Float, nullable=True)
    
    activity = relationship("Activity", back_populates="records")

class RecoverySnapshot(Base):
    __tablename__ = "recovery_snapshots"
    date = Column(Date, primary_key=True)
    athlete_id = Column(Integer, ForeignKey("athletes.id"))
    hrv_ms = Column(Float, nullable=True)
    resting_hr = Column(Integer, nullable=True)
    sleep_duration_hr = Column(Float, nullable=True)
    sleep_quality_score = Column(Float, nullable=True)
    recovery_score = Column(Float, nullable=True)
    stress_level = Column(Integer, nullable=True)
    body_battery = Column(Integer, nullable=True)
    training_load = Column(Float, nullable=True)  # COROS proprietary load
    hrv_baseline = Column(Float, nullable=True)
    hrv_sd = Column(Float, nullable=True)
    vo2_max = Column(Float, nullable=True)
    ati = Column(Float, nullable=True)
    cti = Column(Float, nullable=True)
    tib = Column(Float, nullable=True)
    fatigue_pct = Column(Float, nullable=True)
    fatigue_state = Column(Integer, nullable=True)
    load_ratio = Column(Float, nullable=True)
    load_ratio_state = Column(Integer, nullable=True)
    t7d_load = Column(Float, nullable=True)
    t28d_load = Column(Float, nullable=True)
    recommend_tl_max = Column(Float, nullable=True)
    recommend_tl_min = Column(Float, nullable=True)
    lthr = Column(Integer, nullable=True)
    ltsp = Column(Integer, nullable=True)
    performance_index = Column(Float, nullable=True)
    performance_score = Column(Integer, nullable=True)

class AthleteFeedback(Base):
    __tablename__ = "athlete_feedback"
    id = Column(Integer, primary_key=True, index=True)
    date = Column(DateTime, default=None)
    activity_id = Column(String, ForeignKey("activities.id"), nullable=True)
    athlete_id = Column(Integer, ForeignKey("athletes.id"))
    rpe = Column(Integer)  # 1-10
    motivation = Column(Integer)  # 1-5
    soreness = Column(Integer)  # 1-5
    injury_notes = Column(Text, nullable=True)
    general_notes = Column(Text, nullable=True)
    strength_exercises = Column(JSON, nullable=True)  # [{name, sets, reps, weight}, ...]
    
    activity = relationship("Activity", back_populates="feedback")

class CoachingRecommendation(Base):
    __tablename__ = "coaching_recommendations"
    id = Column(Integer, primary_key=True, index=True)
    date = Column(Date, index=True)
    athlete_id = Column(Integer, ForeignKey("athletes.id"))
    recommended_workout = Column(Text)
    rationale = Column(Text)
    adaptation_reason = Column(String, nullable=True)
    coaching_note = Column(Text, nullable=True)
    plan_vs_actual_score = Column(Float, nullable=True)

class InjuryLog(Base):
    __tablename__ = "injury_logs"
    id = Column(Integer, primary_key=True, index=True)
    athlete_id = Column(Integer, ForeignKey("athletes.id"))
    date_reported = Column(Date, index=True)
    body_part = Column(String)
    status = Column(String) # e.g. "Active", "Recovering", "Resolved"
    severity = Column(Integer, nullable=True) # 1-10
    notes = Column(Text, nullable=True)
    affected_sports = Column(String, nullable=True) # comma-separated like "run,bike"

class WeeklyPlan(Base):
    __tablename__ = "weekly_plans"
    id = Column(Integer, primary_key=True, index=True)
    week_start = Column(Date, index=True)  # Monday of the week
    athlete_id = Column(Integer, ForeignKey("athletes.id"))
    plan_json = Column(JSON)  # Stores the full 7-day plan structure
    created_at = Column(DateTime, default=None)
    last_adapted = Column(DateTime, nullable=True)

