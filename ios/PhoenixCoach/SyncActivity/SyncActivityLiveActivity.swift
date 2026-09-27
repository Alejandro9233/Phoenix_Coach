import ActivityKit
import WidgetKit
import SwiftUI

/// Deep sync in the Dynamic Island and on the lock screen.
///
/// Decided 2026-09-26 (Alex switched from the 2026-09-15 pick, V3 step strip,
/// to V5 "ring"): the sync's four steps as a segmented ring around the
/// glyph — done and current segments bright, upcoming faint — beside the
/// "Deep sync" micro-label and the stage sentence, with a thin elapsed timer
/// trailing. The compact island shows a small ring and the timer.
///
/// The timer is `Text(timerInterval:)`, so it ticks without an update per
/// second; the app only sends a state when the stage changes, and the last
/// one freezes the timer through `pauseTime`.
///
/// No spinner. Live Activities are rendered snapshots, so an indeterminate
/// `ProgressView` sits still and reads as a hang. The ticking timer and the
/// advancing ring are the proof of life. Color is reserved for meaning:
/// white throughout, red only on failure, and the outcome is also said in
/// words.
struct SyncActivityLiveActivity: Widget {
    var body: some WidgetConfiguration {
        ActivityConfiguration(for: SyncActivityAttributes.self) { context in
            SyncRow(context: context)
                .padding(DS.Spacing.l)
                .activityBackgroundTint(DS.Colors.background)
                .activitySystemActionForegroundColor(DS.Colors.onSurface)
        } dynamicIsland: { context in
            DynamicIsland {
                DynamicIslandExpandedRegion(.bottom) {
                    SyncRow(context: context)
                }
            } compactLeading: {
                SyncRing(state: context.state, size: 20, lineWidth: 2, glyph: false)
            } compactTrailing: {
                SyncTimer(context: context, size: 14, weight: .semibold)
                    .frame(width: 40)
            } minimal: {
                SyncRing(state: context.state, size: 20, lineWidth: 2, glyph: false)
            }
            .keylineTint(DS.Colors.accent)
        }
    }
}

// MARK: - Pieces

/// Expanded island and lock screen share one row: ring with the glyph
/// inside, label over the stage sentence, timer trailing.
private struct SyncRow: View {
    let context: ActivityViewContext<SyncActivityAttributes>

    var body: some View {
        HStack(spacing: DS.Spacing.m) {
            SyncRing(state: context.state, size: 40, lineWidth: 2.5, glyph: true)
            VStack(alignment: .leading, spacing: DS.Spacing.xs) {
                SyncLabel(text: "Deep sync")
                SyncStageText(context: context)
            }
            Spacer(minLength: DS.Spacing.s)
            SyncTimer(context: context, size: 26, weight: .ultraLight)
        }
        .accessibilityElement(children: .combine)
    }
}

/// The four steps as ring segments: done and current lit, upcoming faint.
/// The same three-state idea as Today's readiness arc. On failure the
/// current segment takes the red so the ring says where it stopped.
private struct SyncRing: View {
    let state: SyncActivityAttributes.ContentState
    let size: CGFloat
    let lineWidth: CGFloat
    let glyph: Bool

    private var count: Int { SyncActivityAttributes.steps.count }

    var body: some View {
        ZStack {
            ForEach(0..<count, id: \.self) { i in
                let span = 1.0 / Double(count)
                let gap = 0.035
                Circle()
                    .trim(from: Double(i) * span + gap / 2, to: Double(i + 1) * span - gap / 2)
                    .stroke(fill(i), style: StrokeStyle(lineWidth: lineWidth, lineCap: .round))
                    .rotationEffect(.degrees(-90))
                    .shadow(color: fill(i).opacity(i == state.step && state.outcome == .running ? 0.5 : 0), radius: 4)
            }
            if glyph {
                SyncGlyph(outcome: state.outcome)
            }
        }
        .frame(width: size, height: size)
        .accessibilityLabel("Step \(state.step + 1) of \(count), \(SyncActivityAttributes.steps[state.step])")
    }

    private func fill(_ i: Int) -> Color {
        if state.outcome == .failed && i == state.step { return DS.Colors.danger }
        return i <= state.step ? Color.white : Color.white.opacity(0.14)
    }
}

