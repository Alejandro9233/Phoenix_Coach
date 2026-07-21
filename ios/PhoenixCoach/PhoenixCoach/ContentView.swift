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
                    Image(systemName: "chart.bar.doc.horizontal.fill")
                    Text("Log")
                }
                .tag(2)
            
            DashboardView()
                .tabItem {
                    Image(systemName: "list.bullet.clipboard.fill")
                    Text("Feedback")
                }
                .tag(3)
            
            ProfileView()
                .tabItem {
                    Image(systemName: "person.crop.circle.fill")
                    Text("Profile")
                }
                .tag(4)
        }
        .tint(.orange)
    }
}

#Preview {
    ContentView()
        .preferredColorScheme(.dark)
}
