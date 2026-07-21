#!/usr/bin/env python3
"""
FIT File Audit Script — Phase 0 Data Trial
Parses all FIT files and generates a comprehensive field availability report.
"""

import os
import sys
import json
from pathlib import Path
from collections import defaultdict
from datetime import datetime

try:
    from fitparse import FitFile
except ImportError:
    print("ERROR: fitparse not installed. Run: pip3 install fitparse")
    sys.exit(1)

FIT_DIR = Path(__file__).parent.parent / "fit_examples"

def parse_fit_file(filepath):
    """Parse a single FIT file and extract all message types and their fields."""
    result = {
        "filename": filepath.name,
        "filesize_bytes": filepath.stat().st_size,
        "message_types": defaultdict(lambda: {"count": 0, "fields": defaultdict(lambda: {"count": 0, "sample_values": [], "units": None})}),
        "sport": None,
        "sub_sport": None,
        "start_time": None,
        "total_elapsed_time": None,
        "total_distance": None,
        "avg_heart_rate": None,
        "max_heart_rate": None,
        "total_calories": None,
        "avg_speed": None,
        "avg_cadence": None,
        "total_ascent": None,
        "total_descent": None,
        "errors": [],
    }
    
    try:
        fitfile = FitFile(str(filepath))
        
        for record in fitfile.get_messages():
            msg_type = record.name
            result["message_types"][msg_type]["count"] += 1
            
            for field in record.fields:
                field_info = result["message_types"][msg_type]["fields"][field.name]
                field_info["count"] += 1
                if field.units:
                    field_info["units"] = field.units
                
                # Capture sample values (max 3 per field)
                if len(field_info["sample_values"]) < 3 and field.value is not None:
                    val = field.value
                    if isinstance(val, datetime):
                        val = val.isoformat()
                    elif isinstance(val, bytes):
                        val = f"<bytes:{len(val)}>"
                    field_info["sample_values"].append(val)
                
                # Extract key summary fields from session message
                if msg_type == "session":
                    if field.name == "sport":
                        result["sport"] = str(field.value)
                    elif field.name == "sub_sport":
                        result["sub_sport"] = str(field.value)
                    elif field.name == "start_time":
                        result["start_time"] = field.value.isoformat() if isinstance(field.value, datetime) else str(field.value)
                    elif field.name == "total_elapsed_time":
                        result["total_elapsed_time"] = field.value
                    elif field.name == "total_distance":
                        result["total_distance"] = field.value
                    elif field.name == "avg_heart_rate":
                        result["avg_heart_rate"] = field.value
                    elif field.name == "max_heart_rate":
                        result["max_heart_rate"] = field.value
                    elif field.name == "total_calories":
                        result["total_calories"] = field.value
                    elif field.name == "avg_speed":
                        result["avg_speed"] = field.value
                    elif field.name == "avg_cadence" or field.name == "avg_running_cadence":
                        result["avg_cadence"] = field.value
                    elif field.name == "total_ascent":
                        result["total_ascent"] = field.value
                    elif field.name == "total_descent":
                        result["total_descent"] = field.value
                        
    except Exception as e:
        result["errors"].append(str(e))
    
    return result


