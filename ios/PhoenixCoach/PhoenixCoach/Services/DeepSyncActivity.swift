import ActivityKit
import Foundation
import UIKit

/// Mirrors TodayView's deep sync into a Live Activity (Dynamic Island and
/// lock screen), so the 30-90 s COROS scrape can be watched after leaving
/// the app. Drawn by the SyncActivity extension; the data contract is
/// `SyncActivityAttributes`, compiled into both targets.
///
/// Three calls, all from `performSmartRefresh`: `start` when the job is
/// requested, `update` on every stage the backend reports, `end` with the
/// outcome. The light tier never touches this — one second is below
/// ActivityKit's latency, and the pill already covers it.
///
/// The catch: the phone drives every update, and iOS suspends a backgrounded
/// app within seconds. Suspended, the poll loop stops and the island would
/// freeze on whatever stage it last saw — verified in the simulator
/// (2026-09-13). Two things keep it honest without push infrastructure,
/// which the app does not have by choice:
///
/// 1. A background task is held for the sync's life. iOS then grants about
///    30 s of running time after the app leaves the foreground, which is
///    enough for most syncs to end the activity properly.
/// 2. The moment the app enters the background, the next content carries a
///    `staleDate` at the end of that window. If the sync outlives it, the
///    extension reads `isStale` and stops claiming a live stage. The job
///    itself keeps running server-side; the poll loop resumes on the next
///    open and ends the activity then.
///
/// Failures here are swallowed with a log. The activity is a readout, not
/// the sync: if the system refuses one (Live Activities off in Settings,
/// too many active), the sync must run exactly as before.
@MainActor
final class DeepSyncActivity {
    static let shared = DeepSyncActivity()

    /// How long iOS keeps a backgrounded app running for a background task.
    /// Not a promise: the system reports the real figure at runtime and it
    /// has been ~30 s since iOS 13. The stale date lands a little inside it.
    private static let backgroundGrace: TimeInterval = 25

    private var activity: ActivityKit.Activity<SyncActivityAttributes>?
    /// Which of `SyncActivityAttributes.steps` is in progress. Kept here so a
    /// stage string nobody recognises leaves the strip where it was.
    private var step = 0
    private var backgroundTask: UIBackgroundTaskIdentifier = .invalid
    /// Set while the app is in the background with an activity up: the
    /// point past which the phone can no longer promise updates.
    private var staleDeadline: Date?
    private var observers: [NSObjectProtocol] = []

    private init() {
        let center = NotificationCenter.default
        observers = [
            center.addObserver(forName: UIApplication.didEnterBackgroundNotification,
                               object: nil, queue: .main) { _ in
                Task { @MainActor in await DeepSyncActivity.shared.appDidEnterBackground() }
            },
            center.addObserver(forName: UIApplication.willEnterForegroundNotification,
                               object: nil, queue: .main) { _ in
                Task { @MainActor in DeepSyncActivity.shared.staleDeadline = nil }
            },
        ]
    }

    func start(stage: String) {
        endStale()
        guard ActivityAuthorizationInfo().areActivitiesEnabled else { return }
        do {
            step = SyncActivityAttributes.step(for: stage, previous: 0)
            activity = try ActivityKit.Activity.request(
                attributes: SyncActivityAttributes(startedAt: .now),
                content: content(stage: stage, outcome: .running, endedAt: nil)
            )
            beginBackgroundTask()
        } catch {
            print("Live Activity request failed (non-fatal): \(error)")
        }
    }

    func update(stage: String) async {
        guard let activity else { return }
        step = SyncActivityAttributes.step(for: stage, previous: step)
        await activity.update(content(stage: stage, outcome: .running, endedAt: nil))
    }

    /// Final state, then the system takes it down shortly after. `endedAt`
    /// is what freezes the elapsed timer on the last frame.
    func end(_ outcome: SyncActivityAttributes.Outcome, message: String) async {
        guard let activity else { return }
        self.activity = nil
        // Synced lights the whole strip; failed leaves it where it stopped.
        if outcome == .synced { step = SyncActivityAttributes.steps.count - 1 }
        await activity.end(content(stage: message, outcome: outcome, endedAt: .now),
                           dismissalPolicy: .after(.now.addingTimeInterval(6)))
        endBackgroundTask()
    }

    /// Activities left behind by a launch that died mid-sync. The job itself
    /// keeps running server-side and its result lands on the next refresh,
    /// so a stale "syncing" readout would be lying. Called on Today's first
    /// load and before every new request.
    func endStale() {
        for stale in ActivityKit.Activity<SyncActivityAttributes>.activities where stale.id != activity?.id {
            Task { await stale.end(nil, dismissalPolicy: .immediate) }
        }
    }

    // MARK: - Background

    private func content(stage: String, outcome: SyncActivityAttributes.Outcome, endedAt: Date?)
        -> ActivityContent<SyncActivityAttributes.ContentState> {
        ActivityContent(
            state: SyncActivityAttributes.ContentState(stage: stage, step: step, outcome: outcome, endedAt: endedAt),
            staleDate: staleDeadline
        )
    }

    /// Re-sends the current state with the stale date attached. Only the
    /// content's `staleDate` changes; the stage the island shows does not.
    private func appDidEnterBackground() async {
        guard let activity else { return }
        staleDeadline = .now.addingTimeInterval(Self.backgroundGrace)
        let state = activity.content.state
        await activity.update(content(stage: state.stage, outcome: state.outcome, endedAt: state.endedAt))
    }

    private func beginBackgroundTask() {
        endBackgroundTask()
        backgroundTask = UIApplication.shared.beginBackgroundTask(withName: "Deep sync readout") {
            // Documented to run on the main thread; the task must be ended
            // before this returns or the app is terminated, so no hop.
            MainActor.assumeIsolated { DeepSyncActivity.shared.endBackgroundTask() }
        }
    }

    private func endBackgroundTask() {
        guard backgroundTask != .invalid else { return }
        UIApplication.shared.endBackgroundTask(backgroundTask)
        backgroundTask = .invalid
    }

    #if DEBUG
    /// `--sync-demo` launch argument: walks the activity through fake stages
    /// with no network, so the island can be screenshotted in the simulator
    /// without firing a real scrape at production. Same idea as `--variant`.
    static var demoRequested: Bool { CommandLine.arguments.contains("--sync-demo") }

    /// Seconds per fake stage: `--sync-demo 3` for a quick run that ends
    /// inside the background grace, `--sync-demo 20` for one that outlives it.
    private static var demoStageSeconds: Int {
        let args = CommandLine.arguments
        guard let i = args.firstIndex(of: "--sync-demo"), i + 1 < args.count, let n = Int(args[i + 1]) else { return 8 }
        return n
    }

    func runDemo() async {
        let seconds = Self.demoStageSeconds
        start(stage: "Starting deep sync...")
        for stage in ["Scraping COROS...", "Evaluating recovery...", "Adapting today's plan...", "Refreshing data..."] {
            try? await Task.sleep(for: .seconds(seconds))
            await update(stage: stage)
        }
        try? await Task.sleep(for: .seconds(seconds))
        await end(.synced, message: "Biometrics synced")
    }
    #endif
}
