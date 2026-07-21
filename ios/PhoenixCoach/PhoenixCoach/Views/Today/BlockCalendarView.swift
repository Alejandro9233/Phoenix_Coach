import SwiftUI

struct BlockCalendarView: View {
    @StateObject private var network = NetworkManager.shared
    @State private var calendarData: BlockCalendarResponse?
    @State private var isLoading = false
    @State private var errorMessage: String?
    @State private var showRegenConfirmation = false
    @State private var isRegenerating = false
    
    // Tracks which phases are currently expanded
    @State private var expandedPhases: Set<String> = []
    
    // Holds the week that was tapped to view its daily workouts
    @State private var selectedWeekDetail: BlockWeek? = nil
    
    // MARK: - Design System (Quiet Performance)
    
    private static let backgroundColor = Color(red: 0.075, green: 0.075, blue: 0.082)        // #131315
    private static let surfaceColor = Color(red: 0.122, green: 0.122, blue: 0.129)            // #1F1F21
    private static let primaryTextColor = Color(red: 0.784, green: 0.776, blue: 0.780)        // #C8C6C7
    private static let accentColor = Color(red: 1.0, green: 0.71, blue: 0.604)                // #FFB59A
    private static let outlineColor = Color(red: 0.569, green: 0.565, blue: 0.580)            // #919094
    private static let onSurfaceVariantColor = Color(red: 0.780, green: 0.776, blue: 0.792)   // #C7C6CA
    
    // MARK: - Computed Properties
    
    /// Current day index (0 = Monday, 6 = Sunday)
    private var currentDayIndex: Int {
        let weekday = Calendar.current.component(.weekday, from: Date())
        // Calendar weekday: 1 = Sunday, 2 = Monday, ..., 7 = Saturday
        return weekday == 1 ? 6 : weekday - 2
    }
    
    /// Groups weeks by phase name, preserving order
    private var groupedWeeks: [(String, [BlockWeek])] {
        guard let data = calendarData else { return [] }
        var groups: [(String, [BlockWeek])] = []
        var currentPhase = ""
        var currentGroup: [BlockWeek] = []
        
        for week in data.weeks {
            if week.phaseName != currentPhase {
                if !currentGroup.isEmpty {
                    groups.append((currentPhase, currentGroup))
                }
                currentPhase = week.phaseName
                currentGroup = [week]
            } else {
                currentGroup.append(week)
            }
        }
        if !currentGroup.isEmpty {
            groups.append((currentPhase, currentGroup))
        }
        return groups
    }
    
    // MARK: - Body
    
    var body: some View {
        ZStack {
            Self.backgroundColor.ignoresSafeArea()
            
            if isLoading {
                ProgressView("Loading timeline...")
                    .scaleEffect(1.1)
                    .foregroundStyle(Self.outlineColor)
            } else if let error = errorMessage {
                errorView(error)
            } else if calendarData != nil {
                timelineContent
            }
        }
        .navigationTitle("Training Timeline")
        .navigationBarTitleDisplayMode(.inline)
        .toolbarBackground(Self.backgroundColor, for: .navigationBar)
        .toolbarColorScheme(.dark, for: .navigationBar)
        .toolbar {
            ToolbarItem(placement: .topBarTrailing) {
                Button(action: {
                    showRegenConfirmation = true
                }) {
                    if isRegenerating {
                        ProgressView()
                            .controlSize(.small)
                    } else {
                        Image(systemName: "arrow.triangle.2.circlepath")
                            .font(.body.bold())
                            .foregroundStyle(Self.accentColor)
                    }
                }
                .disabled(isRegenerating)
            }
        }
        .alert("Regenerate Weekly Plan?", isPresented: $showRegenConfirmation) {
            Button("Cancel", role: .cancel) { }
            Button("Regenerate", role: .destructive) {
                Task {
                    await regenerateWeeklyPlan()
                }
            }
        } message: {
            Text("This will rewrite the active workouts for the current week based on latest fitness metrics. Past completed activities are preserved.")
        }
        .sheet(item: $selectedWeekDetail) { week in
            WeeklyWorkoutsDetailSheet(week: week)
        }
        .task {
            await loadData()
        }
    }
    
    // MARK: - Timeline Content
    
