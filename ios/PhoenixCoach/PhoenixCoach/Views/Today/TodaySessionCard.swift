import SwiftUI

/// Today's session — the workout card on the Today tab.
///
/// Built from the "Today Activities Card" design canvas (Option A, 2026-09-10).
/// One card per day: title + sport/zone/HR line + a thin hero numeral for the
/// minutes, a zone-coloured segment bar of the steps, the steps as rows with
/// a zone label instead of a timeline dot, and the fuel line in an inner
/// panel. An adapted day adds a chip in the header, the reason in plain
/// words with the telemetry comparison as its footer row, and the original
/// plan as a one-line disclosure at the bottom — the previous card showed
/// the original as a second full card you scrolled sideways to find.
struct TodaySessionCard: View {
    let plan: DayPlan
    var onCompare: (() -> Void)? = nil

    @State private var showOriginal = false
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    private var workouts: [Workout] { plan.workouts ?? [] }
    private var isAdapted: Bool {
        guard let a = plan.adaptation else { return false }
        return !a.isEmpty
    }
    private var originals: [Workout] { plan.originalWorkouts ?? [] }

    var body: some View {
        VStack(alignment: .leading, spacing: DS.Spacing.m) {
            HStack(alignment: .center, spacing: DS.Spacing.s) {
                Text("Today's session")
                    .font(.system(size: 11, weight: .bold))
                    .textCase(.uppercase)
                    .tracking(DS.Tracking.wide)
                    .foregroundStyle(DS.Colors.outline)
                Spacer()
                if isAdapted {
                    Text("Adapted")
                        .font(.system(size: 10, weight: .bold))
                        .textCase(.uppercase)
                        .tracking(DS.Tracking.normal)
                        .foregroundStyle(DS.Colors.onAccent)
                        .padding(.horizontal, DS.Spacing.s)
                        .padding(.vertical, DS.Spacing.xs)
                        .background(DS.Colors.accent)
                        .clipShape(Capsule())
                }
            }
            .padding(.horizontal, DS.Spacing.xs)

            VStack(alignment: .leading, spacing: DS.Spacing.l) {
                ForEach(Array(workouts.enumerated()), id: \.offset) { index, workout in
                    if index > 0 { hairline }
                    SessionWorkoutBlock(workout: workout)
                    if index == 0 && isAdapted, let reason = plan.adaptation {
                        adaptationPanel(reason)
                    }
                }

                if isAdapted && !originals.isEmpty {
                    hairline
                    originalDisclosure
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .glassCard()
        }
    }

    // MARK: - Adapted day

    private func adaptationPanel(_ reason: String) -> some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack(alignment: .top, spacing: DS.Spacing.s) {
                Image(systemName: "sparkles")
                    .font(.system(size: 13))
                    .symbolRenderingMode(.hierarchical)
                    .foregroundStyle(.white)
                    .padding(.top, 2)
                Text(reason)
                    .font(.system(size: 13))
                    .foregroundStyle(DS.Colors.onSurface)
                    .fixedSize(horizontal: false, vertical: true)
            }
            .padding(DS.Spacing.m)

            if let onCompare {
                Rectangle()
                    .fill(Color.white.opacity(0.06))
                    .frame(height: 1)
                Button(action: onCompare) {
                    HStack(spacing: DS.Spacing.xs + 2) {
                        Image(systemName: "waveform.path.ecg")
                            .font(.system(size: 12, weight: .semibold))
                        Text("Compare telemetry")
                            .font(.system(size: 13, weight: .semibold))
                        Spacer()
                        Image(systemName: "chevron.right")
                            .font(.system(size: 12, weight: .bold))
                            .foregroundStyle(DS.Colors.outline)
                    }
                    .foregroundStyle(.white)
                    .padding(.horizontal, DS.Spacing.m)
                    .frame(height: 44)
                    .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
                .accessibilityLabel("Compare telemetry")
            }
        }
        .background(Color.white.opacity(0.03))
        .clipShape(RoundedRectangle(cornerRadius: DS.Radius.medium))
    }

    private var originalDisclosure: some View {
        VStack(alignment: .leading, spacing: DS.Spacing.m) {
            Button {
                withAnimation(reduceMotion ? nil : DS.Animation.quick) {
                    showOriginal.toggle()
                }
            } label: {
                HStack(alignment: .center, spacing: DS.Spacing.s) {
                    VStack(alignment: .leading, spacing: 2) {
                        Text("Original plan")
                            .font(.system(size: 11, weight: .bold))
                            .textCase(.uppercase)
                            .tracking(DS.Tracking.normal)
                            .foregroundStyle(DS.Colors.outline)
                        ForEach(Array(originals.enumerated()), id: \.offset) { _, w in
                            originalLine(w)
                        }
                    }
                    Spacer()
                    Image(systemName: showOriginal ? "chevron.up" : "chevron.down")
                        .font(.system(size: 12, weight: .bold))
                        .foregroundStyle(DS.Colors.outline)
                }
                .frame(minHeight: 44)
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .accessibilityLabel("Original plan")
            .accessibilityValue(showOriginal ? "Expanded" : "Collapsed")
            .accessibilityAddTraits(.isButton)

            if showOriginal {
                VStack(alignment: .leading, spacing: DS.Spacing.m) {
                    ForEach(Array(originals.enumerated()), id: \.offset) { index, w in
                        if index > 0 { hairline }
                        SessionStepList(steps: w.steps)
                    }
                }
                .padding(DS.Spacing.m)
                .background(Color.white.opacity(0.03))
                .clipShape(RoundedRectangle(cornerRadius: DS.Radius.medium))
                .transition(.opacity)
            }
        }
    }

    /// "Tempo Run · Zone 4 · 50 min" with the zone word in its colour.
    private func originalLine(_ w: Workout) -> some View {
        let zone = SessionFormat.zone(of: w)
        let minutes = SessionFormat.minutes(w.totalTime)
        var line = Text(w.title)
        if let zone {
            line = line + Text(" · ") + Text("Zone \(zone)").foregroundColor(DS.Colors.zone(zone))
        }
        if let minutes {
            line = line + Text(" · \(minutes) min")
        }
        return line
            .font(.system(size: 13))
            .foregroundStyle(DS.Colors.onSurface)
    }

    private var hairline: some View {
        Rectangle()
            .fill(Color.white.opacity(0.06))
            .frame(height: 1)
    }
}

// MARK: - One workout inside the card

private struct SessionWorkoutBlock: View {
    let workout: Workout

    private var minutes: Int? { SessionFormat.minutes(workout.totalTime) }
    private var zone: Int? { SessionFormat.zone(of: workout) }

    var body: some View {
        VStack(alignment: .leading, spacing: DS.Spacing.l) {
            HStack(alignment: .top, spacing: DS.Spacing.m) {
                VStack(alignment: .leading, spacing: DS.Spacing.xs + 2) {
                    HStack(spacing: DS.Spacing.s) {
                        Image(systemName: workout.sportIcon)
                            .font(.system(size: 15, weight: .semibold))
                            .symbolRenderingMode(.hierarchical)
                            .foregroundStyle(.white)
                        Text(workout.title)
                            .font(.system(size: 15, weight: .semibold))
                            .foregroundStyle(.white)
                            .lineLimit(2)
                    }
                    Text(subtitle)
                        .font(.system(size: 11, weight: .bold))
                        .textCase(.uppercase)
                        .tracking(DS.Tracking.normal)
                        .foregroundStyle(DS.Colors.outline)
                }
                Spacer(minLength: DS.Spacing.s)
                if let minutes {
                    HStack(alignment: .firstTextBaseline, spacing: 2) {
                        Text("\(minutes)")
                            .font(.system(size: 36, weight: .ultraLight))
                            .monospacedDigit()
                            .foregroundStyle(.white)
                        Text("min")
                            .font(.system(size: 13))
                            .foregroundStyle(DS.Colors.outline)
                    }
                    .fixedSize()
                }
            }
            .accessibilityElement(children: .combine)

            if !workout.steps.isEmpty {
                SessionSegmentBar(steps: workout.steps)
                SessionStepList(steps: workout.steps)
            }

            if let fuel = workout.fuel, !fuel.isEmpty {
                HStack(alignment: .center, spacing: DS.Spacing.s) {
                    Image(systemName: "fork.knife")
                        .font(.system(size: 13))
                        .symbolRenderingMode(.hierarchical)
                        .foregroundStyle(DS.Colors.outline)
                    Text(fuel)
                        .font(.system(size: 13))
                        .foregroundStyle(DS.Colors.onSurface)
                        .fixedSize(horizontal: false, vertical: true)
                }
                .padding(DS.Spacing.m)
                .frame(maxWidth: .infinity, alignment: .leading)
                .background(Color.white.opacity(0.03))
                .clipShape(RoundedRectangle(cornerRadius: DS.Radius.medium))
                .accessibilityElement(children: .combine)
            }
        }
    }

    /// "Run · Zone 2 · 140–150 bpm" — each part only when known.
    private var subtitle: String {
        var parts = [SessionFormat.sportName(workout.sport)]
        if let zone { parts.append("Zone \(zone)") }
        if let hr = SessionFormat.heartRate(workout.hrTarget) { parts.append(hr) }
        return parts.joined(separator: " · ")
    }
}

/// Step durations as a thin bar, each segment in its zone colour.
private struct SessionSegmentBar: View {
    let steps: [WorkoutStep]

    private let gap: CGFloat = 2

    var body: some View {
        let weights = steps.map { CGFloat(max(SessionFormat.minutes($0.duration) ?? 0, 0)) }
        let total = weights.reduce(0, +)
        let visible = weights.filter { $0 > 0 }.count
        if total > 0 {
            GeometryReader { geo in
                let usable = max(geo.size.width - gap * CGFloat(max(visible - 1, 0)), 0)
                HStack(spacing: gap) {
                    ForEach(Array(steps.enumerated()), id: \.offset) { index, step in
                        if weights[index] > 0 {
                            RoundedRectangle(cornerRadius: 3)
                                .fill(DS.Colors.zone(step.zone))
                                .frame(width: usable * weights[index] / total, height: 8)
                        }
                    }
                }
            }
            .frame(height: 8)
            .accessibilityHidden(true)
        }
    }
}

/// The steps as rows: zone label · name + description · duration.
private struct SessionStepList: View {
    let steps: [WorkoutStep]

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            ForEach(Array(steps.enumerated()), id: \.offset) { index, step in
                if index > 0 {
                    Rectangle()
                        .fill(Color.white.opacity(0.06))
                        .frame(height: 1)
                }
                HStack(alignment: .top, spacing: DS.Spacing.m) {
                    Text(step.zone.map { "Z\($0)" } ?? "")
                        .font(.system(size: 11, weight: .bold))
                        .monospacedDigit()
                        .tracking(DS.Tracking.normal)
                        .foregroundStyle(DS.Colors.zone(step.zone))
                        .frame(width: 28, alignment: .leading)
                    VStack(alignment: .leading, spacing: 2) {
                        Text(step.type)
                            .font(.system(size: 11, weight: .bold))
                            .textCase(.uppercase)
                            .tracking(DS.Tracking.normal)
                            .foregroundStyle(DS.Colors.outline)
                        if let desc = step.description, !desc.isEmpty {
                            Text(desc)
                                .font(.system(size: 13))
                                .foregroundStyle(DS.Colors.onSurface)
                                .fixedSize(horizontal: false, vertical: true)
                        }
                    }
                    Spacer(minLength: DS.Spacing.s)
                    Text(SessionFormat.stepDuration(step.duration))
                        .font(.system(size: 13, weight: .light))
                        .monospacedDigit()
                        .foregroundStyle(.white)
                }
                .padding(.vertical, DS.Spacing.s)
                .accessibilityElement(children: .combine)
            }
        }
    }
}