def generate_report(all_results):
    """Generate a comprehensive field availability report."""
    
    print("=" * 80)
    print("FIT FILE AUDIT REPORT — Phase 0 Data Trial")
    print(f"Generated: {datetime.now().isoformat()}")
    print(f"Files analyzed: {len(all_results)}")
    print("=" * 80)
    
    # ── Activity Summary ──
    print("\n" + "─" * 80)
    print("ACTIVITY SUMMARY")
    print("─" * 80)
    print(f"{'Filename':<30} {'Sport':<15} {'SubSport':<15} {'Duration':<12} {'Distance':<12} {'AvgHR':<8} {'MaxHR':<8} {'Calories':<10}")
    print("-" * 120)
    
    for r in all_results:
        duration = ""
        if r["total_elapsed_time"]:
            mins = r["total_elapsed_time"] / 60
            duration = f"{mins:.1f} min"
        
        distance = ""
        if r["total_distance"]:
            km = r["total_distance"] / 1000 if r["total_distance"] > 100 else r["total_distance"]
            distance = f"{km:.2f} km"
        
        print(f"{r['filename']:<30} {str(r['sport'] or '?'):<15} {str(r['sub_sport'] or '?'):<15} "
              f"{duration:<12} {distance:<12} {str(r['avg_heart_rate'] or '?'):<8} "
              f"{str(r['max_heart_rate'] or '?'):<8} {str(r['total_calories'] or '?'):<10}")
    
    # ── Sport Type Distribution ──
    print("\n" + "─" * 80)
    print("SPORT TYPE DISTRIBUTION")
    print("─" * 80)
    sport_counts = defaultdict(int)
    for r in all_results:
        sport_key = f"{r['sport'] or 'unknown'} / {r['sub_sport'] or 'generic'}"
        sport_counts[sport_key] += 1
    for sport, count in sorted(sport_counts.items()):
        print(f"  {sport}: {count} files")
    
    # ── Message Types Found ──
    print("\n" + "─" * 80)
    print("MESSAGE TYPES FOUND (across all files)")
    print("─" * 80)
    all_msg_types = defaultdict(int)
    for r in all_results:
        for msg_type, info in r["message_types"].items():
            all_msg_types[msg_type] += info["count"]
    
    for msg_type, count in sorted(all_msg_types.items(), key=lambda x: -x[1]):
        print(f"  {msg_type}: {count} records total")
    
    # ── Detailed Field Report Per Message Type ──
    # Focus on the most important message types for coaching
    important_types = ["session", "record", "lap", "sport", "activity", "event", 
                       "device_info", "hrv", "hr", "stress", "respiration_rate",
                       "sleep_level", "monitoring", "workout", "workout_step",
                       "training_file", "hr_zone", "power_zone", "speed_zone"]
    
    print("\n" + "─" * 80)
    print("DETAILED FIELD REPORT (key message types)")
    print("─" * 80)
    
    # Collect all fields per message type across all files
    global_fields = defaultdict(lambda: defaultdict(lambda: {
        "files_present": 0,
        "total_records": 0,
        "sample_values": [],
        "units": None,
    }))
    
    for r in all_results:
        for msg_type, info in r["message_types"].items():
            for field_name, field_info in info["fields"].items():
                gf = global_fields[msg_type][field_name]
                gf["files_present"] += 1
                gf["total_records"] += field_info["count"]
                if field_info["units"]:
                    gf["units"] = field_info["units"]
                for sv in field_info["sample_values"]:
                    if len(gf["sample_values"]) < 3:
                        gf["sample_values"].append(sv)
    
    for msg_type in sorted(global_fields.keys()):
        if msg_type not in important_types and msg_type not in ["unknown_61", "unknown_140", "unknown_147"]:
            # Still show it but mark as secondary
            pass
        
        fields = global_fields[msg_type]
        print(f"\n  📋 {msg_type.upper()} ({len(fields)} fields)")
        print(f"  {'Field':<35} {'Files':<8} {'Records':<10} {'Units':<12} {'Sample Values'}")
        print(f"  {'-'*100}")
        
        for field_name in sorted(fields.keys()):
            fi = fields[field_name]
            samples = str(fi["sample_values"][:2])
            if len(samples) > 50:
                samples = samples[:50] + "..."
            print(f"  {field_name:<35} {fi['files_present']:<8} {fi['total_records']:<10} "
                  f"{str(fi['units'] or ''):<12} {samples}")
    
    # ── Critical Fields for Coaching ──
    print("\n" + "─" * 80)
    print("CRITICAL FIELDS FOR COACHING — AVAILABILITY MATRIX")
    print("─" * 80)
    
    coaching_fields = {
        "Heart Rate (per record)": ("record", "heart_rate"),
        "Heart Rate (avg session)": ("session", "avg_heart_rate"),
        "Heart Rate (max session)": ("session", "max_heart_rate"),
        "Speed/Pace": ("record", "speed"),
        "Cadence": ("record", "cadence"),
        "Distance": ("session", "total_distance"),
        "Duration": ("session", "total_elapsed_time"),
        "Calories": ("session", "total_calories"),
        "Elevation (ascent)": ("session", "total_ascent"),
        "Elevation (descent)": ("session", "total_descent"),
        "GPS (latitude)": ("record", "position_lat"),
        "GPS (longitude)": ("record", "position_long"),
        "Altitude": ("record", "altitude"),
        "Temperature": ("record", "temperature"),
        "Power (cycling)": ("record", "power"),
        "Stroke count (swim)": ("record", "total_strokes"),
        "Stroke type (swim)": ("record", "stroke_type"),  
        "Pool length": ("session", "pool_length"),
        "HRV data": ("hrv", "time"),
        "Training Effect (aerobic)": ("session", "total_training_effect"),
        "Training Effect (anaerobic)": ("session", "total_anaerobic_training_effect"),
        "VO2max estimate": ("session", "enhanced_avg_respiration_rate"),
        "HR Zones": ("hr_zone", "high_bpm"),
        "Workout Steps": ("workout_step", "duration_value"),
    }
    
    print(f"  {'Coaching Field':<35} {'Available?':<12} {'In Files':<10} {'Sample'}")
    print(f"  {'-'*80}")
    
    for label, (msg_type, field_name) in coaching_fields.items():
        if msg_type in global_fields and field_name in global_fields[msg_type]:
            fi = global_fields[msg_type][field_name]
            status = "✅ YES"
            files = f"{fi['files_present']}/{len(all_results)}"
            sample = str(fi["sample_values"][:1])[:30]
        else:
            status = "❌ NO"
            files = "0"
            sample = ""
        print(f"  {label:<35} {status:<12} {files:<10} {sample}")
    
    # ── Missing Critical Data ──
    print("\n" + "─" * 80)
    print("⚠️  DATA NOT IN FIT FILES (must come from COROS scraper)")
    print("─" * 80)
    missing = [
        "HRV (resting / daily measurement)",
        "Sleep duration",
        "Sleep quality / stages",
        "Recovery score",
        "Resting heart rate (daily)",
        "Body battery / energy level",
        "SpO2 measurements",
        "Stress level",
        "Training load (COROS proprietary)",
    ]
    for item in missing:
        # Check if any related message type exists
        print(f"  ❌ {item} — must be scraped from COROS Training Hub")
    
    # ── Errors ──
    errors = [(r["filename"], e) for r in all_results for e in r["errors"]]
    if errors:
        print("\n" + "─" * 80)
        print("⚠️  PARSING ERRORS")
        print("─" * 80)
        for fname, err in errors:
            print(f"  {fname}: {err}")
    
    print("\n" + "=" * 80)
    print("END OF AUDIT REPORT")
    print("=" * 80)
    
    return global_fields


