import Foundation
import UserNotifications
import os.log
import Combine

/// Local notification scheduling.
///
/// Two traps this class exists to avoid, both of which silently dropped every
/// notification before:
///
/// 1. The Profile toggles are `@AppStorage("notifyX") = true`. SwiftUI does NOT
///    write that default into UserDefaults — it only substitutes it on read
///    through the property wrapper. A plain `UserDefaults.bool(forKey:)` read
///    therefore returned `false` until the user physically flipped a switch.
///    `registerDefaults()` fixes that and must run before anything reads them.
/// 2. Authorization was cached in a `@Published` populated by an async callback.
///    Schedulers firing during app launch read it before it was ever set. All
///    authorization checks now go to the system at schedule time instead.
@MainActor
class NotificationManager: ObservableObject {
    static let shared = NotificationManager()

    /// UserDefaults keys for the Profile > Notifications toggles.
    /// Must match the `@AppStorage` keys in ProfileView.
    enum Toggle: String, CaseIterable {
        case morningReadiness = "notifyMorningReadiness"
        case coachAnalysis = "notifyCoachAnalysis"
        case loadAlerts = "notifyLoadAlerts"
        case raceCountdown = "notifyRaceCountdown"
    }

    @Published var isAuthorized: Bool = false

    private nonisolated let logger = Logger(subsystem: "com.phoenix.coach", category: "Notifications")

    private init() {
        // Airtight ordering: every toggle read that gates a notification goes
        // through `shared`, so seeding here guarantees the defaults exist before
        // any gate can read them.
        Self.registerDefaults()
        refreshAuthorizationStatus()
    }

    /// Seeds the notification toggles to on. Call once at launch, before any
    /// view reads them. Registered defaults never overwrite a real user choice.
    static func registerDefaults() {
        UserDefaults.standard.register(
            defaults: Dictionary(uniqueKeysWithValues: Toggle.allCases.map { ($0.rawValue, true) })
        )
    }

    func requestPermission() {
        UNUserNotificationCenter.current().requestAuthorization(options: [.alert, .badge, .sound]) { [weak self] granted, error in
            if let error = error {
                self?.logger.error("Permission request failed: \(error.localizedDescription)")
            } else {
                self?.logger.info("Notification permission granted: \(granted)")
            }
            Task { @MainActor in self?.isAuthorized = granted }
        }
    }

    func refreshAuthorizationStatus() {
        UNUserNotificationCenter.current().getNotificationSettings { [weak self] settings in
            let ok = settings.authorizationStatus == .authorized || settings.authorizationStatus == .provisional
            Task { @MainActor in self?.isAuthorized = ok }
        }
    }

    // MARK: - Gate

    /// Runs `schedule` only if the toggle is on AND the system says we're allowed.
    ///
    /// Authorization is read live rather than from `isAuthorized`, because on a
    /// cold launch the permission dialog may still be on screen when a view
    /// tries to schedule. In that `.notDetermined` window we ask, then proceed
    /// on the answer — so the first day's notification isn't lost to a race.
    private func ifAllowed(_ toggle: Toggle, _ schedule: @escaping () -> UNNotificationRequest) {
        guard UserDefaults.standard.bool(forKey: toggle.rawValue) else {
            logger.debug("Skipped \(toggle.rawValue): toggle off")
            return
        }

        let center = UNUserNotificationCenter.current()
        center.getNotificationSettings { [weak self] settings in
            switch settings.authorizationStatus {
            case .authorized, .provisional:
                self?.submit(schedule())
            case .notDetermined:
                center.requestAuthorization(options: [.alert, .badge, .sound]) { granted, _ in
                    Task { @MainActor in self?.isAuthorized = granted }
                    guard granted else { return }
                    self?.submit(schedule())
                }
            default:
                self?.logger.debug("Skipped \(toggle.rawValue): not authorized")
            }
        }
    }

    private nonisolated func submit(_ request: UNNotificationRequest) {
        UNUserNotificationCenter.current().add(request) { [weak self] error in
            if let error = error {
                Task { @MainActor in
                    self?.logger.error("Failed to schedule \(request.identifier): \(error.localizedDescription)")
                }
            }
        }
    }

    // MARK: - Notification Schedulers

    func scheduleMorningReadiness(workoutTitle: String?) {
        ifAllowed(.morningReadiness) {
            let content = UNMutableNotificationContent()
            content.title = "Morning Readiness"
            content.sound = .default

            if let workoutTitle = workoutTitle, !workoutTitle.isEmpty {
                content.body = "Good morning! You have a \(workoutTitle) scheduled today."
            } else {
                content.body = "Rest Day: Focus on stretching and active recovery."
            }

            var dateComponents = DateComponents()
            dateComponents.hour = 8
            dateComponents.minute = 0

            // Re-registering the same identifier replaces the pending request,
            // so this stays a single daily reminder no matter how often the
            // Today tab reloads.
            return UNNotificationRequest(
                identifier: "morning_readiness",
                content: content,
                trigger: UNCalendarNotificationTrigger(dateMatching: dateComponents, repeats: true)
            )
        }
    }

    func scheduleRaceCountdown(weeks: Int) {
        ifAllowed(.raceCountdown) {
            let content = UNMutableNotificationContent()
            content.title = "Race Countdown"
            content.body = weeks <= 0
                ? "Race week. Review your Block Calendar for the taper."
                : "\(weeks) week\(weeks == 1 ? "" : "s") until race day! Review your Block Calendar for the upcoming phase."
            content.sound = .default

            // Every Sunday at 9 AM
            var dateComponents = DateComponents()
            dateComponents.weekday = 1
            dateComponents.hour = 9
            dateComponents.minute = 0

            return UNNotificationRequest(
                identifier: "race_countdown",
                content: content,
                trigger: UNCalendarNotificationTrigger(dateMatching: dateComponents, repeats: true)
            )
        }
    }

    func triggerLoadAlert(loadRatio: Double) {
        guard loadRatio > 1.3 else { return } // Only alert for overreaching/high risk

        ifAllowed(.loadAlerts) {
            let content = UNMutableNotificationContent()
            content.title = "Training Load Alert"
            if loadRatio > 1.5 {
                content.body = "⚠️ High Risk Load: Your load ratio is \(String(format: "%.2f", loadRatio)). Consider dialing back intensity."
            } else {
                content.body = "Overreaching Load: Your load ratio is \(String(format: "%.2f", loadRatio)). Ensure you are prioritizing recovery."
            }
            content.sound = .default

            return UNNotificationRequest(
                identifier: "load_alert",
                content: content,
                trigger: UNTimeIntervalNotificationTrigger(timeInterval: 5, repeats: false)
            )
        }
    }

    func triggerCoachAnalysisReady() {
        ifAllowed(.coachAnalysis) {
            let content = UNMutableNotificationContent()
            content.title = "Coach Analysis Ready"
            content.body = "Your latest activity has been synced and analyzed by the coach. Tap to view your rating."
            content.sound = .default

            return UNNotificationRequest(
                identifier: "analysis_ready",
                content: content,
                trigger: UNTimeIntervalNotificationTrigger(timeInterval: 5, repeats: false)
            )
        }
    }

    // MARK: - Utilities

    func cancelAll() {
        UNUserNotificationCenter.current().removeAllPendingNotificationRequests()
        UNUserNotificationCenter.current().removeAllDeliveredNotifications()
    }
}