// MARK: - Formatting

/// Pure helpers over the plan strings. The backend writes durations as
/// "50 min", "45:00", "1:45:00" or a bare number of minutes, and hr_target
/// is sometimes a bare zone digit rather than a bpm range — that digit must
/// never render as "1 bpm".
enum SessionFormat {
    static func minutes(_ raw: String?) -> Int? {
        guard let raw else { return nil }
        let s = raw.lowercased().trimmingCharacters(in: .whitespaces)
        if s.isEmpty { return nil }
        if s.contains(":") {
            let parts = s.split(separator: ":").compactMap { Int($0) }
            guard !parts.isEmpty else { return nil }
            let total: Int
            switch parts.count {
            case 3: total = parts[0] * 60 + parts[1]          // h:mm:ss
            case 2: total = parts[0]                          // mm:ss
            default: total = parts[0]
            }
            return total > 0 ? total : nil
        }
        if s.contains("h") {
            var total = 0
            for part in s.split(separator: " ") {
                if part.hasSuffix("h") { total += (Int(part.dropLast()) ?? 0) * 60 }
                else if part.hasSuffix("min") { total += Int(part.dropLast(3)) ?? 0 }
                else if part.hasSuffix("m") { total += Int(part.dropLast()) ?? 0 }
            }
            return total > 0 ? total : nil
        }
        let digits = s.replacingOccurrences(of: "minutes", with: "")
            .replacingOccurrences(of: "min", with: "")
            .trimmingCharacters(in: .whitespaces)
        if let n = Int(digits) { return n > 0 ? n : nil }
        if let d = Double(digits) { return d > 0 ? Int(d.rounded()) : nil }
        return nil
    }

