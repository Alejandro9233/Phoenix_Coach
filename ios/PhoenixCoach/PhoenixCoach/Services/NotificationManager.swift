import Foundation
import UserNotifications
import os.log
import Combine

@MainActor
class NotificationManager: ObservableObject {
    static let shared = NotificationManager()
    
    @Published var isAuthorized: Bool = false
    
    private let logger = Logger(subsystem: "com.phoenix.coach", category: "Notifications")
    
    private init() {
        checkAuthorizationStatus()
    }
    
    func requestPermission() {
        UNUserNotificationCenter.current().requestAuthorization(options: [.alert, .badge, .sound]) { [weak self] granted, error in
            DispatchQueue.main.async {
                self?.isAuthorized = granted
                if let error = error {
                    self?.logger.error("Failed to request notification permission: \(error.localizedDescription)")
                } else {
                    self?.logger.info("Notification permission granted: \(granted)")
                }
            }
        }
    }
    
    func checkAuthorizationStatus() {
        UNUserNotificationCenter.current().getNotificationSettings { settings in
            DispatchQueue.main.async {
                self.isAuthorized = (settings.authorizationStatus == .authorized || settings.authorizationStatus == .provisional)
            }
        }
    }
    
    // MARK: - Notification Schedulers
    
    func scheduleMorningReadiness(workoutTitle: String?) {
        guard isAuthorized, UserDefaults.standard.bool(forKey: "notifyMorningReadiness") else { return }
        
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
        
        let trigger = UNCalendarNotificationTrigger(dateMatching: dateComponents, repeats: true)
        let request = UNNotificationRequest(identifier: "morning_readiness", content: content, trigger: trigger)
        
        UNUserNotificationCenter.current().add(request) { error in
            if let error = error {
                self.logger.error("Failed to schedule morning readiness: \(error.localizedDescription)")
            }
        }
    }
    
    func scheduleRaceCountdown(weeks: Int) {
        guard isAuthorized, UserDefaults.standard.bool(forKey: "notifyRaceCountdown") else { return }
        
        let content = UNMutableNotificationContent()
        content.title = "Race Countdown"
        content.body = "\(weeks) weeks until race day! Review your Block Calendar for the upcoming phase."
        content.sound = .default
        
        // Schedule every Sunday at 9 AM
        var dateComponents = DateComponents()
        dateComponents.weekday = 1 // Sunday
        dateComponents.hour = 9
        dateComponents.minute = 0
        
        let trigger = UNCalendarNotificationTrigger(dateMatching: dateComponents, repeats: true)
        let request = UNNotificationRequest(identifier: "race_countdown", content: content, trigger: trigger)
        
        UNUserNotificationCenter.current().add(request) { error in
            if let error = error {
                self.logger.error("Failed to schedule race countdown: \(error.localizedDescription)")
            }
        }
    }
    
    func triggerLoadAlert(loadRatio: Double) {
        guard isAuthorized, UserDefaults.standard.bool(forKey: "notifyLoadAlerts") else { return }
        guard loadRatio > 1.3 else { return } // Only alert for overreaching/high risk
        
        let content = UNMutableNotificationContent()
        content.title = "Training Load Alert"
        if loadRatio > 1.5 {
            content.body = "⚠️ High Risk Load: Your load ratio is \(String(format: "%.2f", loadRatio)). Consider dialing back intensity."
        } else {
            content.body = "Overreaching Load: Your load ratio is \(String(format: "%.2f", loadRatio)). Ensure you are prioritizing recovery."
        }
        content.sound = .default
        
        // Trigger 1 hour from now or immediately. Let's trigger immediately since it's background sync.
        let trigger = UNTimeIntervalNotificationTrigger(timeInterval: 5, repeats: false)
        let request = UNNotificationRequest(identifier: "load_alert", content: content, trigger: trigger)
        
        UNUserNotificationCenter.current().add(request) { error in
            if let error = error {
                self.logger.error("Failed to trigger load alert: \(error.localizedDescription)")
            }
        }
    }
    
    func triggerCoachAnalysisReady() {
        guard isAuthorized, UserDefaults.standard.bool(forKey: "notifyCoachAnalysis") else { return }
        
        let content = UNMutableNotificationContent()
        content.title = "Coach Analysis Ready"
        content.body = "Your latest activity has been synced and analyzed by the coach. Tap to view your rating."
        content.sound = .default
        
        let trigger = UNTimeIntervalNotificationTrigger(timeInterval: 5, repeats: false)
        let request = UNNotificationRequest(identifier: "analysis_ready", content: content, trigger: trigger)
        
        UNUserNotificationCenter.current().add(request) { error in
            if let error = error {
                self.logger.error("Failed to trigger analysis ready alert: \(error.localizedDescription)")
            }
        }
    }
    
    // MARK: - Utilities
    
    func cancelAll() {
        UNUserNotificationCenter.current().removeAllPendingNotificationRequests()
        UNUserNotificationCenter.current().removeAllDeliveredNotifications()
    }
}
