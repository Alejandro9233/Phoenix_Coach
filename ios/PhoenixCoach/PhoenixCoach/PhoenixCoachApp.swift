import SwiftUI

@main
struct PhoenixCoachApp: App {
    @Environment(\.scenePhase) private var scenePhase
    
    var body: some Scene {
        WindowGroup {
            ContentView()
                .preferredColorScheme(.dark)
                .task {
                    NotificationManager.shared.requestPermission()
                }
        }
        .onChange(of: scenePhase) { newPhase in
            if newPhase == .active {
                // Could refresh notifications here
            }
        }
    }
}
