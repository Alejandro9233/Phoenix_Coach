import SwiftUI

/// The Today hero: the engine's recovery status as an open arc of four
/// segments (HRV, RHR, form, load), the state word inside, the reason under
/// it, and the numbers pinned to the corners. Round-5 V1 of the Today
/// variants (references/today-v2.md).
struct ReadinessHeader: View {
    let recovery: RecoveryStatus?
    let hrvMs: Double?
    let restingHr: Int?
    let ati: Double?
    let cti: Double?
    let dateText: String
    let syncedText: String
    var onHRV: () -> Void = {}
    var onRHR: () -> Void = {}
    var onLoad: () -> Void = {}

    private static let order = ["hrv", "rhr", "form", "load"]
    private var tint: Color { DS.Colors.readiness(recovery?.status) }

    var body: some View {
        ZStack {
            arc
            center
        }
        .frame(maxWidth: .infinity)
        .frame(height: 248)
        .overlay(alignment: .topLeading) {
            DS.MonoLabel(text: dateText).padding(.leading, DS.Spacing.xs)
        }
        .overlay(alignment: .topTrailing) {
            DS.MonoLabel(text: syncedText).padding(.trailing, DS.Spacing.xs)
        }
        .overlay(alignment: .bottomLeading) { leftReadouts.padding(.leading, DS.Spacing.xs) }
        .overlay(alignment: .bottomTrailing) { rightReadouts.padding(.trailing, DS.Spacing.xs) }
    }

    // MARK: Arc

    private var arc: some View {
        let gap = 0.012
        let seg = (0.75 - gap * 3) / 4
        return ZStack {
            ForEach(Array(Self.order.enumerated()), id: \.offset) { i, key in
                let a = Double(i) * (seg + gap)
                let state = recovery?.checks?[key] ?? "unknown"
                segment(from: a, to: a + seg, state: state)
            }
            Circle().trim(from: 0, to: 0.75)
                .stroke(Color.white.opacity(0.06), style: StrokeStyle(lineWidth: 1, dash: [1, 6]))
                .rotationEffect(.degrees(135))
                .frame(width: 178, height: 178)
        }
        .accessibilityHidden(true)
    }

    /// pass → lit in the tint; concern → the tint dimmed; unknown → a dashed
    /// neutral trace. Three states, so no-data never reads as passed.
    @ViewBuilder
    private func segment(from: Double, to: Double, state: String) -> some View {
        let shape = Circle().trim(from: from, to: to)
        switch state {
        case "pass":
            shape.stroke(tint, style: StrokeStyle(lineWidth: 2, lineCap: .round))
                .rotationEffect(.degrees(135)).frame(width: 200, height: 200)
                .shadow(color: tint.opacity(0.5), radius: 8)
        case "concern":
            shape.stroke(tint.opacity(0.18), style: StrokeStyle(lineWidth: 2, lineCap: .round))
                .rotationEffect(.degrees(135)).frame(width: 200, height: 200)
        default:
            shape.stroke(Color.white.opacity(0.14), style: StrokeStyle(lineWidth: 1, lineCap: .round, dash: [2, 4]))
                .rotationEffect(.degrees(135)).frame(width: 200, height: 200)
        }
    }

    // MARK: Centre

    private var center: some View {
        VStack(spacing: DS.Spacing.s) {
            DS.SectionLabel(text: "Readiness")
            Text(recovery?.word ?? "No data")
                .font(.system(size: 36, weight: .ultraLight))
                .foregroundStyle(.white)
                .contentTransition(.opacity)
            Text(recovery?.detail ?? "Pull down to sync.")
                .font(.system(size: 11))
                .foregroundStyle(DS.Colors.outline)
                .multilineTextAlignment(.center)
                .lineLimit(3)
                .frame(width: 156)
        }
        .accessibilityElement(children: .combine)
        .accessibilityLabel("Readiness \(recovery?.word ?? "no data"). \(recovery?.detail ?? "")")
    }

    // MARK: Corner readouts

    private var leftReadouts: some View {
        VStack(alignment: .leading, spacing: DS.Spacing.xs) {
            Button(action: onHRV) {
                VStack(alignment: .leading, spacing: DS.Spacing.xs) {
                    if let hrv = hrvMs {
                        DS.MonoLabel(text: "HRV \(Int(hrv)) ms", color: .white)
                    } else {
                        DS.MonoLabel(text: "HRV —")
                    }
                    if let pct = recovery?.hrvVsBaseline, pct != "unknown" {
                        DS.MonoLabel(text: "\(pct) baseline")
                    }
                }
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .accessibilityLabel("HRV, open chart")
            Button(action: onRHR) {
                DS.MonoLabel(text: rhrLine)
                    .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .accessibilityLabel("Resting heart rate, open chart")
        }
    }

    private var rhrLine: String {
        var parts: [String] = []
        if let rhr = restingHr { parts.append("RHR \(rhr)") } else { parts.append("RHR —") }
        if let t = recovery?.rhrTrend, t != "unknown" {
            parts.append(t.replacingOccurrences(of: "_", with: " "))
        }
        return parts.joined(separator: " · ")
    }

    private var rightReadouts: some View {
        VStack(alignment: .trailing, spacing: DS.Spacing.xs) {
            if let tib = recovery?.tib {
                DS.MonoLabel(text: String(format: "form %+.0f", tib), color: .white)
            }
            Button(action: onLoad) {
                VStack(alignment: .trailing, spacing: DS.Spacing.xs) {
                    if let load = recovery?.loadRatio {
                        DS.MonoLabel(text: String(format: "load %.2f", load))
                    }
                    if let ati, let cti {
                        DS.MonoLabel(text: "ATL \(Int(ati)) · CTL \(Int(cti))")
                    }
                }
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .accessibilityLabel("Training load, open chart")
        }
    }
}
