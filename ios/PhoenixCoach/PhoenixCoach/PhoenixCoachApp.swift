import SwiftUI

@main
struct PhoenixCoachApp: App {
    @Environment(\.scenePhase) private var scenePhase
    
    var body: some Scene {
        WindowGroup {
            ContentView()
                .preferredColorScheme(.dark)
                .task {
                    NotificationManager.registerDefaults()
                    NotificationManager.shared.requestPermission()
                    await NetworkManager.shared.syncDeviceTimezone()
                }
        }
        .onChange(of: scenePhase) { newPhase in
            if newPhase == .active {
                // Catches travel: returning to the app from a new timezone
                // re-points the backend's idea of "today".
                Task { await NetworkManager.shared.syncDeviceTimezone() }
            }
        }
    }
}
