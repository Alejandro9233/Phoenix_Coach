import Foundation

// MARK: - Coaching Recommendation (from /coaching endpoint)

struct CoachingRecommendation: Codable, Identifiable {
    var id = UUID()
    let summary: String
    let workouts: [Workout]?
    let rationale: String?
    let adaptation: String?
    let coachNote: String?
    let athleteSummary: String?
    
    enum CodingKeys: String, CodingKey {
        case summary, workouts, rationale, adaptation
        case coachNote = "coach_note"
        case athleteSummary = "athlete_summary"
    }
    
    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        self.summary = try container.decode(String.self, forKey: .summary)
        self.workouts = try container.decodeIfPresent([Workout].self, forKey: .workouts)
        self.rationale = try container.decodeIfPresent(String.self, forKey: .rationale)
        self.adaptation = try container.decodeIfPresent(String.self, forKey: .adaptation)
        self.coachNote = try container.decodeIfPresent(String.self, forKey: .coachNote)
        self.athleteSummary = try container.decodeIfPresent(String.self, forKey: .athleteSummary)
    }
}

struct WeekSummary: Codable {
    let focus: String
    let rationale: String
    let expectedTotalHours: Double?
    let expectedRunKm: Double?
    
    enum CodingKeys: String, CodingKey {
        case focus, rationale
        case expectedTotalHours = "expected_total_hours"
        case expectedRunKm = "expected_run_km"
    }
}

struct DayPlan: Codable {
    let summary: String
    let workouts: [Workout]?
    let rationale: String?
    let coachNote: String?
    let adaptation: String?
    let originalWorkouts: [Workout]?
    
    enum CodingKeys: String, CodingKey {
        case summary, workouts, rationale, adaptation
        case coachNote = "coach_note"
        case originalWorkouts = "original_workouts"
    }
}

struct WeeklyPlanResponse: Codable {
    let weekSummary: WeekSummary?
    let days: [String: DayPlan]
    let weeklyReview: String?
    
    enum CodingKeys: String, CodingKey {
        case weekSummary = "week_summary"
        case days
        case weeklyReview = "weekly_review"
    }
}

// MARK: - Weekly Plan Status (from /weekly-plan/status endpoint)

struct WorkoutCompliance: Codable {
    let workoutTitle: String?
    let plannedSport: String?
    let score: Int
    let status: String  // "completed", "partial", "mismatch", "missed", "pending"
    let durationPct: Int?
    let hrOnTarget: Bool?
    let notes: String?
    
    enum CodingKeys: String, CodingKey {
        case workoutTitle = "workout_title"
        case plannedSport = "planned_sport"
        case score, status, notes
        case durationPct = "duration_pct"
        case hrOnTarget = "hr_on_target"
    }
}

struct ActualActivity: Codable, Identifiable {
    var id: String { activityId ?? UUID().uuidString }
    let activityId: String?
    let sport: String?
    let durationMin: Double?
    let distanceKm: Double?
    let avgHr: Int?
    let maxHr: Int?
    let trainingLoad: Double?
    let startTime: String?
    
    enum CodingKeys: String, CodingKey {
        case activityId = "id"
        case sport
        case durationMin = "duration_min"
        case distanceKm = "distance_km"
        case avgHr = "avg_hr"
        case maxHr = "max_hr"
        case trainingLoad = "training_load"
        case startTime = "start_time"
    }
}

struct DayActual: Codable {
    let completed: Bool
    let skipped: Bool
    let isRest: Bool
    let isPast: Bool
    let isToday: Bool
    let isFuture: Bool
    let activities: [ActualActivity]
    let extraActivities: [ActualActivity]?
    let compliance: [WorkoutCompliance]
    
    enum CodingKeys: String, CodingKey {
        case completed, skipped, compliance, activities
        case isRest = "is_rest"
        case isPast = "is_past"
        case isToday = "is_today"
        case isFuture = "is_future"
        case extraActivities = "extra_activities"
    }
}

struct DayPlanWithActual: Codable {
    let summary: String
    let workouts: [Workout]?
    let rationale: String?
    let coachNote: String?
    let adaptation: String?
    let actual: DayActual?
    let originalWorkouts: [Workout]?
    
    enum CodingKeys: String, CodingKey {
        case summary, workouts, rationale, adaptation, actual
        case coachNote = "coach_note"
        case originalWorkouts = "original_workouts"
    }
}

struct WeekProgress: Codable {
    let sessionsCompleted: Int
    let sessionsPlanned: Int
    let completionPct: Int
    let hoursDone: Double
    let hoursPlanned: Double
    let totalTrainingLoad: Int
    
    enum CodingKeys: String, CodingKey {
        case sessionsCompleted = "sessions_completed"
        case sessionsPlanned = "sessions_planned"
        case completionPct = "completion_pct"
        case hoursDone = "hours_done"
        case hoursPlanned = "hours_planned"
        case totalTrainingLoad = "total_training_load"
    }
}

struct WeeklyPlanStatusResponse: Codable {
    let weekSummary: WeekSummary?
    let days: [String: DayPlanWithActual]
    let weekProgress: WeekProgress?
    