    private var timelineContent: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 0) {
                // Header summary
                if let data = calendarData {
                    headerSummary(data)
                        .padding(.horizontal, 20)
                        .padding(.bottom, 24)
                }
                
                // Phase groups with timeline
                ForEach(Array(groupedWeeks.enumerated()), id: \.offset) { index, group in
                    let (phaseName, weeks) = group
                    let isExpanded = expandedPhases.contains(phaseName)
                    let phaseType = weeks.first?.phase ?? ""
                    let isLastPhase = index == groupedWeeks.count - 1
                    
                    // Determine phase status
                    let hasCurrentWeek = weeks.contains(where: { $0.isCurrentWeek })
                    let currentWeekNum = calendarData?.currentWeekNumber ?? 1
                    let isActivePhase = hasCurrentWeek
                    let isPastPhase = !isActivePhase && (weeks.last?.weekNumber ?? 0) < currentWeekNum
                    let isFuturePhase = !isActivePhase && !isPastPhase
                    
                    VStack(alignment: .leading, spacing: 0) {
                        // Phase header
                        phaseHeader(
                            phaseName: phaseName,
                            phaseType: phaseType,
                            weeks: weeks,
                            isExpanded: isExpanded,
                            isActive: isActivePhase,
                            isPast: isPastPhase
                        )
                        
                        if isExpanded {
                            if isFuturePhase {
                                futurePhasePlaceholder(currentWeekNumber: currentWeekNum)
                                    .padding(.leading, 36)
                                    .padding(.trailing, 20)
                                    .padding(.top, 12)
                                    .padding(.bottom, 16)
                            } else {
                                VStack(alignment: .leading, spacing: 0) {
                                    ForEach(weeks) { week in
                                        let isLastInPhase = week.id == weeks.last?.id
                                        weekCardRow(
                                            week: week,
                                            currentWeekNum: currentWeekNum,
                                            showConnector: !isLastInPhase || !isLastPhase
                                        )
                                    }
                                }
                            }
                        }
                    }
                }
                
                // Bottom spacer
                Spacer().frame(height: 40)
            }
            .padding(.top, 16)
        }
        .scrollIndicators(.hidden)
    }
    
    // MARK: - Header Summary
    
    private func headerSummary(_ data: BlockCalendarResponse) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(spacing: 8) {
                Image(systemName: "trophy.fill")
                    .font(.caption)
                    .foregroundStyle(Self.accentColor)
                
                Text(data.raceName.uppercased())
                    .font(.system(size: 11, weight: .semibold))
                    .foregroundStyle(Self.accentColor)
                    .tracking(1.2)
            }
            
            if let raceDate = data.raceDate {
                Text(formatRaceDate(raceDate))
                    .font(.system(size: 20, weight: .bold))
                    .foregroundStyle(Self.primaryTextColor)
            }
            
            HStack(spacing: 16) {
                statPill(label: "TOTAL", value: "\(data.totalWeeks) weeks")
                statPill(label: "CURRENT", value: "Week \(data.currentWeekNumber)")
            }
            .padding(.top, 4)
        }
        .padding(16)
        .background(Self.surfaceColor)
        .clipShape(RoundedRectangle(cornerRadius: 8))
        .overlay(
            RoundedRectangle(cornerRadius: 8)
                .stroke(Color.white.opacity(0.06), lineWidth: 1)
        )
    }
    
    private func statPill(label: String, value: String) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(label)
                .font(.system(size: 9, weight: .bold))
                .foregroundStyle(Self.outlineColor)
                .tracking(0.8)
            Text(value)
                .font(.system(size: 13, weight: .semibold))
                .foregroundStyle(Self.primaryTextColor)
        }
    }
    
    // MARK: - Phase Header
    
    private func phaseHeader(
        phaseName: String,
        phaseType: String,
        weeks: [BlockWeek],
        isExpanded: Bool,
        isActive: Bool,
        isPast: Bool
    ) -> some View {
        Button {
            withAnimation(.spring(response: 0.3, dampingFraction: 0.8)) {
                if isExpanded {
                    expandedPhases.remove(phaseName)
                } else {
                    expandedPhases.insert(phaseName)
                }
            }
        } label: {
            HStack(spacing: 12) {
                // Timeline dot
                Circle()
                    .fill(isActive ? Self.accentColor : (isPast ? Self.outlineColor.opacity(0.6) : Self.outlineColor.opacity(0.3)))
                    .frame(width: 10, height: 10)
                    .overlay(
                        Circle()
                            .stroke(isActive ? Self.accentColor.opacity(0.4) : Color.clear, lineWidth: 3)
                    )
                
                // Phase name
                Text(phaseName.uppercased())
                    .font(.system(size: 12, weight: .bold))
                    .foregroundStyle(isActive ? Self.primaryTextColor : Self.outlineColor)
                    .tracking(1.0)
                
                Spacer()
                
                // Badge
                if isActive {
                    Text("ACTIVE")
                        .font(.system(size: 9, weight: .bold))
                        .foregroundStyle(.white)
                        .padding(.horizontal, 8)
                        .padding(.vertical, 3)
                        .background(Self.accentColor.opacity(0.8))
                        .clipShape(Capsule())
                } else {
                    let first = weeks.first?.weekNumber ?? 0
                    let last = weeks.last?.weekNumber ?? 0
                    Text(first == last ? "WEEK \(first)" : "WEEKS \(first)-\(last)")
                        .font(.system(size: 9, weight: .bold))
                        .foregroundStyle(Self.outlineColor)
                        .padding(.horizontal, 8)
                        .padding(.vertical, 3)
                        .background(Color.white.opacity(0.04))
                        .clipShape(Capsule())
                }
                
                // Chevron
                Image(systemName: isExpanded ? "chevron.up" : "chevron.down")
                    .font(.system(size: 10, weight: .bold))
                    .foregroundStyle(Self.outlineColor.opacity(0.6))
            }
            .padding(.horizontal, 20)
            .padding(.vertical, 14)
        }
        .buttonStyle(.plain)
    }
    
    // MARK: - Week Card Row (with timeline connector)
    
    private func weekCardRow(week: BlockWeek, currentWeekNum: Int, showConnector: Bool) -> some View {
        let isPast = week.weekNumber < currentWeekNum
        let isCurrent = week.isCurrentWeek
        
        return HStack(alignment: .top, spacing: 12) {
            // Timeline connector
            VStack(spacing: 0) {
                // Top connector line
                Rectangle()
                    .fill(Color.white.opacity(0.08))
                    .frame(width: 1.5)
                    .frame(height: 8)
                
                // Node dot
                Circle()
                    .fill(isCurrent ? Self.accentColor : (isPast ? Self.outlineColor.opacity(0.4) : Color.white.opacity(0.15)))
                    .frame(width: 6, height: 6)
                
                // Bottom connector line
                if showConnector {
                    Rectangle()
                        .fill(Color.white.opacity(0.08))
                        .frame(width: 1.5)
                        .frame(maxHeight: .infinity)
                }
            }
            .frame(width: 12)
            .padding(.leading, 19)
            
            // Week card
            Button {
                selectedWeekDetail = week
            } label: {
                if isCurrent {
                    currentWeekCard(week)
                } else if isPast {
                    pastWeekCard(week)
                } else {
                    futureWeekCard(week)
                }
            }
            .buttonStyle(.plain)
            .padding(.trailing, 20)
            .padding(.bottom, 10)
        }
    }
    
    // MARK: - Past Week Card
    
    private func pastWeekCard(_ week: BlockWeek) -> some View {
        HStack(spacing: 12) {
            VStack(alignment: .leading, spacing: 4) {
                HStack(spacing: 6) {
                    Text("WEEK \(week.weekNumber)")
                        .font(.system(size: 12, weight: .bold))
                        .foregroundStyle(Self.outlineColor)
                    
                    Image(systemName: "checkmark.circle.fill")
                        .font(.system(size: 11))
                        .foregroundStyle(Self.outlineColor.opacity(0.6))
                    
                    if week.isRecoveryWeek {
                        Text("RECOVERY")
                            .font(.system(size: 8, weight: .bold))
                            .foregroundStyle(Self.outlineColor)
                            .padding(.horizontal, 5)
                            .padding(.vertical, 2)
                            .background(Color.white.opacity(0.06))
                            .clipShape(Capsule())
                    }
                }
                
                if let summary = week.planSummary {
                    Text(summary)
                        .font(.system(size: 11, weight: .medium))
                        .foregroundStyle(Self.outlineColor.opacity(0.7))
                        .lineLimit(1)
                }
                
                HStack(spacing: 8) {
                    Text("\(week.expectedTotalHours ?? "0")h")
                        .font(.system(size: 10, weight: .semibold))
                        .foregroundStyle(Self.outlineColor.opacity(0.5))
                    
                    if let tl = week.actualTrainingLoad {
                        Text("•")
                            .font(.system(size: 10))
                            .foregroundStyle(Self.outlineColor.opacity(0.3))
                        Text("TL: \(Int(tl))")
                            .font(.system(size: 10, weight: .semibold))
                            .foregroundStyle(Self.outlineColor.opacity(0.5))
                    }
                }
            }
            
            Spacer()
            
            Image(systemName: "chevron.right")
                .font(.system(size: 10))
                .foregroundStyle(Self.outlineColor.opacity(0.3))
        }
        .padding(12)
        .background(Self.surfaceColor.opacity(0.5))
        .clipShape(RoundedRectangle(cornerRadius: 8))
        .overlay(
            RoundedRectangle(cornerRadius: 8)
                .stroke(Color.white.opacity(0.04), lineWidth: 1)
        )
        .opacity(0.55)
    }
    
    // MARK: - Current Week Card (Hero)
    
    private func currentWeekCard(_ week: BlockWeek) -> some View {
        VStack(alignment: .leading, spacing: 14) {
            // Title row
            HStack(spacing: 8) {
                Text("WEEK \(week.weekNumber)")
                    .font(.system(size: 14, weight: .bold))
                    .foregroundStyle(Self.primaryTextColor)
                
                // Pulsing dot + CURRENT label
                HStack(spacing: 4) {
                    Circle()
                        .fill(Self.accentColor)
                        .frame(width: 6, height: 6)
                    
                    Text("CURRENT")
                        .font(.system(size: 9, weight: .bold))
                        .foregroundStyle(Self.accentColor)
                }
                .padding(.horizontal, 8)
                .padding(.vertical, 3)
                .background(Self.accentColor.opacity(0.12))
                .clipShape(Capsule())
                
                Spacer()
                
                if week.isRecoveryWeek {
                    Text("RECOVERY")
                        .font(.system(size: 8, weight: .bold))
                        .foregroundStyle(.teal)
                        .padding(.horizontal, 6)
                        .padding(.vertical, 2)
                        .background(Color.teal.opacity(0.15))
                        .clipShape(Capsule())
                }
            }
            
            // Focus text
            if let summary = week.planSummary {
                Text(summary)
                    .font(.system(size: 13, weight: .medium))
                    .foregroundStyle(Self.onSurfaceVariantColor)
                    .lineLimit(2)
            }
            
            // Targets row
            HStack(spacing: 0) {
                // Target Duration
                VStack(alignment: .leading, spacing: 2) {
                    Text("TARGET DURATION")
                        .font(.system(size: 9, weight: .bold))
                        .foregroundStyle(Self.outlineColor)
                        .tracking(0.5)
                    Text("\(week.expectedTotalHours ?? "0")h")
                        .font(.system(size: 20, weight: .bold))
                        .foregroundStyle(Self.primaryTextColor)
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                
                // Divider
                Rectangle()
                    .fill(Color.white.opacity(0.08))
                    .frame(width: 1, height: 36)
                
                // Training Load
                VStack(alignment: .leading, spacing: 2) {
                    Text("TRAINING LOAD")
                        .font(.system(size: 9, weight: .bold))
                        .foregroundStyle(Self.outlineColor)
                        .tracking(0.5)
                    if let tl = week.actualTrainingLoad {
                        Text("\(Int(tl))")
                            .font(.system(size: 20, weight: .bold))
                            .foregroundStyle(Self.primaryTextColor)
                    } else {
                        Text("—")
                            .font(.system(size: 20, weight: .bold))
                            .foregroundStyle(Self.outlineColor.opacity(0.4))
                    }
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(.leading, 16)
            }
            
            // Micro-visualization: 7-bar chart
            if let workouts = week.workouts, !workouts.isEmpty {
                microVisualization(workouts: workouts)
            }
            
            // Tap hint
            HStack {
                Spacer()
                HStack(spacing: 4) {
                    Text("View schedule")
                        .font(.system(size: 10, weight: .medium))
                        .foregroundStyle(Self.outlineColor.opacity(0.5))
                    Image(systemName: "chevron.right")
                        .font(.system(size: 9))
                        .foregroundStyle(Self.outlineColor.opacity(0.4))
                }
            }
        }
        .padding(16)
        .background(
            ZStack {
                Self.surfaceColor
                
                // Subtle radial gradient overlay
                RadialGradient(
                    gradient: Gradient(colors: [
                        Self.accentColor.opacity(0.06),
                        Color.clear
                    ]),
                    center: .topLeading,
                    startRadius: 0,
                    endRadius: 200
                )
            }
        )
        .clipShape(RoundedRectangle(cornerRadius: 8))
        .overlay(
            RoundedRectangle(cornerRadius: 8)
                .stroke(Color.white.opacity(0.1), lineWidth: 1)
        )
    }
    
    // MARK: - Micro-Visualization (7-bar chart)
    
    private func microVisualization(workouts: [CalendarWorkout]) -> some View {
        let dayNames = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
        let dayLabels = ["M", "T", "W", "T", "F", "S", "S"]
        
        // Sum workout durations per day
        var dayMinutes: [Double] = Array(repeating: 0, count: 7)
        for w in workouts {
            if let idx = dayNames.firstIndex(of: w.day) {
                dayMinutes[idx] += parseMinutes(from: w.totalTime ?? "0")
            }
        }
        
        let maxMinutes = dayMinutes.max() ?? 1
        let normalizedMax = max(maxMinutes, 1) // avoid division by zero
        
        return VStack(spacing: 4) {
            HStack(alignment: .bottom, spacing: 6) {
                ForEach(0..<7, id: \.self) { i in
                    let height = dayMinutes[i] > 0 ? max(dayMinutes[i] / normalizedMax * 32, 4) : 4
                    let isToday = i == currentDayIndex
                    let barColor: Color = isToday ? Self.accentColor : Color.white.opacity(dayMinutes[i] > 0 ? 0.25 : 0.08)
                    
                    RoundedRectangle(cornerRadius: 2)
                        .fill(barColor)
                        .frame(height: CGFloat(height))
                        .frame(maxWidth: .infinity)
                }
            }
            .frame(height: 36)
            
            HStack(spacing: 6) {
                ForEach(0..<7, id: \.self) { i in
                    let isToday = i == currentDayIndex
                    Text(dayLabels[i])
                        .font(.system(size: 8, weight: isToday ? .bold : .medium))
                        .foregroundStyle(isToday ? Self.accentColor : Self.outlineColor.opacity(0.4))
                        .frame(maxWidth: .infinity)
                }
            }
        }
        .padding(.top, 4)
    }
    
    // MARK: - Future Week Card
    
    private func futureWeekCard(_ week: BlockWeek) -> some View {
        HStack(spacing: 12) {
            VStack(alignment: .leading, spacing: 4) {
                HStack(spacing: 6) {
                    Text("WEEK \(week.weekNumber)")
                        .font(.system(size: 12, weight: .bold))
                        .foregroundStyle(Self.primaryTextColor.opacity(0.7))
                    
                    if week.isRecoveryWeek {
                        Text("RECOVERY")
                            .font(.system(size: 8, weight: .bold))
                            .foregroundStyle(.teal.opacity(0.8))
                            .padding(.horizontal, 5)
                            .padding(.vertical, 2)
                            .background(Color.teal.opacity(0.1))
                            .clipShape(Capsule())
                    }
                }
                
                if let summary = week.planSummary {
                    Text(summary)
                        .font(.system(size: 11, weight: .medium))
                        .foregroundStyle(Self.onSurfaceVariantColor.opacity(0.6))
                        .lineLimit(1)
                }
                
                HStack(spacing: 8) {
                    HStack(spacing: 3) {
                        Image(systemName: "clock")
                            .font(.system(size: 9))
                        Text("\(week.expectedTotalHours ?? "0")h")
                            .font(.system(size: 10, weight: .semibold))
                    }
                    .foregroundStyle(Self.outlineColor.opacity(0.5))
                    
                    HStack(spacing: 3) {
                        Image(systemName: "figure.run")
                            .font(.system(size: 9))
                        Text("\(week.expectedRunKm ?? "0") km")
                            .font(.system(size: 10, weight: .semibold))
                    }
                    .foregroundStyle(Self.outlineColor.opacity(0.5))
                }
            }
            
            Spacer()
            
            Image(systemName: "chevron.right")
                .font(.system(size: 10))
                .foregroundStyle(Self.outlineColor.opacity(0.3))
        }
        .padding(12)
        .background(Self.surfaceColor.opacity(0.4))
        .clipShape(RoundedRectangle(cornerRadius: 8))
        .overlay(
            RoundedRectangle(cornerRadius: 8)
                .stroke(Color.white.opacity(0.04), lineWidth: 1)
        )
    }
    
    // MARK: - Future Phase Placeholder
    
    private func futurePhasePlaceholder(currentWeekNumber: Int) -> some View {
        HStack(spacing: 10) {
            Image(systemName: "sparkles")
                .font(.system(size: 14))
                .foregroundStyle(Self.outlineColor.opacity(0.5))
            
            Text("Future block details will populate upon completion of Week \(currentWeekNumber) assessment.")
                .font(.system(size: 11, weight: .regular).italic())
                .foregroundStyle(Self.outlineColor.opacity(0.5))
                .lineLimit(3)
        }
        .padding(14)
        .background(Self.surfaceColor.opacity(0.3))
        .clipShape(RoundedRectangle(cornerRadius: 8))
        .overlay(
            RoundedRectangle(cornerRadius: 8)
                .stroke(Color.white.opacity(0.04), lineWidth: 1)
        )
    }
    
    // MARK: - Error View
    
    private func errorView(_ error: String) -> some View {
        VStack(spacing: 16) {
            Image(systemName: "exclamationmark.triangle.fill")
                .font(.largeTitle)
                .foregroundStyle(Self.accentColor.opacity(0.8))
            Text(error)
                .font(.system(size: 13, weight: .medium))
                .foregroundStyle(Self.onSurfaceVariantColor)
                .multilineTextAlignment(.center)
                .padding(.horizontal)
            Button("Retry") {
                Task {
                    await loadData()
                }
            }
            .font(.system(size: 13, weight: .semibold))
            .foregroundStyle(.white)
            .padding(.horizontal, 20)
            .padding(.vertical, 8)
            .background(Self.accentColor.opacity(0.8))
            .clipShape(Capsule())
        }
    }
    
    // MARK: - Helpers
    
    private func loadData() async {
        isLoading = true
        errorMessage = nil
        do {
            let data = try await network.fetchBlockCalendar()
            await MainActor.run {
                self.calendarData = data
                self.isLoading = false
                
                // Auto-expand the phase containing the current active week and the previous phase
                if let currentWeek = data.weeks.first(where: { $0.isCurrentWeek }) {
                    self.expandedPhases.insert(currentWeek.phaseName)
                }
                // Expand all past phases that have plans
                for (phaseName, weeks) in groupedWeeks {
                    if weeks.contains(where: { $0.hasPlan }) {
                        self.expandedPhases.insert(phaseName)
                    }
                }
            }
        } catch {
            await MainActor.run {
                self.errorMessage = "Failed to load timeline: \(error.localizedDescription)"
                self.isLoading = false
            }
        }
    }
    
    private func regenerateWeeklyPlan() async {
        isRegenerating = true
        errorMessage = nil
        do {
            _ = try await network.regenerateWeeklyPlan()
            await MainActor.run {
                self.isRegenerating = false
            }
            await loadData()
        } catch {
            await MainActor.run {
                self.errorMessage = "Regeneration failed: \(error.localizedDescription)"
                self.isRegenerating = false
            }
        }
    }
    
    private func formatRaceDate(_ dateStr: String) -> String {
        let inputFormatter = DateFormatter()
        inputFormatter.dateFormat = "yyyy-MM-dd"
        guard let date = inputFormatter.date(from: dateStr) else { return dateStr }
        
        let outputFormatter = DateFormatter()
        outputFormatter.dateFormat = "MMMM d, yyyy"
        return outputFormatter.string(from: date)
    }
    
    private func parseMinutes(from timeString: String) -> Double {
        let cleaned = timeString.lowercased().trimmingCharacters(in: .whitespaces)
        
        // Handle "Xh Ym" format
        if cleaned.contains("h") {
            var total: Double = 0
            let parts = cleaned.components(separatedBy: " ")
            for part in parts {
                if part.hasSuffix("h") {
                    total += (Double(part.dropLast()) ?? 0) * 60
                } else if part.hasSuffix("m") || part.hasSuffix("min") {
                    let numStr = part.replacingOccurrences(of: "min", with: "").replacingOccurrences(of: "m", with: "")
                    total += Double(numStr) ?? 0
                }
            }
            return total
        }
        
        // Handle "X min" or "X minutes"
        if cleaned.contains("min") {
            let numStr = cleaned.replacingOccurrences(of: "minutes", with: "")
                .replacingOccurrences(of: "min", with: "")
                .trimmingCharacters(in: .whitespaces)
            return Double(numStr) ?? 0
        }
        
        // Handle "HH:MM:SS" or "MM:SS"
        if cleaned.contains(":") {
            let parts = cleaned.components(separatedBy: ":")
            if parts.count == 3 {
                let hours = Double(parts[0]) ?? 0
                let mins = Double(parts[1]) ?? 0
                return hours * 60 + mins
            } else if parts.count == 2 {
                return Double(parts[0]) ?? 0
            }
        }
        
        // Fallback: try as a plain number (minutes)
        return Double(cleaned) ?? 0
    }
}

// MARK: - Detail Sheet Component

struct WeeklyWorkoutsDetailSheet: View {
    @Environment(\.dismiss) private var dismiss
    let week: BlockWeek
    
    // Design system
    private static let backgroundColor = Color(red: 0.075, green: 0.075, blue: 0.082)
    private static let surfaceColor = Color(red: 0.122, green: 0.122, blue: 0.129)
    private static let primaryTextColor = Color(red: 0.784, green: 0.776, blue: 0.780)
    private static let accentColor = Color(red: 1.0, green: 0.71, blue: 0.604)
    private static let outlineColor = Color(red: 0.569, green: 0.565, blue: 0.580)
    private static let onSurfaceVariantColor = Color(red: 0.780, green: 0.776, blue: 0.792)
    
    // Sort workouts by day of week to ensure correct visual order
    private var sortedWorkouts: [CalendarWorkout] {
        guard let list = week.workouts else { return [] }
        let order = ["Monday": 0, "Tuesday": 1, "Wednesday": 2, "Thursday": 3, "Friday": 4, "Saturday": 5, "Sunday": 6]
        return list.sorted { w1, w2 in
            (order[w1.day] ?? 99) < (order[w2.day] ?? 99)
        }
    }
    
    var body: some View {
        NavigationStack {
            ZStack {
                Self.backgroundColor.ignoresSafeArea()
                
                ScrollView {
                    VStack(alignment: .leading, spacing: 20) {
                        // Week Overview Card
                        VStack(alignment: .leading, spacing: 12) {
                            Text("WEEK OVERVIEW")
                                .font(.system(size: 10, weight: .bold))
                                .foregroundStyle(Self.accentColor)
                                .tracking(1.0)
                            
                            Text(week.phaseName)
                                .font(.system(size: 18, weight: .bold))
                                .foregroundStyle(Self.primaryTextColor)
                            
                            Text("Week \(week.weekNumber) • \(week.isRecoveryWeek ? "Recovery Week" : "Build Week \(week.cycleWeek)/3")")
                                .font(.system(size: 13, weight: .medium))
                                .foregroundStyle(Self.outlineColor)
                            
                            Rectangle()
                                .fill(Color.white.opacity(0.06))
                                .frame(height: 1)
                            
                            HStack(spacing: 20) {
                                statBadge(icon: "clock.fill", title: "Expected Hours", value: "\(week.expectedTotalHours ?? "0") hrs", color: Self.accentColor)
                                statBadge(icon: "figure.run", title: "Target Run", value: "\(week.expectedRunKm ?? "0") km", color: .green)
                            }
                            
                            if let tl = week.actualTrainingLoad {
                                HStack(spacing: 20) {
                                    statBadge(icon: "bolt.fill", title: "Training Load", value: "\(Int(tl))", color: Self.accentColor)
                                }
                            }
                        }
                        .padding(16)
                        .background(Self.surfaceColor)
                        .clipShape(RoundedRectangle(cornerRadius: 8))
                        .overlay(
                            RoundedRectangle(cornerRadius: 8)
                                .stroke(Color.white.opacity(0.08), lineWidth: 1)
                        )
                        
                        // Detailed workouts list
                        if week.hasPlan, !sortedWorkouts.isEmpty {
                            Text("DAILY SCHEDULE")
                                .font(.system(size: 10, weight: .bold))
                                .foregroundStyle(Self.outlineColor)
                                .tracking(1.0)
                                .padding(.horizontal, 4)
                            
                            ForEach(sortedWorkouts) { workout in
                                workoutRow(workout)
                            }
                        } else {
                            // Projected future week guidelines
                            VStack(spacing: 20) {
                                Image(systemName: "sparkles")
                                    .font(.system(size: 36))
                                    .foregroundStyle(Self.accentColor.opacity(0.6))
                                    .symbolEffect(.bounce, options: .repeating)
                                
                                Text("Projected Phase Prescriptions")
                                    .font(.system(size: 16, weight: .bold))
                                    .foregroundStyle(Self.primaryTextColor)
                                
                                Text("This is a projected future week in your periodization timeline. The AI coach will compile your day-by-day customized schedule when this week becomes active.\n\nExpected guidelines for this phase:")
                                    .font(.system(size: 13, weight: .medium))
                                    .foregroundStyle(Self.outlineColor)
                                    .multilineTextAlignment(.center)
                                    .padding(.horizontal)
                                
                                // Recommended weekly frequencies
                                VStack(alignment: .leading, spacing: 10) {
                                    guidelineRow(sport: "running", text: "3x Running (Easy base, strides)")
                                    guidelineRow(sport: "cycling", text: "2x Cycling (Z2 Endurance)")
                                    guidelineRow(sport: "swimming", text: "2x Swimming (Technique drills)")
                                    guidelineRow(sport: "strength", text: "3x Strength (Hypertrophy focus)")
                                }
                                .padding(14)
                                .background(Self.accentColor.opacity(0.05))
                                .clipShape(RoundedRectangle(cornerRadius: 8))
                            }
                            .padding(20)
                            .background(Self.surfaceColor)
                            .clipShape(RoundedRectangle(cornerRadius: 8))
                            .overlay(
                                RoundedRectangle(cornerRadius: 8)
                                    .stroke(Color.white.opacity(0.06), lineWidth: 1)
                            )
                        }
                    }
                    .padding()
                }
            }
            .navigationTitle("Week \(week.weekNumber) Schedule")
            .navigationBarTitleDisplayMode(.inline)
            .toolbarBackground(Self.backgroundColor, for: .navigationBar)
            .toolbarColorScheme(.dark, for: .navigationBar)
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button("Done") {
                        dismiss()
                    }
                    .font(.system(size: 14, weight: .semibold))
                    .foregroundStyle(Self.accentColor)
                }
            }
        }
    }
    
    // MARK: - Subcomponents
    
    private func statBadge(icon: String, title: String, value: String, color: Color) -> some View {
        HStack(spacing: 10) {
            Image(systemName: icon)
                .font(.system(size: 14))
                .foregroundStyle(color)
                .frame(width: 28, height: 28)
                .background(color.opacity(0.12))
                .clipShape(Circle())
            
            VStack(alignment: .leading, spacing: 2) {
                Text(title.uppercased())
                    .font(.system(size: 8, weight: .bold))
                    .foregroundStyle(Self.outlineColor)
                    .tracking(0.5)
                Text(value)
                    .font(.system(size: 13, weight: .bold))
                    .foregroundStyle(Self.primaryTextColor)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }
    
    private func workoutRow(_ w: CalendarWorkout) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                Text(w.day.uppercased())
                    .font(.system(size: 9, weight: .bold))
                    .foregroundStyle(Self.accentColor)
                    .tracking(0.8)
                Spacer()
                Text(sportEmoji(for: w.sport))
                    .font(.system(size: 16))
            }
            
            VStack(alignment: .leading, spacing: 4) {
                Text(w.title)
                    .font(.system(size: 14, weight: .bold))
                    .foregroundStyle(Self.primaryTextColor)
                
                HStack(spacing: 12) {
                    HStack(spacing: 3) {
                        Text("⏱️")
                            .font(.system(size: 10))
                        Text(w.totalTime ?? "--")
                            .font(.system(size: 11, weight: .medium))
                    }
                    HStack(spacing: 3) {
                        Text("❤️")
                            .font(.system(size: 10))
                        Text("Zone \(w.hrTarget ?? "--")")
                            .font(.system(size: 11, weight: .medium))
                    }
                }
                .foregroundStyle(Self.outlineColor)
            }
            
            // Muscle groups capsules if strength
            if w.sport.lowercased() == "strength", let groups = w.muscleGroups, !groups.isEmpty {
                ScrollView(.horizontal, showsIndicators: false) {
                    HStack(spacing: 5) {
                        ForEach(groups, id: \.self) { group in
                            Text(group.uppercased())
                                .font(.system(size: 8, weight: .bold))
                                .foregroundStyle(.white)
                                .padding(.horizontal, 7)
                                .padding(.vertical, 3)
                                .background(colorForMuscleGroup(group))
                                .clipShape(Capsule())
                        }
                    }
                }
            }
        }
        .padding(12)
        .background(Self.surfaceColor.opacity(0.6))
        .clipShape(RoundedRectangle(cornerRadius: 8))
        .overlay(
            RoundedRectangle(cornerRadius: 8)
                .stroke(Color.white.opacity(0.04), lineWidth: 1)
        )
    }
    
    private func guidelineRow(sport: String, text: String) -> some View {
        HStack(spacing: 10) {
            Text(sportEmoji(for: sport))
                .font(.body)
            Text(text)
                .font(.system(size: 12, weight: .medium))
                .foregroundStyle(Self.onSurfaceVariantColor)
            Spacer()
        }
    }
    
    private func sportEmoji(for sport: String) -> String {
        switch sport.lowercased() {
        case "running": return "🏃"
        case "cycling": return "🚴"
        case "swimming": return "🏊"
        case "strength": return "🏋️"
        case "rest": return "😴"
        default: return "😴"
        }
    }
    
    private func colorForMuscleGroup(_ group: String) -> Color {
        switch group.lowercased() {
        case "chest": return .pink
        case "shoulders": return .orange
        case "back": return .teal
        case "legs": return .green
        case "arms": return .purple
        default: return .secondary
        }
    }
}
