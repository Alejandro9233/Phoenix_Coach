import Foundation

// MARK: - Dashboard Data (from /dashboard endpoint)

struct DashboardResponse: Codable {
    let athlete: Athlete?
    var activities: [Activity]
    let recovery: [RecoverySnapshot]
    let personal: PersonalSummary?
    /// Recent tab's volume ledger (backend/services/volume_ledger.py). Nil
    /// when the eight-week window holds no training, and decoded leniently:
    /// a shape this build doesn't know hides the card, not the tab.
    let volume: VolumeLedger?

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        athlete = try c.decodeIfPresent(Athlete.self, forKey: .athlete)
        activities = try c.decodeIfPresent([Activity].self, forKey: .activities) ?? []
        recovery = try c.decodeIfPresent([RecoverySnapshot].self, forKey: .recovery) ?? []
        personal = try c.decodeIfPresent(PersonalSummary.self, forKey: .personal)
        volume = try? c.decodeIfPresent(VolumeLedger.self, forKey: .volume)
    }
}

/// Hours per sport per week, eight weeks, oldest first; the last week is
/// the current, partial one. `sports` is the row order; keys of the dicts.
struct VolumeLedger: Codable {
    let weeks: [VolumeWeek]
    let sports: [String]?
    let thisWeek: [String: Double]?
    let avg4wk: [String: Double]?
    let delta4wk: [String: Double]?
    let gone: [VolumeGap]?

    enum CodingKeys: String, CodingKey {
        case weeks, sports, gone
        case thisWeek = "this_week"
        case avg4wk = "avg_4wk"
        case delta4wk = "delta_4wk"
    }
}

struct VolumeWeek: Codable, Identifiable {
    let start: String
    let hours: [String: Double]
    let total: Double?
    var id: String { start }
}

/// A sport with hours in the window and none in the last two weeks.
struct VolumeGap: Codable {
    let sport: String
    let weeks: Int?
    let was: Double?
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
    /// COROS's own race clock (its fitness model), stored daily by the sync.
    var corosPrediction: CorosPrediction?

    enum CodingKeys: String, CodingKey {
        case name, age, timezone, prediction
        case corosPrediction = "coros_prediction"
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
    /// COROS's set count for a strength session (0 on rows the old scraper wrote).
    let sets: Int?
    /// Laps compacted at sync (`activities.lap_data`); nil until the morning
    /// pull backfills the row. See ActivityLaps.
    let lapData: ActivityLaps?

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
        case sets
        case lapData = "lap_data"
    }
    
    var durationFormatted: String {
        guard let sec = durationSec else { return "--" }
        let totalSec = Int(sec)
        let h = totalSec / 3600
        let m = (totalSec % 3600) / 60
        return h > 0 ? "\(h)h \(m)m" : "\(m)m"
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

    /// Sets from the compacted log first (counted per exercise), else COROS's
    /// record field. Zero means "not a strength session" — callers omit it.
    var setCount: Int? {
        if let n = lapData?.sets, n > 0 { return n }
        if let n = sets, n > 0 { return n }
        return nil
    }

    /// The exercise log, only when the row carries one.
    var strengthLog: ActivityLaps? {
        guard let laps = lapData, laps.kind == "strength", !(laps.exercises ?? []).isEmpty else { return nil }
        return laps
    }
}

/// `activities.lap_data`, compacted once at sync (backend/services/coros_mcp.py).
/// Runs carry km splits with HR drift and pace fade; strength sessions carry
/// the exercise log; `{}` is "fetched, nothing usable". Every field decodes
/// leniently so a shape this build doesn't know can't take the Recent tab
/// down with it.
struct ActivityLaps: Codable {
    let kind: String?
    let exercises: [StrengthExercise]?
    let sets: Int?
    let reps: Int?
    let workS: Int?
    let restS: Int?
    let avgHrWork: Int?
    let hrDriftBpm: Double?
    let paceFadeS: Double?
    let fullKm: Int?

    enum CodingKeys: String, CodingKey {
        case kind, exercises, sets, reps
        case workS = "work_s"
        case restS = "rest_s"
        case avgHrWork = "avg_hr_work"
        case hrDriftBpm = "hr_drift_bpm"
        case paceFadeS = "pace_fade_s"
        case fullKm = "full_km"
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        kind = try? c.decodeIfPresent(String.self, forKey: .kind)
        exercises = try? c.decodeIfPresent([StrengthExercise].self, forKey: .exercises)
        sets = Self.int(c, .sets)
        reps = Self.int(c, .reps)
        workS = Self.int(c, .workS)
        restS = Self.int(c, .restS)
        avgHrWork = Self.int(c, .avgHrWork)
        hrDriftBpm = try? c.decodeIfPresent(Double.self, forKey: .hrDriftBpm)
        paceFadeS = try? c.decodeIfPresent(Double.self, forKey: .paceFadeS)
        fullKm = Self.int(c, .fullKm)
    }

    /// Python's `round()` writes ints, but a float in the same slot must not
    /// fail the whole row.
    static func int(_ c: KeyedDecodingContainer<CodingKeys>, _ key: CodingKeys) -> Int? {
        (try? c.decodeIfPresent(Double.self, forKey: key)).map { Int($0.rounded()) }
    }
}

/// One exercise block of a strength session: the watch's per-set reps, the
/// block's work and rest seconds, and the working HR.
struct StrengthExercise: Codable {
    let name: String
    let sets: Int?
    let reps: Int?
    let setReps: [Int]?
    let workS: Int?
    let restS: Int?
    let avgHr: Int?
    let maxHr: Int?

    enum CodingKeys: String, CodingKey {
        case name, sets, reps
        case setReps = "set_reps"
        case workS = "work_s"
        case restS = "rest_s"
        case avgHr = "avg_hr"
        case maxHr = "max_hr"
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        name = (try? c.decodeIfPresent(String.self, forKey: .name)) ?? "Exercise"
        sets = Self.int(c, .sets)
        reps = Self.int(c, .reps)
        setReps = (try? c.decodeIfPresent([Double].self, forKey: .setReps)).map { $0.map { Int($0.rounded()) } }
        workS = Self.int(c, .workS)
        restS = Self.int(c, .restS)
        avgHr = Self.int(c, .avgHr)
        maxHr = Self.int(c, .maxHr)
    }

    static func int(_ c: KeyedDecodingContainer<CodingKeys>, _ key: CodingKeys) -> Int? {
        (try? c.decodeIfPresent(Double.self, forKey: key)).map { Int($0.rounded()) }
    }

    /// "4 × 8" when every set matched, "3 × 7·9·5" when they didn't (a 0 is a
    /// set the watch couldn't count), "3 sets" when it counted none.
    var setsLabel: String {
        let reps = setReps ?? []
        let n = sets ?? reps.count
        if let first = reps.first, first > 0, reps.allSatisfy({ $0 == first }) { return "\(n) × \(first)" }
        if !reps.isEmpty { return "\(n) × " + reps.map(String.init).joined(separator: "·") }
        return "\(n) sets"
    }
}
