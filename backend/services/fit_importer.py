import os
from datetime import datetime
from garmin_fit_sdk import Decoder, Stream
from backend.models.database import Activity, ActivityRecord

def parse_fit_file(file_path):
    """
    Parses a FIT file and returns SQLAlchemy models for Activity and its Records.
    """
    stream = Stream.from_file(file_path)
    decoder = Decoder(stream)
    messages, errors = decoder.read()
    
    if errors:
        print(f"Warning: Errors during FIT parsing for {file_path}: {errors}")

    session_msg = messages.get('session_mesgs', [{}])[0]
    activity_msg = messages.get('activity_mesgs', [{}])[0]
    
    # Basic session info
    activity_id = os.path.basename(file_path)
    sport = session_msg.get('sport', 'unknown')
    sub_sport = session_msg.get('sub_sport')
    start_time = session_msg.get('start_time')
    duration_sec = session_msg.get('total_elapsed_time', 0)
    distance_m = session_msg.get('total_distance')
    avg_hr = session_msg.get('avg_heart_rate')
    max_hr = session_msg.get('max_heart_rate')
    calories = session_msg.get('total_calories')
    avg_speed = session_msg.get('enhanced_avg_speed', session_msg.get('avg_speed'))
    max_speed = session_msg.get('enhanced_max_speed', session_msg.get('max_speed'))
    avg_power = session_msg.get('avg_power')
    ascent = session_msg.get('total_ascent')
    descent = session_msg.get('total_descent')
    pool_length = session_msg.get('pool_length')
    
    # Swimming specifics
    total_lengths = len(messages.get('length_mesgs', []))
    
    # Create Activity object
    activity = Activity(
        id=activity_id,
        sport=str(sport) if sport else 'unknown',
        sub_sport=str(sub_sport) if sub_sport else None,
        start_time=start_time,
        duration_sec=float(duration_sec),
        distance_m=float(distance_m) if distance_m else None,
        avg_hr=int(avg_hr) if avg_hr else None,
        max_hr=int(max_hr) if max_hr else None,
        calories=int(calories) if calories else None,
        avg_speed_ms=float(avg_speed) if avg_speed else None,
        max_speed_ms=float(max_speed) if max_speed else None,
        avg_power_watts=float(avg_power) if avg_power else None,
        total_ascent_m=float(ascent) if ascent else None,
        total_descent_m=float(descent) if descent else None,
        pool_length_m=float(pool_length) if pool_length else None,
        total_lengths=total_lengths if total_lengths > 0 else None,
        source="fit_import"
    )
    
    # Parse records (per-second data)
    records = []
    record_msgs = messages.get('record_mesgs', [])
    for r in record_msgs:
        rec = ActivityRecord(
            activity_id=activity_id,
            timestamp=r.get('timestamp'),
            heart_rate=r.get('heart_rate'),
            speed_ms=r.get('enhanced_speed', r.get('speed')),
            cadence=r.get('cadence'),
            power_watts=r.get('power'),
            altitude_m=r.get('enhanced_altitude', r.get('altitude')),
            lat=r.get('position_lat'),
            lon=r.get('position_long')
        )
        records.append(rec)
        
    return activity, records