    enum CodingKeys: String, CodingKey {
        case weekSummary = "week_summary"
        case days
        case weekProgress = "week_progress"
    }
}


struct Workout: Codable {
    let sport: String
    let title: String
    let steps: [WorkoutStep]
    let totalTime: String?
    let hrTarget: String?
    let muscleGroups: [String]?
    
    enum CodingKeys: String, CodingKey {
        case sport, title, steps
        case totalTime = "total_time"
        case hrTarget = "hr_target"
        case muscleGroups = "muscle_groups"
    }
}

struct WorkoutStep: Codable, Identifiable {
    var id = UUID()
    let type: String
    let duration: String
    let zone: Int?
    let description: String?
    
    enum CodingKeys: String, CodingKey {
        case type, duration, zone, description
    }
    
    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        self.type = try container.decode(String.self, forKey: .type)
        self.description = try container.decodeIfPresent(String.self, forKey: .description)
        
        // Robust duration decoding: supports String, Int, or Double
        if let strDuration = try? container.decode(String.self, forKey: .duration) {
            self.duration = strDuration
        } else if let intDuration = try? container.decode(Int.self, forKey: .duration) {
            self.duration = String(intDuration)
        } else if let doubleDuration = try? container.decode(Double.self, forKey: .duration) {
            self.duration = String(format: "%.0f", doubleDuration)
        } else {
            self.duration = "0"
        }
        
        // Robust zone decoding: supports Int or String representation
        if let intZone = try? container.decodeIfPresent(Int.self, forKey: .zone) {
            self.zone = intZone
        } else if let strZone = try? container.decodeIfPresent(String.self, forKey: .zone), let parsedZone = Int(strZone) {
            self.zone = parsedZone
        } else {
            self.zone = nil
        }
    }
}

// MARK: - Dashboard Data (from /dashboard endpoint)

struct DashboardResponse: Codable {
    let athlete: Athlete?
    let activities: [Activity]
    let recovery: [RecoverySnapshot]
}

struct Athlete: Codable {
    let id: Int?
    let name: String?
    let vo2Max: Double?
    let hrRest: Int?
    let hrMax: Int?
    let thresholdPaceMinKm: Double?
    let hrvBaseline: Double?
    let staminaLevel: Double?
    
    enum CodingKeys: String, CodingKey {
        case id, name
        case vo2Max = "vo2_max"
        case hrRest = "hr_rest"
        case hrMax = "hr_max"
        case thresholdPaceMinKm = "threshold_pace_min_km"
        case hrvBaseline = "hrv_baseline"
        case staminaLevel = "stamina_level"
    }
}

struct AthleteProfile: Codable {
    var name: String?
    var age: Int?
    var weightKg: Double?
    var raceName: String?
    var raceType: String?
    var raceDistance: String?
    var raceDate: String?
    var swimDays: String?
    var bikeDays: String?
    var runDays: String?
    var strengthDays: String?
    var targetFinishTime: String?
    var trainingStartDate: String?
    
    enum CodingKeys: String, CodingKey {
        case name, age
        case weightKg = "weight_kg"
        case raceName = "race_name"
        case raceType = "race_type"
        case raceDistance = "race_distance"
        case raceDate = "race_date"
        case swimDays = "swim_days"
        case bikeDays = "bike_days"
        case runDays = "run_days"
        case strengthDays = "strength_days"
        case targetFinishTime = "target_finish_time"
        case trainingStartDate = "training_start_date"
    }
}


struct Activity: Codable, Identifiable {
    let id: String?
    let sport: String?
    let startTime: String?
    let durationSec: Double?  // Backend sends Float
    let distanceM: Double?
    let avgHr: Int?
    let maxHr: Int?
    let trainingLoad: Double?
    let avgPowerWatts: Double?
    
    enum CodingKeys: String, CodingKey {
        case id, sport
        case startTime = "start_time"
        case durationSec = "duration_sec"
        case distanceM = "distance_m"
        case avgHr = "avg_hr"
        case maxHr = "max_hr"
        case trainingLoad = "training_load"
        case avgPowerWatts = "avg_power_watts"
    }
    
    var durationFormatted: String {
        guard let sec = durationSec else { return "--" }
        let totalSec = Int(sec)
        let h = totalSec / 3600
        let m = (totalSec % 3600) / 60
        return h > 0 ? "\(h)h \(m)m" : "\(m)m"
    }
    
    var distanceFormatted: String {
        guard let m = distanceM, m > 0 else { return "" }
        return String(format: "%.1f km", m / 1000)
    }
    
    var startTimeDate: Date? {
        guard let time = startTime else { return nil }
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        return formatter.date(from: time) ?? ISO8601DateFormatter().date(from: time)
    }

    var sportEmoji: String {
        switch sport?.lowercased() {
        case "running": return "🏃"
        case "cycling": return "🚴"
        case "swimming": return "🏊"
        case "strength", "training": return "🏋️"
        default: return "🏅"
        }
    }
}

struct ActivityAnalysis: Codable {
    let analysis: String
    let rating: String
    let advice: String
}

