import Foundation

// MARK: - Weekly Plan

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
    let complianceScore: Int?
    /// Week-to-date run km vs the Python-computed target. Optional — nil on
    /// old backends or profiles without a run range (hide the bar, never
    /// draw a zero-denominator one).
    var runKmDone: Double?
    var runKmTarget: Double?
    var runKmHardCap: Double?

    enum CodingKeys: String, CodingKey {
        case sessionsCompleted = "sessions_completed"
        case sessionsPlanned = "sessions_planned"
        case completionPct = "completion_pct"
        case hoursDone = "hours_done"
        case hoursPlanned = "hours_planned"
        case totalTrainingLoad = "total_training_load"
        case complianceScore = "compliance_score"
        case runKmDone = "run_km_done"
        case runKmTarget = "run_km_target"
        case runKmHardCap = "run_km_hard_cap"
    }
}

struct WeeklyPlanStatusResponse: Codable {
    let weekSummary: WeekSummary?
    let days: [String: DayPlanWithActual]
    let weekProgress: WeekProgress?
    let weeksToRace: Int?
    var race: RaceStatus?

    enum CodingKeys: String, CodingKey {
        case weekSummary = "week_summary"
        case days
        case weekProgress = "week_progress"
        case weeksToRace = "weeks_to_race"
        case race
    }
}

/// The final-two-weeks block on /weekly-plan/status: countdown always,
/// deterministic pacing table when the goal is a running race.
struct RaceStatus: Codable {
    let raceName: String?
    let raceDistance: String?
    let raceDate: String?
    let daysToRace: Int?
    let isRaceWeek: Bool?
    let pacing: RacePacing?

    enum CodingKeys: String, CodingKey {
        case raceName = "race_name"
        case raceDistance = "race_distance"
        case raceDate = "race_date"
        case daysToRace = "days_to_race"
        case isRaceWeek = "is_race_week"
        case pacing
    }
}

struct RacePacing: Codable {
    let target: String?
    let distanceKm: Double?
    let avgPace: String?
    let first5kPace: String?
    let cruisePace: String?
    let splits: [RaceSplit]?
    let waypoints: [RaceWaypoint]?
    let hrCaps: RaceHrCaps?

    enum CodingKeys: String, CodingKey {
        case target
        case distanceKm = "distance_km"
        case avgPace = "avg_pace"
        case first5kPace = "first_5k_pace"
        case cruisePace = "cruise_pace"
        case splits, waypoints
        case hrCaps = "hr_caps"
    }
}

struct RaceSplit: Codable, Identifiable {
    var id: Double { toKm ?? 0 }
    let toKm: Double?
    let split: String?
    let cumulative: String?
    let pace: String?

    enum CodingKeys: String, CodingKey {
        case toKm = "to_km"
        case split, cumulative, pace
    }
}

struct RaceWaypoint: Codable, Identifiable {
    var id: String { label ?? "" }
    let label: String?
    let km: Double?
    let time: String?
}

struct RaceHrCaps: Codable {
    let first10k: Int?
    let to30k: Int?
    let final: Int?

    enum CodingKeys: String, CodingKey {
        case first10k = "first_10k"
        case to30k = "to_30k"
        case final
    }
}


struct Workout: Codable {
    let sport: String
    let title: String
    let steps: [WorkoutStep]
    let totalTime: String?
    let hrTarget: String?
    /// Python-stamped pace band ("4:39-4:49/km", pace_enforcer).
    var paceTarget: String?
    let muscleGroups: [String]?
    /// Python-computed carb/fluid line for long runs (fueling.stamp_fuel).
    var fuel: String?

    var sportIcon: String {
        PhoenixCoach.sportIcon(for: sport)
    }

    enum CodingKeys: String, CodingKey {
        case sport, title, steps, fuel
        case totalTime = "total_time"
        case hrTarget = "hr_target"
        case paceTarget = "pace_target"
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
