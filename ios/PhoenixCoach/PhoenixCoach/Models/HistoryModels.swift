import Foundation

// MARK: - History feed (GET /history)
//
// One merged, newest-first ledger: "refresh" events (frozen at sync time) and
// "plan_change" events (derived from each week's plan receipts). One flat
// Codable covers both types — every field optional, switch on `type`.

struct HistoryFeedResponse: Codable {
    let events: [HistoryEvent]
    let nextBefore: String?

    enum CodingKeys: String, CodingKey {
        case events
        case nextBefore = "next_before"
    }
}

struct HistoryEvent: Codable, Identifiable {
    var id: String { eventId ?? at ?? UUID().uuidString }

    let eventId: String?
    let type: String?          // "refresh" | "plan_change"
    let at: String?            // athlete-local ISO with offset
    let localDay: String?      // frozen grouping day "2026-08-25"

    // refresh
    let syncStatus: String?    // "ok" | "partial"
    let syncMessage: String?
    let newActivityCount: Int?
    let newActivities: [HistoryActivity]?
    let recovery: RecoverySummary?
    let recoveryStale: Bool?
    let staleReason: String?
    let recoveryDelta: RecoveryDelta?
    let triggers: [RefreshTrigger]?
    let adaptation: HistoryAdaptation?
    let weekAfter: WeekAfter?

    // plan_change
    let weekStart: String?
    let source: String?
    let days: [String]?
    let reason: String?
    let before: [String: [ReceiptWorkout]]?
    let after: [String: [ReceiptWorkout]]?
    let afterSource: String?   // "receipt" | "reconstructed" | "current"
    let stripped: [StrippedWorkout]?

    enum CodingKeys: String, CodingKey {
        case eventId = "id"
        case type, at, recovery, triggers, adaptation, days, reason
        case before, after, stripped, source
        case localDay = "local_day"
        case syncStatus = "sync_status"
        case syncMessage = "sync_message"
        case newActivityCount = "new_activity_count"
        case newActivities = "new_activities"
        case recoveryStale = "recovery_stale"
        case staleReason = "stale_reason"
        case recoveryDelta = "recovery_delta"
        case weekAfter = "week_after"
        case weekStart = "week_start"
        case afterSource = "after_source"
    }
}

struct HistoryActivity: Codable, Identifiable {
    var id: String { activityId ?? UUID().uuidString }
    let activityId: String?
    let sport: String?
    let startTime: String?
    let distanceKm: Double?
    let durationMin: Int?
    let avgHr: Int?
    let compliance: HistoryCompliance?

    enum CodingKeys: String, CodingKey {
        case activityId = "activity_id"
        case sport, compliance
        case startTime = "start_time"
        case distanceKm = "distance_km"
        case durationMin = "duration_min"
        case avgHr = "avg_hr"
    }
}

struct HistoryCompliance: Codable {
    let workoutTitle: String?
    let score: Int?
    let status: String?        // completed | partial | mismatch
    let durationPct: Int?
    let hrOnTarget: Bool?
    let distancePct: Int?
    let notes: String?

    enum CodingKeys: String, CodingKey {
        case workoutTitle = "workout_title"
        case score, status, notes
        case durationPct = "duration_pct"
        case hrOnTarget = "hr_on_target"
        case distancePct = "distance_pct"
    }
}

struct RecoveryDelta: Codable {
    let hrvMs: Double?
    let restingHr: Double?
    let tib: Double?

    enum CodingKeys: String, CodingKey {
        case hrvMs = "hrv_ms"
        case restingHr = "resting_hr"
        case tib
    }
}

struct RefreshTrigger: Codable, Identifiable {
    var id: String { name ?? UUID().uuidString }
    let name: String?
    let fired: Bool?
    let value: Double?
    let threshold: Double?

    /// Display label for the deterministic trigger names.
    var label: String {
        switch name {
        case "hrv_drop": return "HRV vs baseline"
        case "rhr_elevated": return "Resting HR"
        case "fatigue_high": return "Fatigue zone"
        case "load_ratio_high": return "Load ratio"
        case "tib_low": return "Form (TIB)"
        default: return name ?? "—"
        }
    }
}

struct HistoryAdaptation: Codable {
    let needed: Bool?
    let adapted: Bool?
    let reasons: [String]?
    let error: String?
    let weekStart: String?
    let receiptAt: String?
    /// The adapt_today receipt this refresh caused, folded in by the feed.
    let receipt: PlanReceipt?

    enum CodingKeys: String, CodingKey {
        case needed, adapted, reasons, error, receipt
        case weekStart = "week_start"
        case receiptAt = "receipt_at"
    }
}

struct PlanReceipt: Codable {
    let at: String?
    let source: String?
    let days: [String]?
    let reason: String?
    let before: [String: [ReceiptWorkout]]?
    let after: [String: [ReceiptWorkout]]?
    let stripped: [StrippedWorkout]?
}

struct WeekAfter: Codable {
    let runKmDone: Double?
    let runKmTarget: Double?
    let sessionsCompleted: Int?
    let sessionsPlanned: Int?

    enum CodingKeys: String, CodingKey {
        case runKmDone = "run_km_done"
        case runKmTarget = "run_km_target"
        case sessionsCompleted = "sessions_completed"
        case sessionsPlanned = "sessions_planned"
    }
}

struct ReceiptWorkout: Codable, Identifiable {
    var id: String { "\(sport ?? "")-\(title ?? "")-\(totalTime ?? "")" }
    let sport: String?
    let title: String?
    let totalTime: String?

    enum CodingKeys: String, CodingKey {
        case sport, title
        case totalTime = "total_time"
    }
}

struct StrippedWorkout: Codable, Identifiable {
    var id: String { "\(day ?? "")-\(title ?? "")" }
    let day: String?
    let title: String?
    let reason: String?
}