struct RecoverySnapshot: Codable, Identifiable {
    // The DB uses `date` as the primary key — there is no integer `id` column.
    var id: String { date ?? UUID().uuidString }
    
    let date: String?
    let restingHr: Int?
    let hrvMs: Double?
    let cti: Double?
    let ati: Double?
    let tib: Double?
    let loadRatio: Double?
    
    enum CodingKeys: String, CodingKey {
        case date
        case restingHr = "resting_hr"
        case hrvMs = "hrv_ms"
        case cti, ati, tib
        case loadRatio = "load_ratio"
    }
}

// MARK: - Chat

struct ChatMessage: Identifiable {
    let id = UUID()
    let role: Role
    var content: String
    let timestamp: Date
    
    enum Role {
        case user, coach
    }
}

// MARK: - Feedback

struct StrengthExercise: Codable, Identifiable {
    var id = UUID()
    var name: String = ""
    var sets: Int = 3
    var reps: Int = 10
    var weightKg: Double?
    
    enum CodingKeys: String, CodingKey {
        case name, sets, reps
        case weightKg = "weight_kg"
    }
}

struct FeedbackEntry: Codable {
    var rpe: Int = 5
    var motivation: Int = 3
    var soreness: Int = 2
    var sleepQuality: Int = 3
    var notes: String = ""
    var strengthExercises: [StrengthExercise] = []
    
    enum CodingKeys: String, CodingKey {
        case rpe, motivation, soreness, notes
        case sleepQuality = "sleep_quality"
        case strengthExercises = "strength_exercises"
    }
}

// MARK: - Pull to Refresh

struct SyncResponse: Codable {
    let syncStatus: String
    let syncMessage: String
    let coaching: CoachingRecommendation
    
    enum CodingKeys: String, CodingKey {
        case syncStatus = "sync_status"
        case syncMessage = "sync_message"
        case coaching
    }
}

// MARK: - Smart Refresh (new single-action refresh flow)

struct SmartRefreshResponse: Codable {
    let syncStatus: String
    let syncMessage: String
    let recovery: RecoverySummary
    let adaptation: AdaptationResult
    
    enum CodingKeys: String, CodingKey {
        case syncStatus = "sync_status"
        case syncMessage = "sync_message"
        case recovery, adaptation
    }
}

struct RecoverySummary: Codable {
    let hrvMs: Double?
    let restingHr: Int?
    let loadRatio: Double?
    let loadRatioLabel: String?
    let cti: Double?
    let ati: Double?
    let tib: Double?
    let fatigueState: Int?
    let staminaLevel: Double?
    
    enum CodingKeys: String, CodingKey {
        case hrvMs = "hrv_ms"
        case restingHr = "resting_hr"
        case loadRatio = "load_ratio"
        case loadRatioLabel = "load_ratio_label"
        case cti, ati, tib
        case fatigueState = "fatigue_state"
        case staminaLevel = "stamina_level"
    }
}

struct AdaptationResult: Codable {
    let needed: Bool
    let adapted: Bool
    let reasons: [String]
}


// MARK: - Context Builder

struct ContextBuilder {
    static func buildSystemPrompt(profile: AthleteProfile?, dashboard: DashboardResponse?, plan: WeeklyPlanResponse?, todayName: String) -> String {
        var context = "You are Phoenix, an elite triathlon AI coach. You are talking to your athlete directly.\n"
        
        if let p = profile {
            context += "Athlete Profile: Training for \(p.raceName ?? "a race") (\(p.raceDistance ?? "Unknown")).\n"
        }
        
        if let d = dashboard, let todayMetrics = d.recovery.first {
            context += "Today's Recovery Metrics: HRV is \(todayMetrics.hrvMs ?? 0)ms, Resting HR is \(todayMetrics.restingHr ?? 0) bpm, Training Balance (TIB) is \(todayMetrics.tib ?? 0).\n"
        }
        
        if let p = plan {
            if let sum = p.weekSummary {
                context += "This week's focus: \(sum.focus). Target: \(sum.expectedTotalHours ?? 0) hrs.\n"
            }
            if let todayPlan = p.days[todayName] {
                let workoutNames = todayPlan.workouts?.map { $0.title }.joined(separator: " and ") ?? "Rest"
                context += "Today's Plan: \(workoutNames). \(todayPlan.summary)\n"
            }
        }
        
        context += "Rule: Be concise, direct, and conversational. Do not output raw JSON, just speak normally to the athlete."
        return context
    }
}

// MARK: - Training Context (from /training-context endpoint)

struct TrainingContext: Codable {
    let currentDate: String
    let raceDate: String?
    let raceName: String
    let raceType: String
    let raceDistance: String
    let weeksToRace: Int
    let phase: String
    let phaseName: String
    let phaseWeek: Int
    let phaseTotalWeeks: Int
    let phasePriorities: String
    let cycleWeek: Int
    let isRecoveryWeek: Bool
    let recoveryNote: String
    let raceGoals: RaceGoals?
    
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
    
    enum CodingKeys: String, CodingKey {
        case day, sport, title
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
