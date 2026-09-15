import SwiftUI

@main
struct PhoenixCoachApp: App {
    @Environment(\.scenePhase) private var scenePhase
    
    var body: some Scene {
        WindowGroup {
            if let n = VariantHost.requested {
                // /variants judging loop: `--variant N` renders one candidate.
                CurrentVariants.view(n)
                    .preferredColorScheme(.dark)
            } else {
                ContentView()
                    .preferredColorScheme(.dark)
                    .task {
                        await NetworkManager.shared.syncDeviceTimezone()
                        #if DEBUG
                        // `--sync-demo`: fake deep-sync stages for screenshotting
                        // the Live Activity without scraping production.
                        if DeepSyncActivity.demoRequested {
                            await DeepSyncActivity.shared.runDemo()
                        }
                        #endif
                    }
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
