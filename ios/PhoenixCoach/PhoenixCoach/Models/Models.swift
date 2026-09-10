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
    var activities: [Activity]
    let recovery: [RecoverySnapshot]
    let personal: PersonalSummary?
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
    /// "yyyy-MM-dd" — drives the race marker on the Recent tab's fitness chart.
    let raceDate: String?

    enum CodingKeys: String, CodingKey {
        case id, name
        case vo2Max = "vo2_max"
        case hrRest = "hr_rest"
        case hrMax = "hr_max"
        case thresholdPaceMinKm = "threshold_pace_min_km"
        case hrvBaseline = "hrv_baseline"
        case staminaLevel = "stamina_level"
        case raceDate = "race_date"
    }
}

struct Injury: Codable, Identifiable {
    var id: Int?
    var dateReported: String?
    var bodyPart: String?
    var status: String?
    var severity: Int?
    var notes: String?
    var affectedSports: String?
    /// "2026-09-20", or nil meaning the injury blocks training until it is
    /// resolved by hand. `get_active_injuries` flips a row past this date to
    /// Recovering, which stops it constraining the plan — so it is the
    /// difference between a restriction that lifts itself and one that waits
    /// for the athlete.
    var expectedRecoveryDate: String?

    enum CodingKeys: String, CodingKey {
        case id
        case dateReported = "date_reported"
        case bodyPart = "body_part"
        case status
        case severity
        case notes
        case affectedSports = "affected_sports"
        case expectedRecoveryDate = "expected_recovery_date"
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
    /// IANA identifier for the device's current timezone, e.g. "America/Hermosillo".
    /// Stamped automatically on save — the backend uses it to decide what "today" is.
    var timezone: String?
    var prediction: RacePrediction?

    enum CodingKeys: String, CodingKey {
        case name, age, timezone, prediction
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
    let subSport: String?
    let startTime: String?
    let durationSec: Double?
    let distanceM: Double?
    let avgHr: Int?
    let maxHr: Int?
    let trainingLoad: Double?
    let avgPowerWatts: Double?
    let totalAscentM: Double?
    let cadence: Int?
    let calories: Int?
    let avg_cadence_scraped: Int?
    let calories_scraped: Int?
    
    enum CodingKeys: String, CodingKey {
        case id, sport
        case subSport = "sub_sport"
        case startTime = "start_time"
        case durationSec = "duration_sec"
        case distanceM = "distance_m"
        case avgHr = "avg_hr"
        case maxHr = "max_hr"
        case trainingLoad = "training_load"
        case avgPowerWatts = "avg_power_watts"
        case totalAscentM = "total_ascent_m"
        case cadence, calories
        case avg_cadence_scraped, calories_scraped
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
        
        let fractionalFormatter = ISO8601DateFormatter()
        fractionalFormatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        if let d = fractionalFormatter.date(from: time) { return d }
        
        if let d = ISO8601DateFormatter().date(from: time) { return d }
        
        let naiveFormatter = DateFormatter()
        naiveFormatter.dateFormat = "yyyy-MM-dd'T'HH:mm:ss"
        return naiveFormatter.date(from: time)
    }

    var sportIcon: String {
        PhoenixCoach.sportIcon(for: sport)
    }
}

// MARK: - Global Sport Icon Helper

func sportIcon(for sport: String?) -> String {
    switch sport?.lowercased() {
    case "run", "running", "trail_running":
        return "figure.run"
    case "swim", "swimming", "open_water_swimming":
        return "figure.pool.swim"
    case "bike", "ride", "cycling", "indoor_cycling":
        return "figure.outdoor.cycle"
    case "strength", "strng", "training", "gym":
        return "figure.strengthtraining.traditional"
    case "rest", "recovery":
        return "bed.double.fill"
    default:
        return "figure.run"
    }
}

struct ActivityAnalysis: Codable {
    let analysis: String
    let rating: String
    let advice: String
    /// Personal-model numbers (backend/services/personal_model.py). Nil when
    /// no baseline exists yet or the run wasn't a steady outdoor run — the
    /// view omits them rather than rendering a placeholder.
    let hrResidualBpm: Double?
    let hrResidualLabel: String?
    let intensity: IntensityZone?

