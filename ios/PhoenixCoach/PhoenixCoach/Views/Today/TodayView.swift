import SwiftUI
import Charts

extension String: @retroactive Identifiable {
    public var id: String { self }
}

struct TodayView: View {
    @StateObject private var network = NetworkManager.shared
    @State private var weeklyPlan: WeeklyPlanResponse?
    @State private var planStatus: WeeklyPlanStatusResponse?
    @State private var dashboard: DashboardResponse?
    @State private var refreshResponse: SmartRefreshResponse?
    
    @State private var isLoading = false
    @State private var isSyncing = false
    @State private var syncMessage = ""
    @State private var errorMessage: String?
    @State private var showConnectionSettings = false
    
    @State private var showScraperError = false
    @State private var scraperErrorMessage = ""
    
    @State private var showHRVChart = false
    @State private var showRHRChart = false
    @State private var showLoadChart = false
    
    // Design system colors matching Quiet Performance HTML mockup
                                                   
    private var todayDayName: String {
        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "en_US")
        formatter.dateFormat = "EEEE"
        return formatter.string(from: Date())
    }
    
    private var latestRecovery: RecoverySummary? {
        if let ref = refreshResponse {
            return ref.recovery
        }
        if let dash = dashboard, let first = dash.recovery.first {
            return RecoverySummary(
                hrvMs: first.hrvMs,
                restingHr: first.restingHr,
                loadRatio: first.loadRatio,
                loadRatioLabel: nil,
                cti: first.cti,
                ati: first.ati,
                tib: first.tib,
                fatigueState: nil,
                staminaLevel: dash.athlete?.staminaLevel
            )
        }
        return nil
    }
    
    private var todayDayPlan: DayPlan? {
        if let statusDay = planStatus?.days[todayDayName] {
            return DayPlan(
                summary: statusDay.summary,
                workouts: statusDay.workouts,
                rationale: statusDay.rationale,
                coachNote: statusDay.coachNote,
                adaptation: statusDay.adaptation,
                originalWorkouts: statusDay.originalWorkouts
            )
        }
        if let rec = weeklyPlan?.days[todayDayName] {
            return rec
        }
        return nil
    }
    
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
                
                if let err = errorMessage, weeklyPlan == nil, dashboard == nil {
                    errorView(err)
                } else {
                    ScrollView {
                        VStack(spacing: 24) {
                            statusPill
                            
                            VStack(spacing: 12) {
                                HStack(spacing: 12) {
                                    hrvCard
                                    rhrCard
                                }
                                loadRatioCard
                            }
                            
                            timelineLink
                            
                            workoutProtocolSection
                            
                            rationaleSection
                        }
                        .padding()
                        .opacity(isSyncing ? 0.3 : 1.0)
                    }
                    .refreshable {
                        UIImpactFeedbackGenerator(style: .medium).impactOccurred()
                        await performSmartRefresh()
                    }
                }
                
            }
            .navigationTitle("")
            .navigationBarTitleDisplayMode(.inline)
            .sheet(isPresented: $showHRVChart) {
                MetricChartSheet(title: "HRV (ms)", data: dashboard?.recovery ?? [], metricType: .hrv)
            }
            .sheet(isPresented: $showRHRChart) {
                MetricChartSheet(title: "Resting HR (bpm)", data: dashboard?.recovery ?? [], metricType: .rhr)
            }
            .sheet(isPresented: $showLoadChart) {
                MetricChartSheet(title: "Load Ratio", data: dashboard?.recovery ?? [], metricType: .load)
            }
            .alert("Scraper Error", isPresented: $showScraperError) {
                Button("OK", role: .cancel) { }
            } message: {
                Text(scraperErrorMessage)
            }
            .task {
                if weeklyPlan == nil {
                    await loadInitialData()
                }
            }
        }
    }
    
    // MARK: - UI Components
    
    private var statusPill: some View {
        HStack(spacing: 8) {
            Circle()
                .fill(network.isConnected ? Color.green : Color.red)
                .frame(width: 8, height: 8)
            
            Text(isSyncing ? syncMessage : (network.isConnected ? "Biometrics Synced" : "Connection Offline"))
                .font(.system(size: 11, weight: .medium))
                .tracking(1.1)
                .foregroundStyle(DS.Colors.primaryText)
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 8)
        .background(.ultraThinMaterial)
        .clipShape(Capsule())
        .overlay(
            Capsule()
                .stroke(Color.white.opacity(0.1), lineWidth: 1)
        )
        .accessibilityLabel(isSyncing ? "Syncing" : (network.isConnected ? "Biometrics Synced" : "Connection Offline"))
    }
    
    private var hrvCard: some View {
        Button(action: { if dashboard?.recovery.isEmpty == false { showHRVChart = true } }) {
            VStack(alignment: .leading, spacing: 8) {
                HStack {
                    Image(systemName: "heart.text.square")
                        .font(.subheadline)
                        .foregroundStyle(DS.Colors.accent)
                    Spacer()
                    Text("HRV")
                        .font(.system(size: 11, weight: .bold))
                        .tracking(1.1)
                        .foregroundStyle(DS.Colors.outline)
                }
                
                Spacer()
                
                HStack(alignment: .bottom, spacing: 2) {
                    if let hrv = latestRecovery?.hrvMs {
                        Text("\(Int(hrv))")
                            .font(.system(size: 36, weight: .ultraLight))
                            .foregroundStyle(DS.Colors.primaryText)
                        Text("ms")
                            .font(.caption2)
                            .foregroundStyle(DS.Colors.outline)
                            .padding(.bottom, 6)
                    } else {
                        Text("--")
                            .font(.system(size: 36, weight: .ultraLight))
                            .foregroundStyle(DS.Colors.outline)
                    }
                }
                
                Spacer()
                
                if let hrv = latestRecovery?.hrvMs, let baseline = dashboard?.athlete?.hrvBaseline, baseline > 0 {
                    let pctDiff = ((hrv - baseline) / baseline) * 100.0
                    let sign = pctDiff >= 0 ? "+" : ""
                    Text("\(sign)\(Int(pctDiff))% vs baseline")
                        .font(.system(size: 10, weight: .medium))
                        .foregroundStyle(pctDiff >= -15 ? .green : .red)
                } else {
                    Text("No baseline")
                        .font(.system(size: 10, weight: .regular))
                        .foregroundStyle(DS.Colors.outline)
                }
            }
            .frame(maxWidth: .infinity, minHeight: 120)
            .glassCard()
        }
        .buttonStyle(.plain)
        .accessibilityElement(children: .combine)
    }
    
    private var rhrCard: some View {
        Button(action: { if dashboard?.recovery.isEmpty == false { showRHRChart = true } }) {
            VStack(alignment: .leading, spacing: 8) {
                HStack {
                    Image(systemName: "heart.fill")
                        .font(.subheadline)
                        .foregroundStyle(DS.Colors.accent)
                    Spacer()
                    Text("RHR")
                        .font(.system(size: 11, weight: .bold))
                        .tracking(1.1)
                        .foregroundStyle(DS.Colors.outline)
                }
                
                Spacer()
                
                HStack(alignment: .bottom, spacing: 2) {
                    if let rhr = latestRecovery?.restingHr {
                        Text("\(rhr)")
                            .font(.system(size: 36, weight: .ultraLight))
                            .foregroundStyle(DS.Colors.primaryText)
                        Text("bpm")
                            .font(.caption2)
                            .foregroundStyle(DS.Colors.outline)
                            .padding(.bottom, 6)
                    } else {
                        Text("--")
                            .font(.system(size: 36, weight: .ultraLight))
                            .foregroundStyle(DS.Colors.outline)
                    }
                }
                
                Spacer()
                
                if let rhr = latestRecovery?.restingHr, let baseRhr = dashboard?.athlete?.hrRest {
                    let diff = rhr - baseRhr
                    let sign = diff >= 0 ? "+" : ""
                    Text("\(sign)\(diff) bpm vs rest")
                        .font(.system(size: 10, weight: .medium))
                        .foregroundStyle(diff <= 5 ? .green : .red)
                } else {
                    Text("No baseline")
                        .font(.system(size: 10, weight: .regular))
                        .foregroundStyle(DS.Colors.outline)
                }
            }
            .frame(maxWidth: .infinity, minHeight: 120)
            .glassCard()
        }
        .buttonStyle(.plain)
        .accessibilityElement(children: .combine)
    }
    
    private var loadRatioCard: some View {
        Button(action: { if dashboard?.recovery.isEmpty == false { showLoadChart = true } }) {
            VStack(alignment: .leading, spacing: 12) {
                HStack {
                    Image(systemName: "waveform.path.ecg")
                        .font(.subheadline)
                        .foregroundStyle(DS.Colors.accent)
                    
                    Text("LOAD RATIO")
                        .font(.system(size: 11, weight: .bold))
                        .tracking(1.1)
                        .foregroundStyle(DS.Colors.outline)
                    
                    Spacer()
                    
                    let label = latestRecovery?.loadRatioLabel ?? loadRatioLabel(for: latestRecovery?.loadRatio)
                    Text(label.uppercased())
                        .font(.system(size: 9, weight: .bold))
                        .foregroundStyle(.black)
                        .padding(.horizontal, 8)
                        .padding(.vertical, 3)
                        .background(badgeColor(for: label))
                        .clipShape(Capsule())
                }
                
                HStack(alignment: .center, spacing: 16) {
                    HStack(alignment: .bottom, spacing: 2) {
                        if let ratio = latestRecovery?.loadRatio {
                            Text(String(format: "%.2f", ratio))
                                .font(.system(size: 36, weight: .ultraLight))
                                .foregroundStyle(DS.Colors.primaryText)
                        } else {
                            Text("--")
                                .font(.system(size: 36, weight: .ultraLight))
                                .foregroundStyle(DS.Colors.outline)
                        }
                    }
                    
                    VStack(alignment: .leading, spacing: 2) {
                        Text("Injury risk evaluation based on acute vs chronic load")
                            .font(.caption2)
                            .foregroundStyle(DS.Colors.onSurface)
                        
                        if let cti = latestRecovery?.cti, let ati = latestRecovery?.ati {
                            Text("ATL: \(Int(ati)) • CTL: \(Int(cti))")
                                .font(.system(size: 10, weight: .medium))
                                .foregroundStyle(DS.Colors.outline)
                        }
                    }
                }
            }
            .frame(maxWidth: .infinity)
            .glassCard()
        }
        .buttonStyle(.plain)
        .accessibilityElement(children: .combine)
    }
    
    private var timelineLink: some View {
        NavigationLink(destination: BlockCalendarView()) {
            HStack(spacing: 12) {
                Image(systemName: "chart.line.uptrend.xyaxis")
                    .font(.title3)
                    .foregroundStyle(DS.Colors.accent)
                
                VStack(alignment: .leading, spacing: 2) {
                    Text("TRAINING TIMELINE")
                        .font(.system(size: 11, weight: .semibold))
                        .tracking(1.1)
                        .foregroundStyle(DS.Colors.outline)
                    Text("View training phases and full calendar")
                        .font(.caption2)
                        .foregroundStyle(.secondary)
                }
                
                Spacer()
                
                Image(systemName: "chevron.right")
                    .font(.footnote)
                    .foregroundStyle(DS.Colors.outline)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .glassCard()
        }
        .buttonStyle(.plain)
    }
    
    private var workoutProtocolSection: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("TODAY'S WORKOUT PROTOCOL")
                .font(.system(size: 11, weight: .bold))
                .tracking(1.1)
                .foregroundStyle(DS.Colors.outline)
                .padding(.horizontal, 4)
            
            if let todayPlan = todayDayPlan {
                let hasAdaptation = todayPlan.adaptation != nil && !(todayPlan.adaptation?.isEmpty ?? true)
                
                if hasAdaptation {
                    ScrollView(.horizontal, showsIndicators: false) {
                        HStack(spacing: 16) {
                            ProtocolCard(
                                cardTitle: "Original Protocol",
                                workouts: todayPlan.originalWorkouts ?? todayPlan.workouts ?? [],
                                rationale: todayPlan.rationale,
                                coachNote: todayPlan.coachNote,
                                isAdapted: false,
                                adaptationReason: nil
                            )
                            .frame(width: 320)
                            
                            ProtocolCard(
                                cardTitle: "AI Adapted Protocol",
                                workouts: todayPlan.workouts ?? [],
                                rationale: todayPlan.rationale,
                                coachNote: todayPlan.coachNote,
                                isAdapted: true,
                                adaptationReason: todayPlan.adaptation
                            )
                            .frame(width: 320)
                        }
                        .padding(.horizontal, 4)
                    }
                } else {
                    ProtocolCard(
                        cardTitle: "Original Protocol",
                        workouts: todayPlan.workouts ?? [],
                        rationale: todayPlan.rationale,
                        coachNote: todayPlan.coachNote,
                        isAdapted: false,
                        adaptationReason: nil
                    )
                }
            } else {
                emptyDayCard
            }
        }
    }
    
    private var rationaleSection: some View {
        Group {
            if let todayPlan = todayDayPlan {
                VStack(alignment: .leading, spacing: 14) {
                    Text("COACH'S RATIONALE & NOTE")
                        .font(.system(size: 11, weight: .bold))
                        .tracking(1.1)
                        .foregroundStyle(DS.Colors.outline)
                    
                    if let rationale = todayPlan.rationale, !rationale.isEmpty {
                        VStack(alignment: .leading, spacing: 4) {
                            Text("RATIONALE")
                                .font(.system(size: 9, weight: .bold))
                                .tracking(0.9)
                                .foregroundStyle(DS.Colors.accent)
                            Text(rationale)
                                .font(.system(size: 13))
                                .foregroundStyle(DS.Colors.onSurface)
                                .lineSpacing(3)
                        }
                    }
                    
                    if let note = todayPlan.coachNote, !note.isEmpty {
                        VStack(alignment: .leading, spacing: 4) {
                            Text("COACH NOTE")
                                .font(.system(size: 9, weight: .bold))
                                .tracking(0.9)
                                .foregroundStyle(DS.Colors.accent)
                            Text(note)
                                .font(.system(size: 13).italic())
                                .foregroundStyle(DS.Colors.onSurface)
                                .lineSpacing(3)
                        }
                    }
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                .glassCard()
            }
        }
    }
    
    private var emptyDayCard: some View {
        VStack(spacing: 12) {
            Image(systemName: "zzz")
                .font(.largeTitle)
                .foregroundStyle(DS.Colors.outline)
            Text("Rest Day")
                .font(.headline.bold())
                .foregroundStyle(DS.Colors.primaryText)
            Text("No structured training scheduled for today. Focus on active recovery, stretching, or general wellness.")
                .font(.caption)
                .foregroundStyle(DS.Colors.onSurface)
                .multilineTextAlignment(.center)
        }
        .frame(maxWidth: .infinity)
        .padding(.vertical, 24)
        .glassCard()
    }
    
    private var syncingOverlay: some View {
        ZStack {
            Color.black.opacity(0.6)
                .ignoresSafeArea()
            
            VStack(spacing: 16) {
                ProgressView()
                    .controlSize(.large)
                    .tint(DS.Colors.accent)
                
                Text(syncMessage)
                    .font(.headline)
                    .foregroundStyle(DS.Colors.primaryText)
                    .multilineTextAlignment(.center)
                    .padding(.horizontal)
            }
            .padding(24)
            .background(.ultraThinMaterial)
            .clipShape(RoundedRectangle(cornerRadius: 12))
            .overlay(
                RoundedRectangle(cornerRadius: 12)
                    .stroke(Color.white.opacity(0.1), lineWidth: 1)
            )
        }
    }
    
    // MARK: - Logic & Network Helpers
    
    private func loadInitialData() async {
        isSyncing = true
        syncMessage = "Loading training data..."
        async let dashTask: () = fetchDashboard()
        async let planTask: () = fetchWeeklyPlan()
        async let statusTask: () = fetchPlanStatus()
        _ = await (dashTask, planTask, statusTask)
        isSyncing = false
    }
    
    private func fetchWeeklyPlan() async {
        isLoading = true
        errorMessage = nil
        do {
            let plan = try await network.fetchWeeklyPlan()
            await MainActor.run {
                self.weeklyPlan = plan
                self.isLoading = false
            }
        } catch {
            await MainActor.run {
                self.errorMessage = error.localizedDescription
                self.isLoading = false
            }
        }
    }
    
    private func fetchPlanStatus() async {
        do {
            let status = try await network.fetchWeeklyPlanStatus()
            await MainActor.run {
                self.planStatus = status
                
                let todayDayName = DateFormatter().shortWeekdaySymbols[Calendar.current.component(.weekday, from: Date()) - 1].lowercased()
                let workoutTitle = status.days[todayDayName]?.workouts?.first?.title
                NotificationManager.shared.scheduleMorningReadiness(workoutTitle: workoutTitle)
            }
        } catch {
            print("Plan status fetch error: \(error)")
        }
    }
    
    private func fetchDashboard() async {
        do {
            let dash = try await network.fetchDashboard()
            await MainActor.run {
                self.dashboard = dash
                
                // Trigger notification logic if applicable
                if let ratio = dash.recovery.first?.loadRatio {
                    NotificationManager.shared.triggerLoadAlert(loadRatio: ratio)
                }
            }
        } catch {
            print("Dashboard fetch error: \(error)")
        }
    }
    
    private func performSmartRefresh() async {
        isSyncing = true
        syncMessage = "Connecting to backend..."
        errorMessage = nil
        
        do {
            try await Task.sleep(nanoseconds: 800_000_000)
            await MainActor.run { self.syncMessage = "Scraping COROS web..." }
            try await Task.sleep(nanoseconds: 1_200_000_000)
            await MainActor.run { self.syncMessage = "Analyzing Data..." }
            
            let response = try await network.smartRefresh()
            await MainActor.run {
                self.refreshResponse = response
                self.syncMessage = "Finishing up..."
                if response.syncStatus == "partial" {
                    self.scraperErrorMessage = response.syncMessage ?? "Data could not be scraped."
                    self.showScraperError = true
                }
            }
            
            async let planTask: () = fetchWeeklyPlan()
            async let statusTask: () = fetchPlanStatus()
            _ = await (planTask, statusTask)
            
            await MainActor.run {
                DispatchQueue.main.asyncAfter(deadline: .now() + 1.0) {
                    self.isSyncing = false
                    UIImpactFeedbackGenerator(style: .medium).impactOccurred()
                }
            }
        } catch {
            await MainActor.run {
                self.errorMessage = "Sync failed: \(error.localizedDescription)"
                self.isSyncing = false
            }
        }
    }
    
    private func loadRatioLabel(for ratio: Double?) -> String {
        guard let ratio = ratio else { return "UNKNOWN" }
        if ratio < 0.8 { return "DETRAINING" }
        if ratio <= 1.3 { return "OPTIMAL" }
        if ratio <= 1.5 { return "OVERREACHING" }
        return "HIGH RISK"
    }
    
    private func badgeColor(for label: String) -> Color {
        switch label.uppercased() {
        case "OPTIMAL":
            return .green
        case "DETRAINING", "OVERREACHING":
            return .orange
        case "HIGH RISK":
            return .red
        default:
            return .gray
        }
    }
    
    private func errorView(_ error: String) -> some View {
        VStack(spacing: 16) {
            Image(systemName: "exclamationmark.triangle.fill")
                .font(.largeTitle)
                .foregroundStyle(DS.Colors.accent.opacity(0.8))
            Text(error)
                .font(.system(size: 13, weight: .medium))
                .foregroundStyle(DS.Colors.onSurface)
                .multilineTextAlignment(.center)
                .padding(.horizontal)
            Button("Retry") {
                Task {
                    await loadInitialData()
                }
            }
            .font(.system(size: 13, weight: .semibold))
            .foregroundStyle(.black)
            .padding(.horizontal, 20)
            .padding(.vertical, 8)
            .background(DS.Colors.accent.opacity(0.8))
            .clipShape(Capsule())
        }
    }
}

