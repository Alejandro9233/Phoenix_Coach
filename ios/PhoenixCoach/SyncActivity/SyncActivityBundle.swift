import WidgetKit
import SwiftUI

/// The extension holds one thing: the deep-sync Live Activity. The home
/// screen widget and Control Center control that Xcode's template ships
/// were removed on purpose — nothing here is worth a widget slot, and a
/// control that could fire a 90 s scrape from Control Center is the toolbar
/// button TodayView already deleted for being too easy to hit by accident.
@main
struct SyncActivityBundle: WidgetBundle {
    var body: some Widget {
        SyncActivityLiveActivity()
    }
}