    enum CodingKeys: String, CodingKey {
        case analysis, rating, advice, intensity
        case hrResidualBpm = "hr_residual_bpm"
        case hrResidualLabel = "hr_residual_label"
    }
}

struct IntensityZone: Codable {
    let zone: String?
    let label: String?
    let pctLthr: Int?

    enum CodingKeys: String, CodingKey {
        case zone, label
        case pctLthr = "pct_lthr"
    }
}

/// Recent tab: easy long runs in the last N weeks. The one marathon-readiness number.
struct LongRunLedger: Codable {
    let weeks: Int?
    let minKm: Double?
    let longRuns: Int?
    let easyLongRuns: Int?
    let lastEasyLongRun: String?

    enum CodingKeys: String, CodingKey {
        case weeks
        case minKm = "min_km"
        case longRuns = "long_runs"
        case easyLongRuns = "easy_long_runs"
        case lastEasyLongRun = "last_easy_long_run"
    }
}

struct PersonalSummary: Codable {
    let ledger: LongRunLedger?
    let modelRuns: Int?

    enum CodingKeys: String, CodingKey {
        case ledger
        case modelRuns = "model_runs"
    }
}

/// Profile: Riegel prediction for the goal race from the athlete's own race efforts.
struct RacePrediction: Codable {
    let basisKm: Double?
    let basisTime: String?
    let basisDate: String?
    let distance: String?
    let predicted: String?
    let predictedSec: Int?
    /// Riegel band around `predicted`. The point estimate is the midpoint, not
    /// a promise — the formula assumes you are already trained for the target
    /// distance, which a first marathoner is not.
    let predictedLo: String?
    let predictedHi: String?
    let target: String?
    let gapPct: Double?

    enum CodingKeys: String, CodingKey {
        case distance, predicted, target
        case basisKm = "basis_km"
        case basisTime = "basis_time"
        case basisDate = "basis_date"
        case predictedSec = "predicted_sec"
        case predictedLo = "predicted_lo"
        case predictedHi = "predicted_hi"
        case gapPct = "gap_pct"
    }
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
    var isError: Bool = false
    /// Set when the backend detected an injury report in this exchange. Renders
    /// a confirmation card under the reply. Nothing is written server-side until
    /// the athlete confirms it.
    var proposal: IssueProposal? = nil
    /// Set when the backend matched this message to a recovered injury. Renders
    /// the mirror card: resolve the injury, rebuild the days it turned to rest.
    var recovery: RecoveryProposal? = nil
    /// Set when the backend read this message as travel days. Renders the card
    /// that rests those days and rebuilds the open week around them.
    var travel: TravelProposal? = nil
    /// Outcome text once a card has been acted on, so the card collapses to a
    /// receipt instead of staying tappable forever.
    var proposalOutcome: String? = nil

    enum Role {
        case user, coach
    }
}

// MARK: - Injury / soreness triage (POST /coach/issue/preview, /coach/issue/apply)

struct IssueProposal: Codable, Equatable {
    let issue: ReportedIssue
    let coachNote: String?
    let affectedDays: [AffectedDay]
    let windowEnd: String?

    enum CodingKeys: String, CodingKey {
        case issue
        case coachNote = "coach_note"
        case affectedDays = "affected_days"
        case windowEnd = "window_end"
    }
}

struct ReportedIssue: Codable, Equatable {
    var bodyPart: String
    var severity: Int
    var affectedSports: [String]
    var durationDays: Int
    var notes: String?

    enum CodingKeys: String, CodingKey {
        case bodyPart = "body_part"
        case severity
        case affectedSports = "affected_sports"
        case durationDays = "duration_days"
        case notes
    }
}

struct AffectedDay: Codable, Equatable, Identifiable {
    let day: String
    let date: String
    let isToday: Bool
    let blockedWorkouts: [BlockedWorkout]
    let options: [IssueOption]
    let recommendedOption: String

    var id: String { day }

    enum CodingKeys: String, CodingKey {
        case day, date, options
        case isToday = "is_today"
        case blockedWorkouts = "blocked_workouts"
        case recommendedOption = "recommended_option"
    }
}

struct BlockedWorkout: Codable, Equatable {
    let sport: String
    let title: String
    let totalTime: String?

