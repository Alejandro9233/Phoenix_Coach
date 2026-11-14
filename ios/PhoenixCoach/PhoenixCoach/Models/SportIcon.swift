import Foundation

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

/// Profile: COROS's predicted half and marathon, with the change since the
/// oldest stored value (the sync stores one per day since 2026-10-08) and the
/// gap to the goal when the goal distance is one of the two. Every field
/// optional: a single day has no delta, a triathlon goal has no gap.
struct CorosPrediction: Codable {
    let date: String?
    let half: String?
    let halfSec: Int?
    let marathon: String?
    let marathonSec: Int?
    let sinceDate: String?
    let daysTracked: Int?
    let halfDeltaSec: Int?
    let marathonDeltaSec: Int?
    let goalDistance: String?
    let goalGapSec: Int?
    let goalGapPct: Double?

    enum CodingKeys: String, CodingKey {
        case date, half, marathon
        case halfSec = "half_sec"
        case marathonSec = "marathon_sec"
        case sinceDate = "since_date"
        case daysTracked = "days_tracked"
        case halfDeltaSec = "half_delta_sec"
        case marathonDeltaSec = "marathon_delta_sec"
        case goalDistance = "goal_distance"
        case goalGapSec = "goal_gap_sec"
        case goalGapPct = "goal_gap_pct"
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