    /// "10:00" stays; a bare "30" becomes "30 min".
    static func stepDuration(_ raw: String) -> String {
        if raw.contains(":") { return raw }
        if let n = Int(raw.trimmingCharacters(in: .whitespaces)) { return "\(n) min" }
        return raw
    }

    /// The main step's zone, else the highest step zone, else hr_target when
    /// it is a bare zone digit.
    static func zone(of w: Workout) -> Int? {
        if let main = w.steps.first(where: { $0.type.lowercased() == "main" })?.zone { return main }
        if let top = w.steps.compactMap(\.zone).max() { return top }
        if let hr = w.hrTarget?.trimmingCharacters(in: .whitespaces),
           hr.count == 1, let z = Int(hr), (1...5).contains(z) { return z }
        return nil
    }

    /// hr_target only when it reads as heart rate, never a bare zone digit.
    static func heartRate(_ raw: String?) -> String? {
        guard let raw = raw?.trimmingCharacters(in: .whitespaces), !raw.isEmpty else { return nil }
        if raw.count <= 1 { return nil }
        let hasDigits = raw.rangeOfCharacter(from: .decimalDigits) != nil
        guard hasDigits else { return nil }
        return raw.lowercased().contains("bpm") ? raw : "\(raw) bpm"
    }

    static func sportName(_ sport: String) -> String {
        switch sport.lowercased() {
        case "run", "running", "trail_running": return "Run"
        case "bike", "ride", "cycling", "indoor_cycling": return "Ride"
        case "swim", "swimming", "open_water_swimming": return "Swim"
        case "strength", "gym", "training": return "Strength"
        case "rest", "recovery": return "Rest"
        default: return sport.capitalized
        }
    }
}
