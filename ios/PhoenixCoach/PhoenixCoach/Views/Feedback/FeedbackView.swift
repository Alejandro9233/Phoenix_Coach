import SwiftUI

struct FeedbackView: View {
    @State private var dashboard: DashboardResponse?
    @State private var isLoadingActivities = false
    @State private var errorMessage: String?
    @State private var selectedSport: String? = nil
    @State private var visibleCount = 10
    
    private var filteredActivities: [Activity] {
        guard let activities = dashboard?.activities else { return [] }
        guard let selected = selectedSport else { return activities }
        return activities.filter { $0.sport?.lowercased().hasPrefix(selected.lowercased()) == true }
    }
    
    private var visibleActivities: [Activity] {
        Array(filteredActivities.prefix(visibleCount))
    }
    
    // Design system colors matching TodayView & ProfileView
                                         // White accent
                 
    var body: some View {
        NavigationStack {
            ZStack {
                DS.Colors.background
                    .ignoresSafeArea()
                
                RadialGradient(
                    gradient: Gradient(colors: [
                        DS.Colors.accent.opacity(0.12),
                        .clear
                    ]),
                    center: .top,
                    startRadius: 0,
                    endRadius: 400
                )
                .ignoresSafeArea()
                
                ScrollView {
                    VStack(spacing: 40) {
                        

                        
                        // Recent Load (Activity History)
                        VStack(alignment: .leading, spacing: 16) {
                            HStack(alignment: .bottom) {
                                Text("Recent Load")
                                    .font(.system(size: 24, weight: .regular))
                                    .foregroundStyle(.white)
                                
                                Spacer()
                                
                                Menu {
                                    Button("All") { selectedSport = nil; visibleCount = 10 }
                                    Button("Run") { selectedSport = "run"; visibleCount = 10 }
                                    Button("Bike") { selectedSport = "bike"; visibleCount = 10 }
                                    Button("Swim") { selectedSport = "swim"; visibleCount = 10 }
                                } label: {
                                    HStack(spacing: 4) {
                                        Text(selectedSport == nil ? "FILTER" : selectedSport!.uppercased())
                                        Image(systemName: "line.3.horizontal.decrease")
                                            .font(.system(size: 12))
                                    }
                                    .font(.system(size: 12, weight: .semibold))
                                    .tracking(1.0)
                                    .foregroundStyle(DS.Colors.onSurface)
                                }
                                .accessibilityLabel("Filter Activities: \(selectedSport?.capitalized ?? "All")")
                            }
                            .padding(.bottom, 8)
                            
                            if let err = errorMessage, dashboard == nil {
                                VStack(spacing: 16) {
                                    Image(systemName: "exclamationmark.triangle.fill")
                                        .font(.largeTitle)
                                        .foregroundStyle(DS.Colors.accent.opacity(0.8))
                                    Text(err)
                                        .font(.system(size: 13, weight: .medium))
                                        .foregroundStyle(DS.Colors.onSurface)
                                        .multilineTextAlignment(.center)
                                    Button("Retry") {
                                        Task { await loadDashboard() }
                                    }
                                    .font(.caption.bold())
                                    .foregroundStyle(DS.Colors.accent)
                                }
                                .padding(.top, 40)
                            } else if isLoadingActivities {
                                ProgressView()
                                    .tint(DS.Colors.accent)
                                    .frame(maxWidth: .infinity, alignment: .center)
                                    .padding(.top, 24)
                            } else if filteredActivities.isEmpty {
                                ContentUnavailableView(
                                    "No \(selectedSport?.capitalized ?? "Recent") Activities",
                                    systemImage: "figure.run",
                                    description: Text("Check back after your next session.")
                                )
                                .padding(.top, 40)
                            } else {
                                LazyVStack(spacing: 16) {
                                    ForEach(visibleActivities, id: \.id) { activity in
                                        NavigationLink {
                                            ActivityDetailView(activity: activity)
                                        } label: {
                                            ActivityCard(activity: activity)
                                        }
                                        .buttonStyle(.plain)
                                    }
                                    if visibleCount < filteredActivities.count {
                                        ProgressView()
                                            .task {
                                                try? await Task.sleep(nanoseconds: 300_000_000)
                                                await MainActor.run {
                                                    visibleCount += 10
                                                }
                                            }
                                            .padding(.top, 16)
                                    }
                                }
                            }
                        }
                        
                    }
                    .padding(24)
                }
                .scrollIndicators(.hidden)
            }
            .preferredColorScheme(.dark)
            .navigationTitle("") // Hide default title
            .navigationBarTitleDisplayMode(.inline)
            .task {
                await loadDashboard()
            }
        }
    }
    

    
    // MARK: - Actions
    
