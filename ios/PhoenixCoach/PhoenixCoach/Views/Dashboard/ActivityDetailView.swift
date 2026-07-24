
import SwiftUI

// MARK: - Helper Views

struct TechnicalGrid: View {
    var body: some View {
        GeometryReader { geometry in
            Path { path in
                let width = geometry.size.width
                let height = geometry.size.height
                let step: CGFloat = 20
                
                for x in stride(from: 0, to: width, by: step) {
                    path.move(to: CGPoint(x: x, y: 0))
                    path.addLine(to: CGPoint(x: x, y: height))
                }
                for y in stride(from: 0, to: height, by: step) {
                    path.move(to: CGPoint(x: 0, y: y))
                    path.addLine(to: CGPoint(x: width, y: y))
                }
            }
            .stroke(Color.white.opacity(0.03), lineWidth: 1)
        }
    }
}

struct CircularProgressGauge: View {
    let value: Double
    let color: Color
    let icon: String
    
    var body: some View {
        ZStack {
            Circle()
                .stroke(Color.white.opacity(0.05), style: StrokeStyle(lineWidth: 2.5, dash: [2, 2]))
            
            Circle()
                .trim(from: 0, to: value)
                .stroke(color, style: StrokeStyle(lineWidth: 2.5, lineCap: .round))
                .rotationEffect(.degrees(-90))
            
            Image(systemName: icon)
                .font(.system(size: 14))
                .foregroundStyle(color.opacity(0.4))
        }
        .frame(width: 64, height: 64)
    }
}

struct GlowBorderCard<Content: View>: View {
    let content: Content
    
    init(@ViewBuilder content: () -> Content) {
        self.content = content()
    }
    
    var body: some View {
        content
            .padding(20)
            .background(Color.white.opacity(0.03))
            .background(.ultraThinMaterial)
            .clipShape(RoundedRectangle(cornerRadius: 16))
            .overlay(
                RoundedRectangle(cornerRadius: 16)
                    .stroke(Color.white.opacity(0.08), lineWidth: 1)
            )
            .background(
                RoundedRectangle(cornerRadius: 16)
                    .fill(LinearGradient(
                        colors: [DS.Colors.accent.opacity(0.2), .clear, .clear],
                        startPoint: .topLeading,
                        endPoint: .bottomTrailing
                    ))
                    .blur(radius: 2)
                    .padding(-1)
            )
    }
}

struct GlassDeepCard<Content: View>: View {
    let content: Content
    
    init(@ViewBuilder content: () -> Content) {
        self.content = content()
    }
    
    var body: some View {
        content
            .padding(32)
            .background(Color.black.opacity(0.4))
            .background(.ultraThinMaterial)
            .clipShape(RoundedRectangle(cornerRadius: 24))
            .overlay(
                RoundedRectangle(cornerRadius: 24)
                    .strokeBorder(Color.white.opacity(0.05), lineWidth: 1)
            )
            .overlay(
                RoundedRectangle(cornerRadius: 24)
                    .strokeBorder(DS.Colors.accent.opacity(0.2), lineWidth: 1)
                    .mask(LinearGradient(colors: [.white, .clear], startPoint: .top, endPoint: .bottom))
            )
            .shadow(color: .black.opacity(0.5), radius: 20)
    }
}

// MARK: - Main View

struct ActivityDetailView: View {
    let activity: Activity
    @State private var analysis: ActivityAnalysis?
    @State private var isLoading = false
    @State private var errorMessage: String?
    
