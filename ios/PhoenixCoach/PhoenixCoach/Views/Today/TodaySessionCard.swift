import SwiftUI

/// Today's session — the instrument card on the Today tab.
///
/// Rounds 6–9 and 12–13 of the Today variants (references/today-v2.md):
/// title row · split body (minutes + target on the left, mono step table on
/// the right) · zone bar · the key set called out in an inner panel · one
/// line in the coach's voice that opens the chat. An adapted day puts a
/// banner *above* the card in the readiness tint; the card itself does not
/// change. Rows reveal their description on tap, no chevrons drawn.
struct TodaySessionCard: View {
    let plan: DayPlan
    /// Readiness status, for the adapted banner's tint.
    var readinessStatus: String? = nil
    var onCompare: (() -> Void)? = nil
    /// Opens the Coach tab with this prompt prefilled.
    var onAskCoach: ((String) -> Void)? = nil

    @State private var revealed: Set<Int> = []
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    private var workouts: [Workout] { plan.workouts ?? [] }
    private var isAdapted: Bool {
        guard let a = plan.adaptation else { return false }
        return !a.isEmpty
    }
    private var original: Workout? { plan.originalWorkouts?.first }
    private var bannerTint: Color {
        let c = DS.Colors.readiness(readinessStatus)
        return c == .white || c == DS.Colors.outline ? DS.Colors.warning : c
    }

