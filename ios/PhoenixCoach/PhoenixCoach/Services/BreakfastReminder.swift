import Foundation
import UserNotifications

/// The 11 AM breakfast reminder — the one local notification the app sends
/// (Alex, 2026-10-21). Local notifications were deleted on 2026-08-16, but
/// the repeating requests that code had scheduled outlived it: iOS kept
/// firing "Morning Readiness: you have a <bike ride> scheduled today" with a
/// title frozen months earlier. So this one never repeats: each date gets
/// its own request, at most `daysAhead` out, rebuilt from the server on every
/// app open. Stop opening the app and the reminders run out on their own.
enum BreakfastReminder {
    static let hour = 11
    static let daysAhead = 3
    private static let prefix = "breakfast-"
    /// Repeating requests left by the deleted NotificationManager (b4162a2).
    private static let ghosts = ["morning_readiness", "race_countdown", "load_alert", "analysis_ready"]

    static func refresh() async {
        let center = UNUserNotificationCenter.current()
        center.removePendingNotificationRequests(withIdentifiers: ghosts)
        center.removeDeliveredNotifications(withIdentifiers: ghosts)

        guard (try? await center.requestAuthorization(options: [.alert, .sound])) == true,
              let days = try? await NetworkManager.shared.fetchNutritionDays(count: daysAhead)
        else { return }

        let stale = await center.pendingNotificationRequests()
            .map(\.identifier)
            .filter { $0.hasPrefix(prefix) }
        center.removePendingNotificationRequests(withIdentifiers: stale)

        for day in days {
            guard let request = request(for: day) else { continue }
            try? await center.add(request)
        }
    }

    private static func request(for day: NutritionDay) -> UNNotificationRequest? {
        guard let code = day.dayType, let breakfast = day.meals?.first else { return nil }
        let parts = day.date.split(separator: "-").compactMap { Int($0) }
        guard parts.count == 3 else { return nil }
        let fire = DateComponents(year: parts[0], month: parts[1], day: parts[2], hour: hour, minute: 0)
        guard let when = Calendar.current.date(from: fire), when > Date() else { return nil }

        let content = UNMutableNotificationContent()
        content.title = "Breakfast at \(breakfast.time) · Day \(code)"
        var body = breakfast.items
            .map { "\($0.name) \(amount($0.amount)) \($0.unit)" }
            .joined(separator: ", ")
        if day.isEstimate {
            body += "\nCalendar estimate: this week has no plan yet."
        }
        content.body = body
        content.sound = .default
        return UNNotificationRequest(
            identifier: prefix + day.date,
            content: content,
            trigger: UNCalendarNotificationTrigger(dateMatching: fire, repeats: false)
        )
    }

    private static func amount(_ value: Double) -> String {
        value == value.rounded() ? String(Int(value)) : String(format: "%.1f", value)
    }
}
