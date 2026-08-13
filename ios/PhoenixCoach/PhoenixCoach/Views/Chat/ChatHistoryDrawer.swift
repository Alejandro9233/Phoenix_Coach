import SwiftUI

struct ChatHistoryDrawer: View {
    let sessions: [APIChatSession]
    let currentSessionId: Int?
    let onSelect: (APIChatSession) -> Void
    
    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            // Header
            HStack {
                Text("Chat History")
                    .font(.title2.bold())
                Spacer()
            }
            .padding()
            .background(DS.Colors.surface)
            
            Divider()
            
            // List of sessions
            ScrollView {
                LazyVStack(spacing: 8) {
                    if sessions.isEmpty {
                        Text("No past conversations.")
                            .font(.subheadline)
                            .foregroundStyle(.secondary)
                            .padding(.top, 24)
                    } else {
                        ForEach(sessions) { session in
                            sessionRow(session)
                        }
                    }
                }
                .padding()
            }
        }
        .background(DS.Colors.background)
        .ignoresSafeArea(.all, edges: .bottom)
    }
    
    private func sessionRow(_ session: APIChatSession) -> some View {
        Button {
            onSelect(session)
        } label: {
            VStack(alignment: .leading, spacing: 4) {
                Text(session.title)
                    .font(.subheadline.bold())
                    .foregroundStyle(session.id == currentSessionId ? DS.Colors.accent : DS.Colors.primaryText)
                    .lineLimit(1)
                
                Text(formatDate(session.updatedAt))
                    .font(.caption2)
                    .foregroundStyle(.secondary)
            }
            .padding(12)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(
                RoundedRectangle(cornerRadius: 12)
                    .fill(session.id == currentSessionId ? DS.Colors.accent.opacity(0.1) : DS.Colors.surface)
            )
            .overlay(
                RoundedRectangle(cornerRadius: 12)
                    .stroke(session.id == currentSessionId ? DS.Colors.accent : Color.clear, lineWidth: 1)
            )
        }
        .buttonStyle(.plain)
    }
    
    private func formatDate(_ dateString: String) -> String {
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        guard let date = formatter.date(from: dateString) else {
            // fallback
            let fallback = ISO8601DateFormatter()
            if let date = fallback.date(from: dateString) {
                let displayFormatter = DateFormatter()
                displayFormatter.dateStyle = .medium
                displayFormatter.timeStyle = .short
                return displayFormatter.string(from: date)
            }
            return dateString 
        }
        
        let displayFormatter = DateFormatter()
        displayFormatter.dateStyle = .medium
        displayFormatter.timeStyle = .short
        return displayFormatter.string(from: date)
    }
}

#Preview {
    ChatHistoryDrawer(
        sessions: [
            APIChatSession(id: 1, title: "Half Marathon Prep", updatedAt: "2024-03-24T10:00:00.000Z"),
            APIChatSession(id: 2, title: "Nutrition Advice", updatedAt: "2024-03-23T15:30:00.000Z")
        ],
        currentSessionId: 1,
        onSelect: { _ in }
    )
    .preferredColorScheme(.dark)
}
