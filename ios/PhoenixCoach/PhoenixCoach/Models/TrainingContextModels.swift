import Foundation

// MARK: - Context Builder

// ContextBuilder removed since LLM prompts are now handled entirely by the backend

// MARK: - Training Context (from /training-context endpoint)

struct TrainingContext: Codable {
    // Everything optional: the engine's `_empty_context` (no athlete / no
    // race) returns a subset, and Today must still render its ring from
    // `recovery` when that happens.
    let currentDate: String?
    let raceDate: String?
    let raceName: String?
    let raceType: String?
    let raceDistance: String?
    let weeksToRace: Int?
    let phase: String?
    let phaseName: String?
    let phaseWeek: Int?
    let phaseTotalWeeks: Int?
    let phasePriorities: String?
    let cycleWeek: Int?
    let isRecoveryWeek: Bool?
    let recoveryNote: String?
    let raceGoals: RaceGoals?
    /// The periodization engine's recovery check — the Today ring.
    let recovery: RecoveryStatus?

    enum CodingKeys: String, CodingKey {
        case currentDate = "current_date"
        case raceDate = "race_date"
        case raceName = "race_name"
        case raceType = "race_type"
        case raceDistance = "race_distance"
        case weeksToRace = "weeks_to_race"
        case phase
        case phaseName = "phase_name"
        case phaseWeek = "phase_week"
        case phaseTotalWeeks = "phase_total_weeks"
        case phasePriorities = "phase_priorities"
        case cycleWeek = "cycle_week"
        case isRecoveryWeek = "is_recovery_week"
        case recoveryNote = "recovery_note"
        case raceGoals = "race_goals"
        case recovery
    }
}

/// `periodization_engine._get_recovery_status`. `status` is green / yellow /
/// red / unknown. `checks` is one tri-state per signal ("pass", "concern",
/// "unknown") set at the same line the engine records a concern, so the
/// ring's segments can never disagree with the word. Every field optional:
/// the no-data branches return subsets.
struct RecoveryStatus: Codable {
    let status: String?
    let detail: String?
    let hrvVsBaseline: String?
    let rhrTrend: String?
    let tib: Double?
    let loadRatio: Double?
    let checks: [String: String]?

    enum CodingKeys: String, CodingKey {
        case status, detail, tib, checks
        case hrvVsBaseline = "hrv_vs_baseline"
        case rhrTrend = "rhr_trend"
        case loadRatio = "load_ratio"
    }

    /// The word in the ring.
    var word: String {
        switch status {
        case "green": return "Ready"
        case "yellow": return "Careful"
        case "red": return "Recover"
        default: return "No data"
        }
    }
}

struct RaceGoals: Codable {
    let targetFinishTime: String?
    
    enum CodingKeys: String, CodingKey {
        case targetFinishTime = "target_finish_time"
    }
}

struct BlockCalendarResponse: Codable {
    let totalWeeks: Int
    let trainingStartDate: String
    let raceDate: String?
    let raceName: String
    let currentWeekNumber: Int
    let weeks: [BlockWeek]
    
    enum CodingKeys: String, CodingKey {
        case totalWeeks = "total_weeks"
        case trainingStartDate = "training_start_date"
        case raceDate = "race_date"
        case raceName = "race_name"
        case currentWeekNumber = "current_week_number"
        case weeks
    }
}

struct CalendarWorkout: Codable, Identifiable {
    var id = UUID()
    let day: String
    let sport: String
    let title: String
    let totalTime: String?
    let hrTarget: String?
    let muscleGroups: [String]?
    let stepsCount: Int?
    let steps: [WorkoutStep]?
    
    var sportIcon: String {
        PhoenixCoach.sportIcon(for: sport)
    }
    
    enum CodingKeys: String, CodingKey {
        case day, sport, title, steps
        case totalTime = "total_time"
        case hrTarget = "hr_target"
        case muscleGroups = "muscle_groups"
        case stepsCount = "steps_count"
    }
}

struct BlockWeek: Codable, Identifiable {
    var id: Int { weekNumber }
    let weekNumber: Int
    let weekStart: String
    let weekEnd: String
    let phase: String
    let phaseName: String
    let cycleWeek: Int
    let isRecoveryWeek: Bool
    let isCurrentWeek: Bool
    let hasPlan: Bool
    let planSummary: String?
    let expectedTotalHours: String?
    let expectedRunKm: String?
    let actualTrainingLoad: Double?
    let workouts: [CalendarWorkout]?
    
    enum CodingKeys: String, CodingKey {
        case weekNumber = "week_number"
        case weekStart = "week_start"
        case weekEnd = "week_end"
        case phase
        case phaseName = "phase_name"
        case cycleWeek = "cycle_week"
        case isRecoveryWeek = "is_recovery_week"
        case isCurrentWeek = "is_current_week"
        case hasPlan = "has_plan"
        case planSummary = "plan_summary"
        case expectedTotalHours = "expected_total_hours"
        case expectedRunKm = "expected_run_km"
        case actualTrainingLoad = "actual_training_load"
        case workouts
    }
}