/// One glyph per outcome, same symbol family as the in-app pill.
private struct SyncGlyph: View {
    let outcome: SyncActivityAttributes.Outcome

    var body: some View {
        Image(systemName: symbol)
            .font(.system(size: 13, weight: .semibold))
            .symbolRenderingMode(.hierarchical)
            .foregroundStyle(outcome == .failed ? DS.Colors.danger : DS.Colors.accent)
    }

    private var symbol: String {
        switch outcome {
        case .running: "arrow.triangle.2.circlepath"
        case .synced: "checkmark"
        case .failed: "exclamationmark.triangle"
        }
    }
}

/// The section-header construct from the app, unchanged.
private struct SyncLabel: View {
    let text: String

    var body: some View {
        Text(text)
            .font(.system(size: 11, weight: .bold))
            .textCase(.uppercase)
            .tracking(DS.Tracking.wide)
            .foregroundStyle(DS.Colors.outline)
    }
}

/// The stage sentence under the label. Red duplicates the failed glyph, so
/// the meaning is carried twice: once in color, once in the words.
///
/// Stale means the app has been suspended and can no longer report the
/// stage; the job is still running server-side. Say that, in the label
/// color, rather than leave a minutes-old stage sitting there as if live.
private struct SyncStageText: View {
    let context: ActivityViewContext<SyncActivityAttributes>

    var body: some View {
        Text(text)
            .font(.system(size: 13, weight: .medium))
            .foregroundStyle(color)
            .lineLimit(2)
            .multilineTextAlignment(.leading)
    }

    private var isStale: Bool {
        context.isStale && context.state.outcome == .running
    }

    private var text: String {
        isStale ? "Still syncing on the server. Open the app for the result." : context.state.stage
    }

    private var color: Color {
        if context.state.outcome == .failed { return DS.Colors.danger }
        return isStale ? DS.Colors.outline : DS.Colors.onSurface
    }
}

/// Elapsed time since the job started. Counts up on its own; `endedAt`
/// pauses it on the final state.
private struct SyncTimer: View {
    let context: ActivityViewContext<SyncActivityAttributes>
    let size: CGFloat
    let weight: Font.Weight

    var body: some View {
        Text(timerInterval: context.attributes.startedAt...Date.distantFuture,
             pauseTime: context.state.endedAt,
             countsDown: false,
             showsHours: false)
            .font(.system(size: size, weight: weight))
            .monospacedDigit()
            .foregroundStyle(.white)
            .multilineTextAlignment(.trailing)
    }
}

// MARK: - Previews

private extension SyncActivityAttributes {
    static var preview: SyncActivityAttributes {
        SyncActivityAttributes(startedAt: .now.addingTimeInterval(-42))
    }
}

private extension SyncActivityAttributes.ContentState {
    static var running: Self {
        .init(stage: "Evaluating recovery...", step: 1, outcome: .running, endedAt: nil)
    }
    static var synced: Self {
        .init(stage: "Biometrics synced", step: 3, outcome: .synced, endedAt: .now)
    }
    static var failed: Self {
        .init(stage: "Deep sync was interrupted. Pull to refresh in a minute.", step: 0, outcome: .failed, endedAt: .now)
    }
}

#Preview("Lock screen", as: .content, using: SyncActivityAttributes.preview) {
    SyncActivityLiveActivity()
} contentStates: {
    SyncActivityAttributes.ContentState.running
    SyncActivityAttributes.ContentState.synced
    SyncActivityAttributes.ContentState.failed
}

#Preview("Island expanded", as: .dynamicIsland(.expanded), using: SyncActivityAttributes.preview) {
    SyncActivityLiveActivity()
} contentStates: {
    SyncActivityAttributes.ContentState.running
    SyncActivityAttributes.ContentState.synced
    SyncActivityAttributes.ContentState.failed
}

#Preview("Island compact", as: .dynamicIsland(.compact), using: SyncActivityAttributes.preview) {
    SyncActivityLiveActivity()
} contentStates: {
    SyncActivityAttributes.ContentState.running
    SyncActivityAttributes.ContentState.synced
}
