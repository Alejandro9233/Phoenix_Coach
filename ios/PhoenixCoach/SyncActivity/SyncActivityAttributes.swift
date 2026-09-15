import ActivityKit
import Foundation

/// The deep-sync Live Activity's data. Compiled into both targets: the app
/// starts, updates and ends the activity (`DeepSyncActivity`); the
/// SyncActivity extension draws it (`SyncActivityLiveActivity`).
///
/// Only the deep tier gets an activity. It is a server-side COROS scrape
/// that runs 30-90 s, longer on a cold instance — long enough to leave the
/// app. The light tier is about a second, below ActivityKit's latency, and
/// stays inline in TodayView's pill.
struct SyncActivityAttributes: ActivityAttributes {
    /// What changes while the job runs. Each server-side stage is one
    /// update; a sync sends a handful, well inside ActivityKit's budget.
    struct ContentState: Codable, Hashable {
        /// The job's current stage, verbatim from the backend
        /// ("Scraping COROS..."), or the final word.
        var stage: String
        /// Index into `steps` of the stage in progress. The strip lights
        /// this one and everything before it.
        var step: Int
        var outcome: Outcome
        /// Set on the final update only. Freezes the elapsed timer where the
        /// job ended instead of letting it tick under "synced".
        var endedAt: Date?
    }

    enum Outcome: String, Codable, Hashable {
        case running
        case synced
        case failed
    }

    /// When the job started. Fixed for the activity's life: the elapsed
    /// timer counts from here on its own, no per-second update needed.
    var startedAt: Date

    /// The four steps the strip shows, in order. Three are the backend
    /// job's `report(...)` stages in `_run_smart_refresh`; the last is the
    /// phone re-reading everything afterwards. "Plan" only runs when
    /// recovery says today's session should change — when it is skipped
    /// the strip simply lights it along with Refresh.
    static let steps = ["Scrape", "Recovery", "Plan", "Refresh"]

    /// Maps a stage string to its step. Matched on prefixes so a reworded
    /// suffix does not break it; a stage nobody here knows keeps the
    /// previous step rather than jumping the strip around.
    static func step(for stage: String, previous: Int) -> Int {
        let s = stage.lowercased()
        if s.hasPrefix("starting") || s.hasPrefix("scraping") || s.hasPrefix("server restarted") { return 0 }
        if s.hasPrefix("evaluating recovery") { return 1 }
        if s.hasPrefix("adapting") { return 2 }
        if s.hasPrefix("refreshing") { return 3 }
        return previous
    }
}