// MARK: - ProtocolCard Component

struct ProtocolCard: View {
    let cardTitle: String
    let workouts: [Workout]
    let rationale: String?
    let coachNote: String?
    let isAdapted: Bool
    let adaptationReason: String?
    
    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            HStack {
                Text(cardTitle.uppercased())
                    .font(.system(size: 11, weight: .bold))
                    .tracking(1.1)
                    .foregroundStyle(isAdapted ? DS.Colors.accent : DS.Colors.outline)
                
                Spacer()
                
                if isAdapted {
                    Text("OPTIMIZED")
                        .font(.system(size: 8, weight: .bold))
                        .foregroundStyle(.black)
                        .padding(.horizontal, 6)
                        .padding(.vertical, 2)
                        .background(DS.Colors.accent)
                        .clipShape(Capsule())
                }
            }
            
            if isAdapted, let reason = adaptationReason {
                Text("Reason: \(reason)")
                    .font(.system(size: 11, weight: .medium))
                    .foregroundStyle(DS.Colors.accent)
                    .padding(8)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .background(DS.Colors.accent.opacity(0.1))
                    .clipShape(RoundedRectangle(cornerRadius: 8))
            }
            
            if workouts.isEmpty {
                Text("No activities planned today.")
                    .font(.subheadline)
                    .foregroundStyle(DS.Colors.onSurface)
                    .frame(maxWidth: .infinity, alignment: .center)
                    .padding(.vertical, 20)
            } else {
                ForEach(workouts, id: \.title) { workout in
                    VStack(alignment: .leading, spacing: 12) {
                        HStack {
                            Text(workout.sportEmoji)
                                .font(.title3)
                            Text(workout.title)
                                .font(.headline.bold())
                                .foregroundStyle(DS.Colors.primaryText)
                            Spacer()
                            if let time = workout.totalTime {
                                Text(time)
                                    .font(.subheadline.bold())
                                    .foregroundStyle(DS.Colors.accent)
                            }
                        }
                        
                        if let hr = workout.hrTarget {
                            Text("Target HR: \(hr)")
                                .font(.caption)
                                .foregroundStyle(DS.Colors.outline)
                        }
                        
                        if !workout.steps.isEmpty {
                            VStack(alignment: .leading, spacing: 8) {
                                ForEach(Array(workout.steps.enumerated()), id: \.offset) { index, step in
                                    HStack(alignment: .top, spacing: 10) {
                                        VStack(spacing: 0) {
                                            Circle()
                                                .fill(isAdapted ? DS.Colors.accent : stepColor(for: step.type))
                                                .frame(width: 8, height: 8)
                                                .padding(.top, 4)
                                            
                                            if index < workout.steps.count - 1 {
                                                Rectangle()
                                                    .fill(DS.Colors.outline.opacity(0.3))
                                                    .frame(width: 1, height: 20)
                                            }
                                        }
                                        
                                        VStack(alignment: .leading, spacing: 2) {
                                            HStack {
                                                Text(step.type.uppercased())
                                                    .font(.system(size: 9, weight: .bold))
                                                    .foregroundStyle(stepColor(for: step.type))
                                                Spacer()
                                                Text(step.duration)
                                                    .font(.system(size: 10, weight: .bold))
                                                    .foregroundStyle(DS.Colors.primaryText)
                                            }
                                            
                                            if let desc = step.description {
                                                Text(desc)
                                                    .font(.caption2)
                                                    .foregroundStyle(DS.Colors.onSurface)
                                            }
                                        }
                                    }
                                }
                            }
                            .padding(.leading, 4)
                        }
                    }
                }
            }
        }
        .padding(16)
        .frame(maxWidth: .infinity)
        .glassCard()
    }
    
    private func stepColor(for type: String) -> Color {
        switch type.lowercased() {
        case "warmup": return .blue
        case "main": return .purple
        case "recovery": return .green
        case "cooldown": return .teal
        default: return .gray
        }
    }
}

