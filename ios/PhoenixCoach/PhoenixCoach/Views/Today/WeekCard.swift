import SwiftUI

/// "This week": sessions as segments, the run-km row, the score demoted to
/// a mono readout. Round-10 V3 of the Today variants.
struct WeekCard: View {
    let progress: WeekProgress

    var body: some View {
        VStack(alignment: .leading, spacing: DS.Spacing.m) {
            HStack(alignment: .firstTextBaseline) {
                DS.SectionLabel(text: "This week")
                Spacer()
                if let score = progress.complianceScore {
                    DS.MonoLabel(text: "adherence \(score)", color: .white)
                }
            }
            if progress.sessionsPlanned > 0 && progress.sessionsPlanned <= 14 {
                HStack(spacing: DS.Spacing.m) {
                    DS.SectionLabel(text: "Sessions")
                    HStack(spacing: 3) {
                        ForEach(0..<progress.sessionsPlanned, id: \.self) { i in
                            RoundedRectangle(cornerRadius: 2)
                                .fill(i < progress.sessionsCompleted ? Color.white : Color.white.opacity(0.10))
                                .frame(height: 6)
                        }
                    }
                    DS.MonoLabel(text: "\(progress.sessionsCompleted) / \(progress.sessionsPlanned)", color: .white)
                }
                .accessibilityElement(children: .combine)
                .accessibilityLabel("\(progress.sessionsCompleted) of \(progress.sessionsPlanned) sessions done")
            }
            // Protected run km, week to date vs the Python target. Hidden when
            // the target is nil — a zero-denominator bar would be a lie.
            if let done = progress.runKmDone, let target = progress.runKmTarget, target > 0 {
                HStack(spacing: DS.Spacing.m) {
                    DS.SectionLabel(text: "Run km")
                    DS.ThinBar(fraction: done / target)
                    DS.MonoLabel(text: String(format: "%.1f / %.0f", done, target), color: .white)
                }
                .accessibilityElement(children: .combine)
                .accessibilityLabel(String(format: "Run %.1f of %.0f kilometres", done, target))
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .outlineCard()
    }
}