    var body: some View {
        VStack(alignment: .leading, spacing: DS.Spacing.m) {
            if isAdapted, let reason = plan.adaptation {
                adaptedBanner(reason)
            }
            DS.SectionLabel(text: "Today's session")
                .padding(.horizontal, DS.Spacing.xs)
            VStack(alignment: .leading, spacing: DS.Spacing.l) {
                ForEach(Array(workouts.enumerated()), id: \.offset) { index, workout in
                    if index > 0 { hairline }
                    instrument(workout, index: index)
                }
                coachOpener
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .outlineCard()
        }
    }

    // MARK: - Adapted banner (round-13 V4)

    private func adaptedBanner(_ reason: String) -> some View {
        VStack(alignment: .leading, spacing: DS.Spacing.s) {
            Rectangle().fill(bannerTint.opacity(0.6)).frame(height: 1)
            HStack(alignment: .top, spacing: DS.Spacing.s) {
                DS.MonoLabel(text: "Adapted", color: bannerTint)
                Text(reason)
                    .font(.system(size: 11))
                    .foregroundStyle(DS.Colors.onSurface)
                    .fixedSize(horizontal: false, vertical: true)
            }
            if let onCompare, let o = original {
                Button(action: onCompare) {
                    HStack {
                        Spacer()
                        DS.MonoLabel(text: "Use the original · \(o.title)\(SessionFormat.minutes(o.totalTime).map { " \($0) min" } ?? "")",
                                     color: .white)
                            .lineLimit(1)
                    }
                    .frame(minHeight: 32)
                    .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
                .accessibilityLabel("Compare with the original plan")
            }
            Rectangle().fill(bannerTint.opacity(0.6)).frame(height: 1)
        }
        .padding(.horizontal, DS.Spacing.xs)
        .transition(.opacity)
        .accessibilityElement(children: .combine)
    }

    // MARK: - Instrument body (round-6 V5)

    private func instrument(_ w: Workout, index: Int) -> some View {
        VStack(alignment: .leading, spacing: DS.Spacing.l) {
            VStack(alignment: .leading, spacing: DS.Spacing.xs + 2) {
                HStack(spacing: DS.Spacing.s) {
                    Image(systemName: w.sportIcon)
                        .font(.system(size: 15, weight: .semibold))
                        .symbolRenderingMode(.hierarchical)
                        .foregroundStyle(.white)
                    Text(w.title)
                        .font(.system(size: 15, weight: .semibold))
                        .foregroundStyle(.white)
                        .lineLimit(2)
                }
                Text(subtitle(w))
                    .font(.system(size: 11, weight: .bold))
                    .textCase(.uppercase)
                    .tracking(DS.Tracking.normal)
                    .foregroundStyle(DS.Colors.outline)
            }
            .accessibilityElement(children: .combine)

            HStack(alignment: .top, spacing: DS.Spacing.l) {
                VStack(alignment: .leading, spacing: DS.Spacing.xs) {
                    if let m = SessionFormat.minutes(w.totalTime) {
                        DS.HeroNumeral(value: "\(m)", unit: "min", size: 36)
                    }
                    if let z = SessionFormat.zone(of: w) {
                        DS.MonoLabel(text: "target Z\(z)", color: DS.Colors.zone(z))
                    }
                    if let hr = SessionFormat.heartRate(w.hrTarget) {
                        DS.MonoLabel(text: hr)
                    }
                }
                .accessibilityElement(children: .combine)
                if !w.steps.isEmpty {
                    Rectangle().fill(Color.white.opacity(0.10)).frame(width: 1)
                    stepTable(w.steps, workoutIndex: index)
                }
            }
            .fixedSize(horizontal: false, vertical: true)

            if !w.steps.isEmpty {
                SessionSegmentBar(steps: w.steps)
            }
            if let key = keySet(of: w) {
                keySetPanel(key)
            }
            if let fuel = w.fuel, !fuel.isEmpty {
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
                .accessibilityElement(children: .combine)
            }
        }
    }

    private func stepTable(_ steps: [WorkoutStep], workoutIndex: Int) -> some View {
        VStack(alignment: .leading, spacing: 0) {
            ForEach(Array(steps.enumerated()), id: \.offset) { i, st in
                if i > 0 { hairline }
                let id = workoutIndex * 100 + i
                let open = revealed.contains(id)
                Button {
                    withAnimation(reduceMotion ? nil : DS.Animation.quick) {
                        if open { revealed.remove(id) } else { revealed.insert(id) }
                    }
                } label: {
                    VStack(alignment: .leading, spacing: DS.Spacing.xs) {
                        HStack(spacing: DS.Spacing.m) {
                            DS.MonoLabel(text: st.zone.map { "Z\($0)" } ?? "", color: DS.Colors.zone(st.zone))
                                .frame(width: 22, alignment: .leading)
                            DS.MonoLabel(text: st.type, color: .white)
                            Spacer(minLength: DS.Spacing.s)
                            Text(SessionFormat.tableDuration(st.duration))
                                .font(.system(size: 13, weight: .light, design: .monospaced))
                                .foregroundStyle(.white)
                        }
                        if open, let d = st.description, !d.isEmpty {
                            Text(d)
                                .font(.system(size: 11))
                                .foregroundStyle(DS.Colors.outline)
                                .fixedSize(horizontal: false, vertical: true)
                                .padding(.leading, 22 + DS.Spacing.m)
                                .transition(.opacity)
                        }
                    }
                    .padding(.vertical, DS.Spacing.s)
                    .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
                .accessibilityElement(children: .combine)
                .accessibilityValue(open ? "Expanded" : "Collapsed")
                .accessibilityAddTraits(.isButton)
            }
        }
    }

    // MARK: - Key set (round-8 V4)

    /// The longest step that isn't warm-up or cool-down.
    private func keySet(of w: Workout) -> WorkoutStep? {
        let candidates = w.steps.filter { st in
            let t = st.type.lowercased()
            return !t.contains("warm") && !t.contains("cool")
        }
        guard let step = candidates.max(by: {
            (SessionFormat.minutes($0.duration) ?? 0) < (SessionFormat.minutes($1.duration) ?? 0)
        }) else { return nil }
        guard let d = step.description, !d.isEmpty else { return nil }
        return step
    }

    private func keySetPanel(_ step: WorkoutStep) -> some View {
        VStack(alignment: .leading, spacing: DS.Spacing.xs) {
            HStack(spacing: DS.Spacing.s) {
                DS.SectionLabel(text: "Key set")
                DS.MonoLabel(text: [step.zone.map { "Z\($0)" }, SessionFormat.stepDuration(step.duration)]
                                .compactMap { $0 }.joined(separator: " · "),
                             color: DS.Colors.zone(step.zone))
            }
            Text(step.description ?? "")
                .font(.system(size: 13))
                .foregroundStyle(.white)
                .fixedSize(horizontal: false, vertical: true)
        }
        .padding(DS.Spacing.m)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Color.white.opacity(0.03))
        .clipShape(RoundedRectangle(cornerRadius: DS.Radius.medium))
        .accessibilityElement(children: .combine)
    }

    // MARK: - Coach opener (round-9 V4)

    /// One line in the coach's voice. The plan's coach note, first sentence,
    /// else a fixed line. Tap opens the Coach tab with the session as context.
    private var openerLine: String {
        if isAdapted { return "I changed today. Ask me why." }
        if let note = plan.coachNote?.trimmingCharacters(in: .whitespacesAndNewlines), !note.isEmpty {
            let first = note.split(whereSeparator: { ".!?".contains($0) }).first.map(String.init) ?? note
            let line = first.trimmingCharacters(in: .whitespaces)
            if line.count <= 64 { return line + (first.count < note.count ? "." : "") }
        }
        return "Questions about today? Ask me."
    }

    private var openerPrompt: String {
        let title = workouts.first?.title ?? "today's session"
        if isAdapted { return "Why did you change today's session (\(title))?" }
        return "About today's session, \(title): what should I know before I start?"
    }

    @ViewBuilder private var coachOpener: some View {
        if let onAskCoach {
            Button {
                onAskCoach(openerPrompt)
            } label: {
                HStack(alignment: .top, spacing: DS.Spacing.s) {
                    Image(systemName: "sparkles")
                        .font(.system(size: 13))
                        .symbolRenderingMode(.hierarchical)
                        .foregroundStyle(.white)
                        .padding(.top, 1)
                    Text(openerLine)
                        .font(.system(size: 13))
                        .foregroundStyle(.white)
                        .multilineTextAlignment(.leading)
                    Spacer()
                    Image(systemName: "arrow.up.right")
                        .font(.system(size: 11, weight: .semibold))
                        .foregroundStyle(DS.Colors.outline)
                        .padding(.top, 2)
                }
                .frame(minHeight: 32)
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .accessibilityLabel("Ask the coach about today's session")
        }
    }

    // MARK: - Helpers

    /// "Run · Zone 2 · 140–150 bpm" — each part only when known.
    private func subtitle(_ w: Workout) -> String {
        var parts = [SessionFormat.sportName(w.sport)]
        if let zone = SessionFormat.zone(of: w) { parts.append("Zone \(zone)") }
        if let hr = SessionFormat.heartRate(w.hrTarget) { parts.append(hr) }
        return parts.joined(separator: " · ")
    }

    private var hairline: some View {
        Rectangle().fill(Color.white.opacity(0.06)).frame(height: 1)
    }
}

/// Rest day: the card shrinks to one line and the coach's opener.
/// Round-12 V3 of the Today variants.
struct RestDayCard: View {
    var note: String? = nil
    var onAskCoach: ((String) -> Void)? = nil

    var body: some View {
        VStack(alignment: .leading, spacing: DS.Spacing.m) {
            DS.SectionLabel(text: "Today's session")
                .padding(.horizontal, DS.Spacing.xs)
            VStack(alignment: .leading, spacing: DS.Spacing.l) {
                HStack(spacing: DS.Spacing.s) {
                    Image(systemName: "bed.double.fill")
                        .font(.system(size: 15, weight: .semibold))
                        .symbolRenderingMode(.hierarchical)
                        .foregroundStyle(.white)
                    Text("Rest day")
                        .font(.system(size: 15, weight: .semibold))
                        .foregroundStyle(.white)
                    Spacer()
                    DS.MonoLabel(text: "no session")
                }
                Text(note?.isEmpty == false ? note! : "Nothing structured. Walk, stretch, sleep.")
                    .font(.system(size: 13))
                    .foregroundStyle(DS.Colors.onSurface)
                    .fixedSize(horizontal: false, vertical: true)
                if let onAskCoach {
                    Button {
                        onAskCoach("It's a rest day. What counts as recovery today, and what should I avoid?")
                    } label: {
                        HStack(alignment: .top, spacing: DS.Spacing.s) {
                            Image(systemName: "sparkles")
                                .font(.system(size: 13))
                                .symbolRenderingMode(.hierarchical)
                                .foregroundStyle(.white)
                            Text("Rest is training. Ask me what counts.")
                                .font(.system(size: 13))
                                .foregroundStyle(.white)
                            Spacer()
                            Image(systemName: "arrow.up.right")
                                .font(.system(size: 11, weight: .semibold))
                                .foregroundStyle(DS.Colors.outline)
                        }
                        .frame(minHeight: 32)
                        .contentShape(Rectangle())
                    }
                    .buttonStyle(.plain)
                    .accessibilityLabel("Ask the coach about rest days")
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .outlineCard()
        }
    }
}

/// Step durations as a thin bar, each segment in its zone colour.
struct SessionSegmentBar: View {
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

    /// The instrument table's column: always mm:ss, so the digits line up.
    static func tableDuration(_ raw: String) -> String {
        if raw.contains(":") {
            let parts = raw.split(separator: ":")
            if parts.count == 2 { return raw }
        }
        if let m = minutes(raw) { return String(format: "%02d:00", m) }
        return raw
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