// MARK: - View Modifiers & Extensions



#Preview {
    TodayView()
        .preferredColorScheme(.dark)
}

// MARK: - Workout Extension

extension Workout {
    var sportEmoji: String {
        switch sport.lowercased() {
        case "running": return "🏃"
        case "cycling": return "🚴"
        case "swimming": return "🏊"
        case "strength": return "🏋️"
        case "rest": return "😴"
        default: return "🏅"
        }
    }
}

struct ConnectionSettingsSheet: View {
    @Environment(\.dismiss) private var dismiss
    @ObservedObject private var network = NetworkManager.shared
    @State private var urlText: String = ""
    @State private var isTesting: Bool = false
    
    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(spacing: 24) {
                    // Header card
                    VStack(spacing: 8) {
                        Image(systemName: network.isConnected ? "wifi" : "wifi.slash")
                            .font(.system(size: 48))
                            .foregroundStyle(network.isConnected ? .green : .orange)
                            .symbolEffect(.bounce, value: network.isConnected)
                        
                        Text(network.isConnected ? "Connection Stable" : "Connection Offline")
                            .font(.title2.bold())
                        
                        Text("Phoenix Coach relies on a local FastAPI server running on your Mac for periodization, scraping, and LLM planning.")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                            .multilineTextAlignment(.center)
                            .padding(.horizontal)
                    }
                    .padding()
                    .frame(maxWidth: .infinity)
                    .background(Color.orange.opacity(0.05))
                    .clipShape(RoundedRectangle(cornerRadius: 8))
                    
                    // Input Card
                    VStack(alignment: .leading, spacing: 14) {
                        Text("SERVER CONFIGURATION")
                            .font(.caption.bold())
                            .foregroundStyle(.orange)
                        
                        TextField("http://192.168.x.x:8001", text: $urlText)
                            .textFieldStyle(.roundedBorder)
                            .autocorrectionDisabled()
                            .textInputAutocapitalization(.never)
                            .keyboardType(.URL)
                            .font(.body.monospaced())
                        
                        HStack(spacing: 12) {
                            Button {
                                isTesting = true
                                network.baseURL = urlText
                                Task {
                                    await network.checkConnection()
                                    isTesting = false
                                }
                            } label: {
                                HStack {
                                    if isTesting {
                                        ProgressView()
                                            .controlSize(.small)
                                            .padding(.trailing, 4)
                                    }
                                    Text("Test & Apply")
                                        .bold()
                                }
                                .frame(maxWidth: .infinity)
                                .padding(.vertical, 10)
                                .background(Color.orange)
                                .foregroundStyle(.white)
                                .clipShape(RoundedRectangle(cornerRadius: 8))
                            }
                            .disabled(isTesting)
                            
                            Button {
                                network.resetToDefaultURL()
                                urlText = network.baseURL
                            } label: {
                                Text("Reset Default")
                                    .bold()
                                    .frame(maxWidth: .infinity)
                                    .padding(.vertical, 10)
                                    .background(Color(.systemGray5))
                                    .foregroundStyle(.primary)
                                    .clipShape(RoundedRectangle(cornerRadius: 8))
                            }
                        }
                    }
                    .padding()
                    .background(.ultraThinMaterial)
                    .clipShape(RoundedRectangle(cornerRadius: 8))
                    
                    // Diagnostics Section
                    VStack(alignment: .leading, spacing: 12) {
                        Text("DIAGNOSTICS & STATUS")
                            .font(.caption.bold())
                            .foregroundStyle(.secondary)
                        
                        statusRow(title: "Backend FastAPI", status: network.isConnected ? "Online" : "Offline", isOk: network.isConnected)
                        statusRow(title: "Mac Ollama API", status: network.isConnected ? (network.isOllamaConnected ? "Running" : "Offline") : "N/A", isOk: network.isConnected && network.isOllamaConnected)
                        statusRow(title: "Device Local LLM", status: "Ready", isOk: true)
                    }
                    .padding()
                    .background(Color(.secondarySystemGroupedBackground))
                    .clipShape(RoundedRectangle(cornerRadius: 8))
                    
                    // Troubleshooting Guide
                    VStack(alignment: .leading, spacing: 10) {
                        Text("Troubleshooting Guide")
                            .font(.headline)
                        
                        bulletPoint("Make sure your Mac backend is running via the command `PYTHONPATH=. python3 backend/main.py` in the workspace.")
                        bulletPoint("Ensure both your iPhone/device and Mac are connected to the exact same Wi-Fi network.")
                        bulletPoint("Check that the app has internet access. You can test in your Mac browser at `https://phoenix-coach.onrender.com/health`.")
                    }
                    .padding(.horizontal, 4)
                }
                .padding()
            }
            .navigationTitle("Connection Settings")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button("Done") {
                        dismiss()
                    }
                    .bold()
                    .tint(.white)
                }
            }
            .onAppear {
                urlText = network.baseURL
            }
        }
    }
    
    private func statusRow(title: String, status: String, isOk: Bool) -> some View {
        HStack {
            Text(title)
                .font(.subheadline)
            Spacer()
            HStack(spacing: 6) {
                Circle()
                    .fill(isOk ? Color.green : Color.red)
                    .frame(width: 8, height: 8)
                Text(status)
                    .font(.caption.bold())
                    .foregroundStyle(isOk ? .green : .red)
            }
        }
    }
    
    private func bulletPoint(_ text: String) -> some View {
        HStack(alignment: .top, spacing: 6) {
            Text("•")
                .foregroundStyle(DS.Colors.warning)
            Text(text)
                .font(.caption)
                .foregroundStyle(.secondary)
        }
    }
}

