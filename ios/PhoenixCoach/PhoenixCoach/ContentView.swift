import SwiftUI

struct ContentView: View {
    @State private var selectedTab = 0
    
    var body: some View {
        TabView(selection: $selectedTab) {
            TodayView()
                .tabItem {
                    Image(systemName: "flame.fill")
                    Text("Today")
                }
                .tag(0)
            
            CoachChatView()
                .tabItem {
                    Image(systemName: "message.fill")
                    Text("Coach")
                }
                .tag(1)
            
            FeedbackView()
                .tabItem {
                    Image(systemName: "clock.arrow.circlepath")
                    Text("Recent")
                }
                .tag(2)

            HistoryView()
                .tabItem {
                    Image(systemName: "list.bullet.rectangle.fill")
                    Text("History")
                }
                .tag(3)

            ProfileView()
                .tabItem {
                    Image(systemName: "person.crop.circle.fill")
                    Text("Profile")
                }
                .tag(4)
        }
        .tint(.white)
        .onChange(of: selectedTab) { _ in
            UIImpactFeedbackGenerator(style: .light).impactOccurred()
        }
        // Posted by TodayView's race-setup card ("no race configured yet").
        .onReceive(NotificationCenter.default.publisher(for: NSNotification.Name("OpenProfileTab"))) { _ in
            selectedTab = 4
        }
        // Posted by the Today debrief card's "View in History".
        .onReceive(NotificationCenter.default.publisher(for: NSNotification.Name("OpenHistoryTab"))) { _ in
            selectedTab = 3
        }
        // Posted with a prefill message (ChatPrefill.pending); CoachChatView
        // consumes it in .onReceive AND .task — an unvisited Coach tab has no
        // live subscription yet, so the holder bridges the first open.
        .onReceive(NotificationCenter.default.publisher(for: NSNotification.Name("OpenCoachChat"))) { _ in
            selectedTab = 1
        }
    }
}

/// Bridges "Discuss with coach" taps into CoachChatView, which may not be
/// materialized yet (TabView builds tabs lazily). Set before posting
/// OpenCoachChat; CoachChatView consumes-and-clears on receive or appear.
enum ChatPrefill {
    static var pending: String?
}

#Preview {
    ContentView()
        .preferredColorScheme(.dark)
}
