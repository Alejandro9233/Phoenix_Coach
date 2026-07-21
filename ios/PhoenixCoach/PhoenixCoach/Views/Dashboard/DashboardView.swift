import SwiftUI

struct DashboardView: View {
    @State private var dashboard: DashboardResponse?
    @State private var isLoading = false
    @State private var visibleCount = 10
    
    var body: some View {
        NavigationStack {
            ScrollView {
                if let data = dashboard {
                    VStack(spacing: 16) {
                        // Activities with coach feedback
                        if data.activities.isEmpty {
                            ContentUnavailableView(
                                "No Activities Yet",
                                systemImage: "figure.run",
                                description: Text("Your training sessions will appear here after syncing with COROS.")
                            )
                        } else {
                            let activities = data.activities
                            let visibleActivities = Array(activities.prefix(visibleCount))
                            
                            ForEach(visibleActivities) { activity in
                                NavigationLink {
                                    ActivityDetailView(activity: activity)
                                } label: {
                                    activityRow(activity)
                                }
                                .buttonStyle(.plain)
                            }
                            
                            if activities.count > visibleCount {
                                Button {
                                    visibleCount += 10
                                } label: {
                                    Text("Load More")
                                        .font(.subheadline.bold())
                                        .foregroundStyle(.orange)
                                        .frame(maxWidth: .infinity)
                                        .padding()
                                        .background(Color(.systemGray6))
                                        .clipShape(RoundedRectangle(cornerRadius: 12))
                                }
                                .padding(.top, 8)
                            }
                        }
                    }
                    .padding()
                } else if isLoading {
                    ProgressView("Loading activities...")
                        .padding(60)
                } else {
                    ContentUnavailableView(
                        "No Data",
                        systemImage: "chart.bar.doc.horizontal",
                        description: Text("Pull down to refresh")
                    )
                }
            }
            .navigationTitle("Feedback")
            .refreshable {
                await fetchDashboard()
            }
            .task {
                await fetchDashboard()
            }
        }
    }
    
    // MARK: - Activity Row
    
    private func activityRow(_ activity: Activity) -> some View {
        HStack(spacing: 14) {
            // Sport Icon
            Text(activity.sportEmoji)
                .font(.system(size: 32))
                .frame(width: 48, height: 48)
                .background(Color(.systemGray6))
                .clipShape(RoundedRectangle(cornerRadius: 12))
            
            // Activity Info
            VStack(alignment: .leading, spacing: 4) {
                Text(activity.sport?.capitalized ?? "Activity")
                    .font(.subheadline.bold())
                
                HStack(spacing: 8) {
                    if !activity.distanceFormatted.isEmpty {
                        Label(activity.distanceFormatted, systemImage: "ruler")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                    Label(activity.durationFormatted, systemImage: "clock")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
                
                if let date = activity.startTimeDate {
                    Text(formatActivityDate(date))
                        .font(.caption2)
                        .foregroundStyle(.tertiary)
                }
            }
            
            Spacer()
            
            // Right side metrics
            VStack(alignment: .trailing, spacing: 4) {
                if let hr = activity.avgHr {
                    HStack(spacing: 3) {
                        Image(systemName: "heart.fill")
                            .font(.caption2)
                            .foregroundStyle(.red)
                        Text("\(hr)")
                            .font(.caption.bold().monospacedDigit())
                    }
                }
                if let tl = activity.trainingLoad {
                    HStack(spacing: 3) {
                        Image(systemName: "gauge.with.needle.fill")
                            .font(.caption2)
                            .foregroundStyle(.orange)
                        Text("\(Int(tl))")
                            .font(.caption.bold().monospacedDigit())
                    }
                }
            }
            
            Image(systemName: "chevron.right")
                .font(.caption.bold())
                .foregroundStyle(.tertiary)
        }
        .padding(16)
        .background(.ultraThinMaterial)
        .clipShape(RoundedRectangle(cornerRadius: 16))
    }
    
    // MARK: - Helpers
    
    private func formatActivityDate(_ date: Date) -> String {
        let formatter = DateFormatter()
        formatter.dateFormat = "EEEE, MMM d • h:mm a" // e.g. "Monday, May 18 • 5:30 PM"
        return formatter.string(from: date)
    }
    
    // MARK: - Networking
    
    private func fetchDashboard() async {
        isLoading = true
        do {
            let data = try await NetworkManager.shared.fetchDashboard()
            await MainActor.run {
                self.dashboard = data
                self.isLoading = false
            }
        } catch {
            await MainActor.run {
                self.isLoading = false
            }
        }
    }
}

#Preview {
    DashboardView()
        .preferredColorScheme(.dark)
}
