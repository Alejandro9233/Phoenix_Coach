"""
Data Agent — Summarizes the athlete's current state from the database.
Pure Python, no LLM needed. Produces a compact text summary for the Response Agent.
"""
from datetime import datetime, timedelta
from sqlalchemy.orm import Session
from backend.models.database import Athlete, Activity, RecoverySnapshot, InjuryLog


class DataAgent:
    def __init__(self, db_session: Session):
        self.db = db_session
    
    def summarize(self, lookback_days=14):
        """
        Produce a compact athlete state summary from the database.
        Returns a string suitable for injection into an LLM prompt (~300 tokens).
        """
        athlete = self.db.query(Athlete).first()
        if not athlete:
            return "No athlete profile found."
        
        today = datetime.now().date()
        cutoff = today - timedelta(days=lookback_days)
        
        # Get recent recovery snapshots
        snapshots = self.db.query(RecoverySnapshot).filter(
            RecoverySnapshot.date >= cutoff
        ).order_by(RecoverySnapshot.date.desc()).all()
        
        # Get recent activities
        activities = self.db.query(Activity).filter(
            Activity.start_time >= datetime.combine(cutoff, datetime.min.time())
        ).order_by(Activity.start_time.desc()).all()
        
        # Build the summary
        lines = []
        lines.append(f"ATHLETE STATE ({today.strftime('%B %d, %Y')}):")
        lines.append(f"Name: {athlete.name}")
        
        # Active Injuries
        active_injuries = self.db.query(InjuryLog).filter(
            InjuryLog.athlete_id == athlete.id,
            InjuryLog.status == "Active"
        ).all()
        if active_injuries:
            lines.append("")
            lines.append("ACTIVE INJURIES (CRITICAL):")
            for inj in active_injuries:
                lines.append(f"  - {inj.body_part} (Severity: {inj.severity}/10)")
                if inj.affected_sports:
                    lines.append(f"    Affected sports to avoid/limit: {inj.affected_sports}")
                if inj.notes:
                    lines.append(f"    Notes: {inj.notes}")

        
        # Profile thresholds
        profile_parts = []
        if athlete.vo2_max: profile_parts.append(f"VO2max: {athlete.vo2_max}")
        if athlete.hr_rest: profile_parts.append(f"RHR: {athlete.hr_rest} bpm")
        if athlete.hr_max: profile_parts.append(f"LTHR: {athlete.hr_max} bpm")
        if athlete.threshold_pace_min_km: 
            pace = athlete.threshold_pace_min_km
            mins = int(pace)
            secs = int((pace - mins) * 60)
            profile_parts.append(f"LT Pace: {mins}:{secs:02d}/km")
        if athlete.hrv_baseline: profile_parts.append(f"HRV Baseline: {athlete.hrv_baseline} ms")
        if profile_parts:
            lines.append(f"Profile: {', '.join(profile_parts)}")
        
        # Latest recovery metrics
        if snapshots:
            latest = snapshots[0]
            lines.append("")
            lines.append("CURRENT FITNESS MARKERS:")
            if latest.cti: lines.append(f"  Fitness (CTI): {latest.cti:.0f}")
            if latest.ati: lines.append(f"  Fatigue (ATI): {latest.ati:.0f}")
            if latest.tib: lines.append(f"  Form (TIB): {latest.tib:.0f}")
            if latest.load_ratio: lines.append(f"  Load Ratio: {latest.load_ratio:.2f}")
            if latest.resting_hr: lines.append(f"  Today RHR: {latest.resting_hr} bpm")
            if latest.hrv_ms: lines.append(f"  Today HRV: {latest.hrv_ms:.0f} ms")
            if latest.t7d_load: lines.append(f"  7-day Load: {latest.t7d_load:.0f}")
            if latest.t28d_load: lines.append(f"  28-day Load: {latest.t28d_load:.0f}")
            if latest.recommend_tl_min and latest.recommend_tl_max:
                lines.append(f"  Recommended Load Range: {latest.recommend_tl_min:.0f}-{latest.recommend_tl_max:.0f}")
            
            # Surfaced recovery fields (stored but previously never shown to LLM)
            if latest.performance_index and latest.performance_index > 0:
                lines.append(f"  Stamina Level: {latest.performance_index:.1f}")
            if latest.performance_score is not None and latest.performance_score >= 0:
                perf_labels = {0: "Declining", 1: "Maintaining", 2: "Low", 3: "Good", 4: "Breakthrough", 5: "Peak"}
                lines.append(f"  Performance: {perf_labels.get(latest.performance_score, 'Unknown')}")
            if latest.fatigue_state:
                fatigue_labels = {1: "Very Fresh", 2: "Fresh", 3: "Normal", 4: "Fatigued", 5: "Overreaching"}
                lines.append(f"  Fatigue Zone: {fatigue_labels.get(latest.fatigue_state, 'Unknown')}")
            
            # RHR trend (7-day average)
            rhr_values = [s.resting_hr for s in snapshots[:7] if s.resting_hr]
            if rhr_values:
                avg_rhr = sum(rhr_values) / len(rhr_values)
                lines.append(f"  7-day Avg RHR: {avg_rhr:.0f} bpm")
            
            # HRV trend
            hrv_values = [s.hrv_ms for s in snapshots[:7] if s.hrv_ms]
            if hrv_values:
                avg_hrv = sum(hrv_values) / len(hrv_values)
                lines.append(f"  7-day Avg HRV: {avg_hrv:.0f} ms")
        
        # Recent activities (last 5)
        if activities:
            lines.append("")
            lines.append(f"RECENT ACTIVITIES (last {min(5, len(activities))} of {len(activities)} in {lookback_days} days):")
            for act in activities[:5]:
                date_str = act.start_time.strftime("%b %d") if act.start_time else "?"
                duration_min = act.duration_sec / 60 if act.duration_sec else 0
                dist_km = act.distance_m / 1000 if act.distance_m else 0
                tl = act.training_load or 0
                
                parts = [f"{date_str}: {act.sport}"]
                if dist_km > 0:
                    parts.append(f"{dist_km:.1f}km")
                    # Add pace for distance-based activities
                    if duration_min > 0:
                        pace_min_km = duration_min / dist_km
                        pace_m = int(pace_min_km)
                        pace_s = int((pace_min_km - pace_m) * 60)
                        parts.append(f"Pace:{pace_m}:{pace_s:02d}/km")
                parts.append(f"{duration_min:.0f}min")
                if act.avg_hr:
                    parts.append(f"HR:{act.avg_hr}")
                if act.avg_power_watts and act.avg_power_watts > 0:
                    parts.append(f"Power:{act.avg_power_watts:.0f}W")
                parts.append(f"TL:{tl:.0f}")
                lines.append(f"  {' | '.join(parts)}")
            
            # Weekly volume summary
            week_cutoff = today - timedelta(days=7)
            week_acts = [a for a in activities if a.start_time and a.start_time.date() >= week_cutoff]
            if week_acts:
                total_duration = sum((a.duration_sec or 0) for a in week_acts) / 3600
                total_load = sum((a.training_load or 0) for a in week_acts)
                sport_counts = {}
                for a in week_acts:
                    sport_counts[a.sport] = sport_counts.get(a.sport, 0) + 1
                sport_str = ", ".join(f"{s}:{c}" for s, c in sorted(sport_counts.items()))
                lines.append(f"  This week: {len(week_acts)} sessions, {total_duration:.1f}h, TL:{total_load:.0f} ({sport_str})")
                # Weekly running distance
                total_run_km = sum((a.distance_m or 0) / 1000 for a in week_acts if a.sport == "running")
                if total_run_km > 0:
                    lines.append(f"  Week running km: {total_run_km:.1f}")
        
        # Weekly plan compliance
        try:
            from backend.services.compliance import get_weekly_plan_status
            status = get_weekly_plan_status(self.db)
            if status and status.get("week_progress"):
                wp = status["week_progress"]
                lines.append("")
                lines.append("WEEKLY PLAN ADHERENCE:")
                lines.append(f"  Sessions: {wp['sessions_completed']}/{wp['sessions_planned']} ({wp['completion_pct']}%)")
                lines.append(f"  Hours: {wp['hours_done']}/{wp['hours_planned']}h")
                lines.append(f"  Week TL: {wp['total_training_load']}")
                
                # Note missed/skipped days
                days = status.get("days", {})
                missed = [d for d, info in days.items() 
                         if info.get("actual", {}).get("skipped")]
                if missed:
                    lines.append(f"  ⚠️ Missed: {', '.join(missed)}")
        except Exception:
            pass  # Compliance service may not be available yet
        
        # Alerts
        alerts = self._check_alerts(snapshots, activities)
        if alerts:
            lines.append("")
            lines.append("ALERTS:")
            for alert in alerts:
                lines.append(f"  ⚠️ {alert}")
        
        return "\n".join(lines)
    
    def _check_alerts(self, snapshots, activities):
        """Check for concerning patterns in the data."""
        alerts = []
        
        if len(snapshots) < 3:
            return alerts
        
        # HRV dropping trend
        hrv_values = [s.hrv_ms for s in snapshots[:5] if s.hrv_ms]
        if len(hrv_values) >= 3:
            baseline = snapshots[0].hrv_baseline if snapshots[0].hrv_baseline else None
            if baseline and hrv_values[0]:
                pct_diff = (hrv_values[0] - baseline) / baseline * 100
                if pct_diff < -10:
                    consecutive_low = sum(1 for v in hrv_values if v < baseline * 0.9)
                    if consecutive_low >= 2:
                        alerts.append(f"HRV {pct_diff:.0f}% below baseline for {consecutive_low} consecutive days")
        
        # RHR elevated
        rhr_values = [s.resting_hr for s in snapshots[:7] if s.resting_hr]
        if len(rhr_values) >= 3:
            avg_rhr = sum(rhr_values) / len(rhr_values)
            if rhr_values[0] and rhr_values[0] > avg_rhr + 5:
                alerts.append(f"RHR elevated: {rhr_values[0]} bpm vs 7-day avg {avg_rhr:.0f} bpm")
        
        # Load ratio warning
        if snapshots[0].load_ratio:
            ratio = snapshots[0].load_ratio
            if ratio > 1.5:
                alerts.append(f"Load ratio {ratio:.2f} — HIGH injury risk, reduce training load")
            elif ratio < 0.8:
                alerts.append(f"Load ratio {ratio:.2f} — detraining risk, consider increasing load")
        
        # Negative TIB for too long
        tib_values = [s.tib for s in snapshots[:5] if s.tib is not None]
        if len(tib_values) >= 3:
            consecutive_negative = sum(1 for v in tib_values if v < -15)
            if consecutive_negative >= 3:
                alerts.append(f"Form (TIB) has been below -15 for {consecutive_negative} consecutive days — accumulated fatigue")
        
        return alerts


if __name__ == "__main__":
    import os, sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    
    engine = create_engine("sqlite:///./phoenix_coach.db")
    Session = sessionmaker(bind=engine)
    session = Session()
    
    agent = DataAgent(session)
    print(agent.summarize())
    session.close()