    private func loadDashboard() async {
        isLoadingActivities = true
        errorMessage = nil
        do {
            let data = try await NetworkManager.shared.fetchDashboard()
            await MainActor.run {
                self.dashboard = data
                self.isLoadingActivities = false
            }
        } catch {
            await MainActor.run {
                self.errorMessage = error.localizedDescription
                self.isLoadingActivities = false
            }
        }
    }
}

// MARK: - Activity Card View

struct ActivityCard: View {
    let activity: Activity
    
    var body: some View {
        VStack(alignment: .leading, spacing: 24) {
            // Header
            HStack(alignment: .top) {
                VStack(alignment: .leading, spacing: 4) {
                    Text((activity.sport ?? "Workout").uppercased())
                        .font(.system(size: 12, weight: .bold))
                        .tracking(1.5)
                        .foregroundStyle(DS.Colors.outline)
                        .opacity(0.8)
                    
                    Text(activityTitle)
                        .font(.system(size: 16, weight: .regular))
                        .foregroundStyle(.white)
                }
                
                Spacer()
                
                Image(systemName: iconForSport)
                    .font(.system(size: 24))
                    .foregroundStyle(Color.white.opacity(0.4))
            }
            
            // Stats
            HStack(spacing: 32) {
                if let dist = activity.distanceM, dist > 0 {
                    let formatted = String(format: "%.1f", dist / 1000)
                    statView(label: "DISTANCE", value: formatted, unit: "km")
                }
                if let _ = activity.durationSec {
                    statView(label: "TIME", value: activity.durationFormatted, unit: "")
                }
                if let hr = activity.avgHr, hr > 0 {
                    statView(label: "AVG HR", value: "\(hr)", unit: "bpm")
                }
            }
            .padding(.top, 8)
            .overlay(
                Rectangle()
                    .frame(height: 1)
                    .foregroundStyle(Color.white.opacity(0.05)),
                alignment: .top
            )
        }
        .padding(24)
        .background(
            RoundedRectangle(cornerRadius: 16)
                .fill(Color.white.opacity(0.02))
        )
        .background(.ultraThinMaterial)
        .clipShape(RoundedRectangle(cornerRadius: 16))
        .overlay(
            RoundedRectangle(cornerRadius: 16)
                .stroke(Color.white.opacity(0.05), lineWidth: 1)
        )
    }
    
    private var activityTitle: String {
        if let startTime = activity.startTimeDate {
            let formatter = DateFormatter()
            formatter.dateFormat = "EEEE"
            let day = formatter.string(from: startTime)
            
            let hour = Calendar.current.component(.hour, from: startTime)
            let timeOfDay: String
            if hour < 12 { timeOfDay = "Morning" }
            else if hour < 17 { timeOfDay = "Afternoon" }
            else { timeOfDay = "Evening" }
            
            let sportName = activity.sport?.capitalized ?? "Workout"
            return "\(day) \(timeOfDay) \(sportName)"
        }
        return activity.sport?.capitalized ?? "Completed Activity"
    }
    
    private var iconForSport: String {
        switch activity.sport?.lowercased() {
        case "run", "running": return "figure.run"
        case "swim", "swimming": return "figure.pool.swim"
        case "bike", "ride", "cycling": return "figure.outdoor.cycle"
        case "strength", "strng": return "figure.strengthtraining.traditional"
        default: return "figure.run"
        }
    }
    
    private func statView(label: String, value: String, unit: String) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(label)
                .font(.system(size: 10, weight: .bold))
                .tracking(1.5)
                .foregroundStyle(DS.Colors.outline)
            
            HStack(alignment: .firstTextBaseline, spacing: 2) {
                Text(value)
                    .font(.system(size: 32, weight: .ultraLight))
                    .foregroundStyle(.white)
                
                if !unit.isEmpty {
                    Text(unit)
                        .font(.system(size: 16, weight: .regular))
                        .foregroundStyle(DS.Colors.onSurface)
                }
            }
        }
        .padding(.top, 12)
    }
}

#Preview {
    FeedbackView()
}