    var body: some View {
        ZStack {
            Color(hex: "#0e0e10").ignoresSafeArea()
            
            TechnicalGrid()
                .ignoresSafeArea()
                .opacity(0.4)
            
            ScrollView {
                VStack(spacing: 40) {
                    
                    // Header
                    headerSection
                    
                    // Gauges
                    gaugesSection
                    
                    // Micro Metrics
                    microMetricsSection
                    
                    // Coach Analysis
                    coachAnalysisSection
                    
                    // Protocol Recommendation
                    protocolSection
                }
                .padding(.horizontal, 24)
                .padding(.top, 40)
                .padding(.bottom, 120)
            }
            .scrollIndicators(.hidden)
        }
        .preferredColorScheme(.dark)
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            ToolbarItem(placement: .principal) {
                VStack {
                    Text("ACTIVITY LOG")
                        .font(.system(size: 10, weight: .medium))
                        .tracking(3.0)
                        .foregroundStyle(DS.Colors.outline.opacity(0.4))
                }
            }
        }
        .task {
            await fetchAnalysis()
        }
    }
    
    // MARK: - Subsections
    
    private var headerSection: some View {
        VStack(alignment: .leading, spacing: 0) {
            if let date = activity.startTimeDate {
                Text(formatDate(date).uppercased())
                    .font(.system(size: 10, weight: .medium))
                    .tracking(2.0)
                    .foregroundStyle(DS.Colors.outline.opacity(0.4))
                    .padding(.bottom, 8)
            }
            
            HStack(spacing: 8) {
                Text((activity.subSport ?? activity.sport ?? "Workout").uppercased())
                    .font(.system(size: 10, weight: .bold))
                    .tracking(2.0)
                    .padding(.horizontal, 8)
                    .padding(.vertical, 2)
                    .background(DS.Colors.accent.opacity(0.1))
                    .foregroundStyle(DS.Colors.accent)
                    .clipShape(Capsule())
                    .overlay(Capsule().strokeBorder(DS.Colors.accent.opacity(0.2), lineWidth: 1))
            }
            .padding(.bottom, 8)
            
            Text(activity.sport?.uppercased() ?? "ACTIVITY")
                .font(.system(size: 40, weight: .thin))
                .tracking(-0.5)
                .foregroundStyle(DS.Colors.primaryText)
                .padding(.bottom, 32)
            
            HStack(alignment: .bottom, spacing: 48) {
                VStack(alignment: .leading, spacing: 4) {
                    Text("DURATION")
                        .font(.system(size: 10, weight: .medium))
                        .tracking(2.0)
                        .foregroundStyle(DS.Colors.outline.opacity(0.4))
                    Text(activity.durationFormatted)
                        .font(.system(size: 32, weight: .ultraLight))
                        .tracking(-1.0)
                        .foregroundStyle(DS.Colors.primaryText)
                }
                
                VStack(alignment: .leading, spacing: 4) {
                    Text("DISTANCE")
                        .font(.system(size: 10, weight: .medium))
                        .tracking(2.0)
                        .foregroundStyle(DS.Colors.outline.opacity(0.4))
                    HStack(alignment: .firstTextBaseline, spacing: 2) {
                        Text(String(format: "%.1f", (activity.distanceM ?? 0) / 1000.0))
                            .font(.system(size: 32, weight: .ultraLight))
                            .tracking(-1.0)
                            .foregroundStyle(DS.Colors.primaryText)
                        Text("km")
                            .font(.system(size: 18))
                            .foregroundStyle(DS.Colors.primaryText.opacity(0.5))
                    }
                }
                
                VStack(alignment: .leading, spacing: 4) {
                    Text("AVG_HR")
                        .font(.system(size: 10, weight: .medium))
                        .tracking(2.0)
                        .foregroundStyle(DS.Colors.outline.opacity(0.4))
                    HStack(alignment: .firstTextBaseline, spacing: 2) {
                        Text("\(activity.avgHr ?? 0)")
                            .font(.system(size: 32, weight: .ultraLight))
                            .tracking(-1.0)
                            .foregroundStyle(DS.Colors.primaryText)
                        Text("bpm")
                            .font(.system(size: 18))
                            .foregroundStyle(DS.Colors.accent.opacity(0.5))
                    }
                }
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }
    
    private var gaugesSection: some View {
        HStack(spacing: 16) {
            GlowBorderCard {
                HStack {
                    VStack(alignment: .leading, spacing: 4) {
                        Text("TRAINING LOAD")
                            .font(.system(size: 10, weight: .medium))
                            .tracking(2.0)
                            .foregroundStyle(DS.Colors.outline.opacity(0.5))
                        Text("\(Int(activity.trainingLoad ?? 0))")
                            .font(.system(size: 32, weight: .ultraLight))
                            .tracking(-1.0)
                            .foregroundStyle(DS.Colors.accent)
                        Text("PRODUCTIVE")
                            .font(.system(size: 10))
                            .foregroundStyle(DS.Colors.accent.opacity(0.6))
                    }
                    Spacer()
                    CircularProgressGauge(value: min(1.0, (activity.trainingLoad ?? 0) / 150.0), color: DS.Colors.accent, icon: "trending.up")
                }
            }
            
            GlowBorderCard {
                HStack {
                    VStack(alignment: .leading, spacing: 4) {
                        Text("EFFICIENCY")
                            .font(.system(size: 10, weight: .medium))
                            .tracking(2.0)
                            .foregroundStyle(DS.Colors.outline.opacity(0.5))
                        HStack(alignment: .firstTextBaseline, spacing: 2) {
                            Text("\(Int(efficiencyValue * 100))")
                                .font(.system(size: 32, weight: .ultraLight))
                                .tracking(-1.0)
                                .foregroundStyle(DS.Colors.primaryText)
                            Text("%")
                                .font(.system(size: 14))
                                .foregroundStyle(DS.Colors.primaryText)
                        }
                        Text("OPTIMIZED")
                            .font(.system(size: 10))
                            .foregroundStyle(DS.Colors.primaryText.opacity(0.4))
                    }
                    Spacer()
                    CircularProgressGauge(value: efficiencyValue, color: DS.Colors.primaryText, icon: "checkmark.circle")
                }
            }
        }
    }
    
    private var microMetricsSection: some View {
        HStack {
            VStack {
                Text("ELEVATION_GAIN")
                    .font(.system(size: 9, weight: .medium))
                    .tracking(0.5)
                    .foregroundStyle(DS.Colors.outline.opacity(0.4))
                    .padding(.bottom, 4)
                    .lineLimit(1)
                    .minimumScaleFactor(0.5)
                Text("\(Int(activity.totalAscentM ?? 0))m")
                    .font(.system(size: 18, weight: .medium))
                    .foregroundStyle(DS.Colors.primaryText)
            }
            .frame(maxWidth: .infinity)
            
            Divider()
                .background(Color.white.opacity(0.05))
            
            VStack {
                Text("AVG_CADENCE")
                    .font(.system(size: 9, weight: .medium))
                    .tracking(0.5)
                    .foregroundStyle(DS.Colors.outline.opacity(0.4))
                    .padding(.bottom, 4)
                    .lineLimit(1)
                    .minimumScaleFactor(0.5)
                HStack(alignment: .firstTextBaseline, spacing: 2) {
                    Text("\(activity.cadence ?? activity.avg_cadence_scraped ?? 0)")
                        .font(.system(size: 18, weight: .medium))
                        .foregroundStyle(DS.Colors.primaryText)
                    Text("spm")
                        .font(.system(size: 10))
                        .foregroundStyle(DS.Colors.primaryText.opacity(0.5))
                }
            }
            .frame(maxWidth: .infinity)
            
            Divider()
                .background(Color.white.opacity(0.05))
            
            VStack {
                Text("CALORIES_BURNED")
                    .font(.system(size: 9, weight: .medium))
                    .tracking(0.5)
                    .foregroundStyle(DS.Colors.outline.opacity(0.4))
                    .padding(.bottom, 4)
                    .lineLimit(1)
                    .minimumScaleFactor(0.5)
                HStack(alignment: .firstTextBaseline, spacing: 2) {
                    Text("\(activity.calories ?? activity.calories_scraped ?? 0)")
                        .font(.system(size: 18, weight: .medium))
                        .foregroundStyle(DS.Colors.primaryText)
                    Text("kcal")
                        .font(.system(size: 10))
                        .foregroundStyle(DS.Colors.primaryText.opacity(0.5))
                }
            }
            .frame(maxWidth: .infinity)
        }
        .padding(.vertical, 16)
        .background(Color.white.opacity(0.03))
        .background(.ultraThinMaterial)
        .clipShape(RoundedRectangle(cornerRadius: 16))
        .overlay(
            RoundedRectangle(cornerRadius: 16)
                .stroke(Color.white.opacity(0.05), lineWidth: 1)
        )
    }
    
    private var coachAnalysisSection: some View {
        GlassDeepCard {
            VStack(alignment: .leading, spacing: 0) {
                HStack(spacing: 12) {
                    RoundedRectangle(cornerRadius: 12)
                        .fill(DS.Colors.accent.opacity(0.1))
                        .frame(width: 40, height: 40)
                        .overlay(
                            Image(systemName: "sparkles")
                                .foregroundStyle(DS.Colors.accent)
                        )
                        .overlay(
                            RoundedRectangle(cornerRadius: 12)
                                .strokeBorder(DS.Colors.accent.opacity(0.2), lineWidth: 1)
                        )
                    
                    VStack(alignment: .leading, spacing: 2) {
                        Text("COACH'S ANALYSIS")
                            .font(.system(size: 12, weight: .bold))
                            .tracking(2.0)
                            .foregroundStyle(DS.Colors.accent)
                        Text("AI ENGINE v2.4_ACTVE")
                            .font(.system(size: 10, weight: .medium))
                            .tracking(2.0)
                            .foregroundStyle(DS.Colors.outline.opacity(0.4))
                    }
                }
                .padding(.bottom, 32)
                
                if isLoading {
                    ProgressView()
                        .tint(DS.Colors.accent)
                        .frame(maxWidth: .infinity, alignment: .center)
                } else if let err = errorMessage {
                    Text(err)
                        .font(.system(size: 14))
                        .foregroundStyle(DS.Colors.warning)
                } else if let coach = analysis {
                    Text(coach.analysis)
                        .font(.system(size: 18, weight: .light))
                        .lineSpacing(6)
                        .foregroundStyle(DS.Colors.primaryText)
                        .padding(.bottom, 24)
                    
                    HStack(spacing: 24) {
                        Rectangle()
                            .fill(DS.Colors.accent.opacity(0.2))
                            .frame(width: 2)
                        
                        Text("\"\(coach.advice)\"")
                            .font(.system(size: 16))
                            .italic()
                            .lineSpacing(6)
                            .foregroundStyle(DS.Colors.primaryText.opacity(0.8))
                    }
                    .padding(.bottom, 48)
                    
                    Divider().background(Color.white.opacity(0.05)).padding(.bottom, 32)
                    
                    HStack {
                        VStack(alignment: .leading, spacing: 4) {
                            Text("INTENSITY FOCUS")
                                .font(.system(size: 9, weight: .medium))
                                .tracking(2.0)
                                .foregroundStyle(DS.Colors.outline.opacity(0.3))
                            HStack(spacing: 8) {
                                Circle()
                                    .fill(DS.Colors.accent)
                                    .frame(width: 6, height: 6)
                                Text("Zone 2 Aerobic")
                                    .font(.system(size: 14, weight: .medium))
                                    .tracking(-0.5)
                                    .foregroundStyle(DS.Colors.primaryText)
                            }
                        }
                        
                        Spacer()
                        
                        Rectangle()
                            .fill(Color.white.opacity(0.05))
                            .frame(width: 1, height: 32)
                        
                        Spacer()
                        
                        VStack(alignment: .trailing, spacing: 4) {
                            Text("SYSTEM STATUS")
                                .font(.system(size: 9, weight: .medium))
                                .tracking(2.0)
                                .foregroundStyle(DS.Colors.outline.opacity(0.3))
                            Text("Fully Recovered")
                                .font(.system(size: 14, weight: .medium))
                                .tracking(-0.5)
                                .foregroundStyle(DS.Colors.primaryText)
                        }
                    }
                } else {
                    Button {
                        Task { await fetchAnalysis() }
                    } label: {
                        Text("REQUEST ANALYSIS")
                            .font(.system(size: 12, weight: .bold))
                            .tracking(2.0)
                            .padding()
                            .background(DS.Colors.surface)
                            .foregroundStyle(DS.Colors.accent)
                            .clipShape(Capsule())
                    }
                }
            }
        }
    }
    
    private var protocolSection: some View {
        HStack(alignment: .top, spacing: 16) {
            Circle()
                .fill(DS.Colors.accent.opacity(0.05))
                .frame(width: 40, height: 40)
                .overlay(
                    Image(systemName: "drop.fill")
                        .foregroundStyle(DS.Colors.accent)
                )
            
            VStack(alignment: .leading, spacing: 8) {
                Text("PROTOCOL RECOMMENDATION")
                    .font(.system(size: 10, weight: .bold))
                    .tracking(1.5)
                    .foregroundStyle(DS.Colors.accent)
                    .lineLimit(1)
                    .minimumScaleFactor(0.5)
                
                Text("Hydrate with electrolytes and prioritize sleep.")
                    .font(.system(size: 14))
                    .foregroundStyle(.white)
                    .lineSpacing(4)
            }
        }
        .padding(20)
        .background(Color.white.opacity(0.01))
        .clipShape(RoundedRectangle(cornerRadius: 16))
        .overlay(
            RoundedRectangle(cornerRadius: 16)
                .stroke(Color.white.opacity(0.1), style: StrokeStyle(lineWidth: 1, dash: [4, 4]))
        )
    }
    
    private func fetchAnalysis() async {
        guard let id = activity.id else { return }
        isLoading = true
        do {
            analysis = try await NetworkManager.shared.fetchActivityAnalysis(activityID: id)
            isLoading = false
        } catch {
            errorMessage = "Failed to load analysis."
            isLoading = false
        }
    }
    
    private func formatDate(_ date: Date) -> String {
        let formatter = DateFormatter()
        formatter.dateFormat = "MMM d, yyyy"
        return formatter.string(from: date)
    }
    
    private var efficiencyValue: Double {
        guard let rating = analysis?.rating.uppercased() else { return 0.0 }
        switch rating {
        case "A": return 0.98
        case "B": return 0.85
        case "C": return 0.75
        case "D": return 0.60
        case "F": return 0.50
        default: return 0.0
        }
    }
}