    enum CodingKeys: String, CodingKey {
        case sport, title
        case totalTime = "total_time"
    }
}

struct IssueOption: Codable, Equatable, Identifiable {
    let id: String
    let sport: String
    let label: String
    let detail: String
}

struct IssueApplyResult: Codable {
    let status: String
    let injuryId: Int
    let bodyPart: String
    let restDays: [String]
    let swappedDays: [String]

    enum CodingKeys: String, CodingKey {
        case status
        case injuryId = "injury_id"
        case bodyPart = "body_part"
        case restDays = "rest_days"
        case swappedDays = "swapped_days"
    }
}

// MARK: - Injury recovery (chat card + POST /coach/recovery/apply)

struct RecoveryProposal: Codable, Equatable {
    let injury: RecoveredInjury
    let rebuildDays: [String]

    enum CodingKeys: String, CodingKey {
        case injury
        case rebuildDays = "rebuild_days"
    }
}

struct RecoveredInjury: Codable, Equatable {
    let id: Int
    let bodyPart: String
    let dateReported: String?
    let affectedSports: [String]
    let severity: Int?

    enum CodingKeys: String, CodingKey {
        case id, severity
        case bodyPart = "body_part"
        case dateReported = "date_reported"
        case affectedSports = "affected_sports"
    }
}

struct RecoveryApplyResult: Codable {
    let status: String
    let injuryId: Int
    let bodyPart: String
    let rebuiltDays: [String]
    /// Set when the injury resolved but the LLM couldn't rebuild the days —
    /// the plan is untouched and the athlete can replan later.
    let rebuildError: String?

    enum CodingKeys: String, CodingKey {
        case status
        case injuryId = "injury_id"
        case bodyPart = "body_part"
        case rebuiltDays = "rebuilt_days"
        case rebuildError = "rebuild_error"
    }
}

// MARK: - Travel triage (chat SSE `travel` key, POST /coach/travel/apply)

struct TravelProposal: Codable, Equatable {
    /// Full day names for this week, e.g. ["Friday", "Saturday"].
    let days: [String]
    /// ISO dates matching `days` — what actually gets POSTed back on Confirm.
    let dates: [String]
    let affectedDays: [TravelAffectedDay]
    let displacedRuns: Int
    let rebuildDays: [String]
    let priorityNote: String

    enum CodingKeys: String, CodingKey {
        case days, dates
        case affectedDays = "affected_days"
        case displacedRuns = "displaced_runs"
        case rebuildDays = "rebuild_days"
        case priorityNote = "priority_note"
    }
}

struct TravelAffectedDay: Codable, Equatable {
    let day: String
    let workouts: [String]
}

struct TravelApplyResult: Codable {
    let status: String
    let travelDays: [String]
    let rebuiltDays: [String]
    /// Set when the days were blocked but the LLM couldn't rebuild the open
    /// remainder — travel still sticks, the athlete replans later.
    let rebuildError: String?

    enum CodingKeys: String, CodingKey {
        case status
        case travelDays = "travel_days"
        case rebuiltDays = "rebuilt_days"
        case rebuildError = "rebuild_error"
    }
}

struct APIChatSession: Codable, Identifiable {
    let id: Int
    let title: String
    let updatedAt: String
    
    enum CodingKeys: String, CodingKey {
        case id, title
        case updatedAt = "updated_at"
    }
}

struct APIChatMessage: Codable, Identifiable {
    let id: Int
    let role: String
    let content: String
    let createdAt: String
    
    enum CodingKeys: String, CodingKey {
        case id, role, content
        case createdAt = "created_at"
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
    /// The frozen refresh event just recorded — byte-identical to the History
    /// feed's row for this refresh (Today's debrief card reads it).
    var event: HistoryEvent?
    var eventRecorded: Bool?

    enum CodingKeys: String, CodingKey {
        case syncStatus = "sync_status"
        case syncMessage = "sync_message"
        case recovery, adaptation, event
        case eventRecorded = "event_recorded"
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

/// State of the backend's deep-refresh job (/smart-refresh/start + /status).
/// `result` is present once `state == "done"`.
struct RefreshJobStatus: Codable {
    let state: String  // idle | running | done | error
    let stage: String
    let result: SmartRefreshResponse?
    let error: String?
}


// MARK: - Context Builder

// ContextBuilder removed since LLM prompts are now handled entirely by the backend

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
