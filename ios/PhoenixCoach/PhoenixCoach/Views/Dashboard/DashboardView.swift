import SwiftUI

struct DashboardView: View {
    var body: some View {
        NavigationStack {
            ZStack {
                DS.Colors.background
                    .ignoresSafeArea()
                    
                ScrollView {
                    VStack {
                        ContentUnavailableView(
                            "Journal",
                            systemImage: "book",
                            description: Text("Your training journal is empty. Start recording your thoughts and feelings about your training here.")
                        )
                        .padding(.top, 100)
                    }
                    .frame(maxWidth: .infinity)
                }
            }
            .navigationTitle("Journal")
        }
    }
}

#Preview {
    DashboardView()
        .preferredColorScheme(.dark)
}
