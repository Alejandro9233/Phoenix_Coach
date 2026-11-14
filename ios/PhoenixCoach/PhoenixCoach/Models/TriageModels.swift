import Foundation

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
