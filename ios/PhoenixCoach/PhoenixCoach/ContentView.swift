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
            
            DashboardView()
                .tabItem {
                    Image(systemName: "book.fill")
                    Text("Journal")
                }
                .tag(2)
            
            FeedbackView()
                .tabItem {
                    Image(systemName: "clock.arrow.circlepath")
                    Text("Recent")
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
    }
}

#Preview {
    ContentView()
        .preferredColorScheme(.dark)
}
