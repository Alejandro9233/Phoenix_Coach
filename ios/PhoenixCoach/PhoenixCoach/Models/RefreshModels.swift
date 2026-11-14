import Foundation

// MARK: - Pull to Refresh

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
