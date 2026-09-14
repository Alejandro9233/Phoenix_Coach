import SwiftUI

/// The training timeline link as a context row: where you are in the
/// phase, a tick strip, weeks to the race. Round-11 V2 of the Today variants.
struct TimelineRow: View {
    let context: TrainingContext?

    private var phaseLabel: String? {
        guard let name = context?.phaseName ?? context?.phase else { return nil }
        // "Phase 2: Build" → "Build"
        if let colon = name.firstIndex(of: ":") {
            return name[name.index(after: colon)...].trimmingCharacters(in: .whitespaces)
        }
        return name.capitalized
    }

    private var line: String {
        var parts: [String] = []
        if let p = phaseLabel { parts.append(p) }
        if let w = context?.phaseWeek, let t = context?.phaseTotalWeeks, t > 0 {
            parts.append("week \(w) of \(t)")
        }
        if let r = context?.weeksToRace {
            parts.append(r <= 0 ? "race week" : "race in \(r) wk")
        }
        return parts.isEmpty ? "View phases and the full calendar" : parts.joined(separator: " · ")
    }

    var body: some View {
        NavigationLink(destination: BlockCalendarView()) {
            VStack(spacing: DS.Spacing.m) {
                hairline
                VStack(alignment: .leading, spacing: DS.Spacing.s) {
                    HStack(spacing: DS.Spacing.m) {
                        DS.SectionLabel(text: "Training timeline")
                        Spacer()
                        if let w = context?.phaseWeek, let t = context?.phaseTotalWeeks, t > 0, t <= 20 {
                            HStack(spacing: 2) {
                                ForEach(0..<t, id: \.self) { i in
                                    Rectangle()
                                        .fill(i < w ? Color.white : Color.white.opacity(0.15))
                                        .frame(width: 3, height: i == w - 1 ? 12 : 8)
                                }
                            }
                            .accessibilityHidden(true)
                        }
                        Image(systemName: "chevron.right")
                            .font(.system(size: 11, weight: .semibold))
                            .foregroundStyle(DS.Colors.outline)
                    }
                    DS.MonoLabel(text: line, color: .white)
                }
                hairline
            }
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .accessibilityLabel("Training timeline. \(line)")
    }

    private var hairline: some View {
        Rectangle().fill(Color.white.opacity(0.08)).frame(height: 1)
    }
}
