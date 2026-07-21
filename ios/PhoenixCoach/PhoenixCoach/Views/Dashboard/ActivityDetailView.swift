import SwiftUI

struct ActivityDetailView: View {
    let activity: Activity
    @State private var analysis: ActivityAnalysis?
    @State private var isLoading = false
    
    var body: some View {
        ScrollView {
            VStack(spacing: 24) {
                // Header with Sport and Date
                VStack(spacing: 8) {
                    Text(activity.sportEmoji)
                        .font(.system(size: 60))
                    Text(activity.sport?.capitalized ?? "Activity")
                        .font(.title.bold())
                    Text(activity.startTimeDate?.formatted(date: .long, time: .shortened) ?? "Unknown Date")
                        .font(.subheadline)
                        .foregroundStyle(.secondary)
                }
                .padding(.top)
                
                // Primary Metrics Grid
                LazyVGrid(columns: [GridItem(.flexible()), GridItem(.flexible()), GridItem(.flexible())], spacing: 20) {
                    metricDetail("Duration", value: activity.durationFormatted, icon: "timer")
                    metricDetail("Distance", value: activity.distanceFormatted, icon: "figure.run")
                    metricDetail("Avg HR", value: "\(activity.avgHr ?? 0) bpm", icon: "heart.fill", color: .red)
                    metricDetail("Max HR", value: "\(activity.maxHr ?? 0) bpm", icon: "waveform.path.ecg", color: .red)
                    metricDetail("Load", value: "\(Int(activity.trainingLoad ?? 0))", icon: "gauge.with.needle.fill", color: .orange)
                    if let power = activity.avgPowerWatts {
                        metricDetail("Power", value: "\(Int(power)) W", icon: "bolt.fill", color: .yellow)
                    }
                }
                .padding()
                .background(.ultraThinMaterial)
                .clipShape(RoundedRectangle(cornerRadius: 20))
                
                // Coach's Take Section
                VStack(alignment: .leading, spacing: 16) {
                    HStack {
                        Label("COACH'S TAKE", systemImage: "brain.head.profile.fill")
                            .font(.caption.bold())
                            .foregroundStyle(.orange)
                        Spacer()
                        if let rating = analysis?.rating {
                            Text(rating)
                                .font(.title2.bold())
                                .padding(.horizontal, 12)
                                .padding(.vertical, 4)
                                .background(ratingColor(rating).opacity(0.2))
                                .foregroundStyle(ratingColor(rating))
                                .clipShape(Capsule())
                        }
                    }
                    
                    if let coach = analysis {
                        VStack(alignment: .leading, spacing: 12) {
                            Text(coach.analysis)
                                .font(.body)
                                .lineSpacing(4)
                            
                            Divider()
                                .padding(.vertical, 4)
                            
                            HStack(alignment: .top, spacing: 12) {
                                Image(systemName: "lightbulb.fill")
                                    .foregroundStyle(.yellow)
                                    .font(.title3)
                                VStack(alignment: .leading, spacing: 4) {
                                    Text("PRO TIP")
                                        .font(.caption.bold())
                                        .foregroundStyle(.secondary)
                                    Text(coach.advice)
                                        .font(.subheadline)
                                        .foregroundStyle(.primary)
                                }
                            }
                        }
                    } else if isLoading {
                        VStack(spacing: 12) {
                            ProgressView()
                            Text("Analyzing session...")
                                .font(.caption)
                                .foregroundStyle(.secondary)
                        }
                        .frame(maxWidth: .infinity)
                        .padding(.vertical, 20)
                    } else {
                        Button {
                            Task { await fetchAnalysis() }
                        } label: {
                            Label("Request Analysis", systemImage: "sparkles")
                                .font(.headline)
                                .frame(maxWidth: .infinity)
                                .padding()
                                .background(Color.orange)
                                .foregroundStyle(.white)
                                .clipShape(RoundedRectangle(cornerRadius: 12))
                        }
                    }
                }
                .padding(20)
                .background(.ultraThinMaterial)
                .clipShape(RoundedRectangle(cornerRadius: 24))
                .shadow(color: .black.opacity(0.1), radius: 10, x: 0, y: 5)
            }
            .padding()
        }
        .navigationTitle("Session Review")
        .navigationBarTitleDisplayMode(.inline)
        .background(Color(.systemGroupedBackground))
        .task {
            await fetchAnalysis()
        }
    }
    
    private func metricDetail(_ label: String, value: String, icon: String, color: Color = .blue) -> some View {
        VStack(spacing: 8) {
            Image(systemName: icon)
                .font(.title3)
                .foregroundStyle(color)
            Text(value)
                .font(.headline.bold())
            Text(label)
                .font(.caption2)
                .foregroundStyle(.secondary)
        }
    }
    
    private func ratingColor(_ rating: String) -> Color {
        let r = rating.prefix(1).uppercased()
        switch r {
        case "A": return .green
        case "B": return .blue
        case "C": return .yellow
        case "D": return .orange
        case "F": return .red
        default: return .secondary
        }
    }
    
    private func fetchAnalysis() async {
        guard let id = activity.id else { return }
        isLoading = true
        do {
            analysis = try await NetworkManager.shared.fetchActivityAnalysis(activityID: id)
            isLoading = false
        } catch {
            print("Failed to fetch activity analysis: \(error)")
            isLoading = false
        }
    }
}