enum MetricType {
    case hrv, rhr, load
}

struct MetricChartSheet: View {
    @Environment(\.dismiss) var dismiss
    let title: String
    let data: [RecoverySnapshot]
    let metricType: MetricType
    
    // Sort chronological and take the last 7
    private var chartData: [RecoverySnapshot] {
        let sorted = data.sorted { ($0.date ?? "") < ($1.date ?? "") }
        return Array(sorted.suffix(7))
    }
    
    var body: some View {
        NavigationStack {
            ZStack {
                DS.Colors.background.ignoresSafeArea()
                
                VStack(spacing: 24) {
                    if chartData.isEmpty {
                        ContentUnavailableView("No Data", systemImage: "chart.xyaxis.line")
                    } else {
                        Chart {
                            ForEach(chartData, id: \.id) { item in
                                if let value = value(for: item) {
                                    LineMark(
                                        x: .value("Day", dateString(for: item) ?? "?"),
                                        y: .value(title, value)
                                    )
                                    .interpolationMethod(.catmullRom)
                                    .foregroundStyle(DS.Colors.accent)
                                    .symbol(Circle())
                                }
                            }
                        }
                        .chartYAxis {
                            AxisMarks(position: .leading)
                        }
                        .frame(height: 250)
                        .padding()
                        .glassCard()
                    }
                    Spacer()
                }
                .padding(24)
            }
            .navigationTitle(title)
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button("Done") { dismiss() }
                        .foregroundStyle(DS.Colors.accent)
                        .font(.body.bold())
                }
            }
        }
        .presentationDetents([.medium])
        .presentationBackground(.ultraThinMaterial)
    }
    
    private func value(for item: RecoverySnapshot) -> Double? {
        switch metricType {
        case .hrv: return item.hrvMs
        case .rhr: return item.restingHr.map { Double($0) }
        case .load: return item.loadRatio
        }
    }
    
    private func dateString(for item: RecoverySnapshot) -> String? {
        guard let dateStr = item.date else { return nil }
        let formatter = DateFormatter()
        formatter.dateFormat = "yyyy-MM-dd"
        guard let date = formatter.date(from: dateStr) else { return nil }
        formatter.dateFormat = "E" // short day like Mon, Tue
        return formatter.string(from: date)
    }
}