def main():
    if not FIT_DIR.exists():
        print(f"ERROR: Directory not found: {FIT_DIR}")
        sys.exit(1)
    
    fit_files = sorted(FIT_DIR.glob("*.fit"))
    if not fit_files:
        print(f"ERROR: No .fit files found in {FIT_DIR}")
        sys.exit(1)
    
    print(f"Found {len(fit_files)} FIT files in {FIT_DIR}\n")
    
    all_results = []
    for filepath in fit_files:
        print(f"  Parsing {filepath.name} ({filepath.stat().st_size:,} bytes)...")
        result = parse_fit_file(filepath)
        all_results.append(result)
    
    print(f"\nAll {len(fit_files)} files parsed successfully.\n")
    
    global_fields = generate_report(all_results)
    
    # Also save raw results as JSON for further analysis
    output_path = Path(__file__).parent.parent / "scripts" / "fit_audit_raw.json"
    
    # Convert defaultdicts to regular dicts for JSON serialization
    json_results = []
    for r in all_results:
        jr = {k: v for k, v in r.items() if k != "message_types"}
        jr["message_types"] = {}
        for msg_type, info in r["message_types"].items():
            jr["message_types"][msg_type] = {
                "count": info["count"],
                "fields": {
                    fname: {
                        "count": finfo["count"],
                        "sample_values": [str(sv) for sv in finfo["sample_values"]],
                        "units": finfo["units"],
                    }
                    for fname, finfo in info["fields"].items()
                }
            }
        json_results.append(jr)
    
    with open(output_path, "w") as f:
        json.dump(json_results, f, indent=2, default=str)
    
    print(f"\nRaw audit data saved to: {output_path}")


if __name__ == "__main__":
    main()
