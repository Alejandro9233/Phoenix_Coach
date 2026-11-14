import SwiftUI

/// Today's adapted session next to the original, with the recovery numbers
/// that caused the change. Opened from the adapted banner on Today.
struct AdaptationComparisonSheet: View {
    let todayPlan: DayPlan
    let latestRecovery: RecoverySummary?
    @Binding var preferOriginal: Bool
    /// Calls the backend to restore the original plan, then reloads. The
    /// button was cosmetic before this existed — it only recolored a card.
    var onUseOriginal: (() async throws -> Void)? = nil
    @State private var isReverting = false
    @State private var revertFailed = false
    @Environment(\.dismiss) private var dismiss
    
    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 20) {
                    // Subtitle banner
                    Text("Coach modified today's prescription based on overnight recovery telemetry.")
                        .font(.system(size: 13, weight: .regular))
                        .foregroundStyle(DS.Colors.onSurface)
                    
                    // Biometric Triggers Card
                    VStack(alignment: .leading, spacing: 12) {
                        HStack {
                            Image(systemName: "waveform.path.ecg")
                                .font(.system(size: 12, weight: .bold))
                                .foregroundStyle(DS.Colors.accent)
                            Text("OVERNIGHT TELEMETRY TRIGGERS")
                                .font(.system(size: 10, weight: .bold))
                                .tracking(1.0)
                                .foregroundStyle(DS.Colors.outline)
                        }
                        
                        HStack(spacing: 10) {
                            if let hrv = latestRecovery?.hrvMs {
                                telemetryMetricTile(
                                    label: "HRV",
                                    value: "\(Int(hrv)) ms",
                                    subtitle: "Telemetry Driver",
                                    isWarning: true
                                )
                            }
                            if let rhr = latestRecovery?.restingHr {
                                telemetryMetricTile(
                                    label: "RESTING HR",
                                    value: "\(rhr) bpm",
                                    subtitle: "Elevated",
                                    isWarning: false
                                )
                            }
                            if let load = latestRecovery?.loadRatio {
                                telemetryMetricTile(
                                    label: "LOAD RATIO",
                                    value: String(format: "%.2f", load),
                                    subtitle: load > 1.3 ? "High Load" : "Balanced",
                                    isWarning: load > 1.3
                                )
                            }
                        }
                        
                        if let reason = todayPlan.adaptation, !reason.isEmpty {
                            HStack(alignment: .top, spacing: 8) {
                                Image(systemName: "sparkles")
                                    .font(.system(size: 12))
                                    .foregroundStyle(DS.Colors.accent)
                                    .padding(.top, 2)
                                Text(reason)
                                    .font(.system(size: 12, weight: .medium))
                                    .foregroundStyle(DS.Colors.primaryText)
                            }
                            .padding(10)
                            .frame(maxWidth: .infinity, alignment: .leading)
                            .background(Color.white.opacity(0.04))
                            .clipShape(RoundedRectangle(cornerRadius: 8))
                        }
                    }
                    .glassCard()
                    
                    // Side-by-Side / Stacked Session Comparison
                    VStack(alignment: .leading, spacing: 14) {
                        Text("PROTOCOL COMPARISON")
                            .font(.system(size: 10, weight: .bold))
                            .tracking(1.0)
                            .foregroundStyle(DS.Colors.outline)
                        
                        // AI Adapted Card (Recommended)
                        VStack(alignment: .leading, spacing: 10) {
                            HStack {
                                Text("AI ADAPTED PROTOCOL")
                                    .font(.system(size: 11, weight: .bold))
                                    .foregroundStyle(DS.Colors.accent)
                                Spacer()
                                if !preferOriginal {
                                    Text("ACTIVE")
                                        .font(.system(size: 8, weight: .bold))
                                        .foregroundStyle(.black)
                                        .padding(.horizontal, 6)
                                        .padding(.vertical, 2)
                                        .background(DS.Colors.accent)
                                        .clipShape(Capsule())
                                }
                            }
                            
                            if let workouts = todayPlan.workouts, !workouts.isEmpty {
                                ForEach(workouts, id: \.title) { w in
                                    comparisonWorkoutRow(w, isAccent: true)
                                }
                            }
                        }
                        .padding(14)
                        .background(Color.white.opacity(0.05))
                        .clipShape(RoundedRectangle(cornerRadius: DS.Radius.medium))
                        .overlay(
                            RoundedRectangle(cornerRadius: DS.Radius.medium)
                                .stroke(!preferOriginal ? Color.white.opacity(0.4) : Color.white.opacity(0.1), lineWidth: !preferOriginal ? 1.5 : 1)
                        )
                        
                        // Original Blueprint Card
                        VStack(alignment: .leading, spacing: 10) {
                            HStack {
                                Text("ORIGINAL BLUEPRINT")
                                    .font(.system(size: 11, weight: .bold))
                                    .foregroundStyle(DS.Colors.outline)
                                Spacer()
                                if preferOriginal {
                                    Text("ACTIVE (OVERRIDE)")
                                        .font(.system(size: 8, weight: .bold))
                                        .foregroundStyle(.black)
                                        .padding(.horizontal, 6)
                                        .padding(.vertical, 2)
                                        .background(DS.Colors.outline)
                                        .clipShape(Capsule())
                                }
                            }
                            
                            let origWorkouts = todayPlan.originalWorkouts ?? todayPlan.workouts ?? []
                            if !origWorkouts.isEmpty {
                                ForEach(origWorkouts, id: \.title) { w in
                                    comparisonWorkoutRow(w, isAccent: false)
                                }
                            } else {
                                Text("No original session data recorded.")
                                    .font(.caption)
                                    .foregroundStyle(DS.Colors.outline)
                            }
                        }
                        .padding(14)
                        .background(Color.black.opacity(0.3))
                        .clipShape(RoundedRectangle(cornerRadius: DS.Radius.medium))
                        .overlay(
                            RoundedRectangle(cornerRadius: DS.Radius.medium)
                                .stroke(preferOriginal ? Color.white.opacity(0.4) : Color.white.opacity(0.08), lineWidth: 1)
                        )
                    }
                    
                    // Coach's Rationale
                    if let rationale = todayPlan.rationale ?? todayPlan.coachNote, !rationale.isEmpty {
                        VStack(alignment: .leading, spacing: 8) {
                            HStack {
                                Image(systemName: "brain.head.profile")
                                    .font(.system(size: 11, weight: .bold))
                                    .foregroundStyle(DS.Colors.accent)
                                Text("COACH'S INTENT")
                                    .font(.system(size: 10, weight: .bold))
                                    .tracking(1.0)
                                    .foregroundStyle(DS.Colors.outline)
                            }
                            Text(rationale)
                                .font(.system(size: 12, weight: .regular))
                                .foregroundStyle(DS.Colors.onSurface)
                                .lineSpacing(3)
                        }
                        .glassCard()
                    }
                    
                    // Action Decision Buttons
                    VStack(spacing: 10) {
                        Button {
                            preferOriginal = false
                            UIImpactFeedbackGenerator(style: .medium).impactOccurred()
                            dismiss()
                        } label: {
                            HStack {
                                Image(systemName: "checkmark.circle.fill")
                                Text("Keep AI Adaptation (Recommended)")
                            }
                            .font(.system(size: 13, weight: .bold))
                            .foregroundStyle(.black)
                            .frame(maxWidth: .infinity)
                            .padding(.vertical, 14)
                            .background(DS.Colors.accent)
                            .clipShape(RoundedRectangle(cornerRadius: DS.Radius.medium))
                        }
                        
                        Button {
                            guard !isReverting else { return }
                            preferOriginal = true
                            isReverting = true
                            revertFailed = false
                            Task {
                                do {
                                    try await onUseOriginal?()
                                    UIImpactFeedbackGenerator(style: .medium).impactOccurred()
                                    dismiss()
                                } catch {
                                    isReverting = false
                                    revertFailed = true
                                }
                            }
                        } label: {
                            HStack {
                                if isReverting {
                                    ProgressView()
                                        .controlSize(.small)
                                } else {
                                    Image(systemName: "arrow.counterclockwise")
                                }
                                Text("Override & Use Original Plan")
                            }
                            .font(.system(size: 13, weight: .semibold))
                            .foregroundStyle(DS.Colors.onSurface)
                            .frame(maxWidth: .infinity)
                            .padding(.vertical, 12)
                            .background(Color.white.opacity(0.06))
                            .clipShape(RoundedRectangle(cornerRadius: DS.Radius.medium))
                            .overlay(
                                RoundedRectangle(cornerRadius: DS.Radius.medium)
                                    .stroke(Color.white.opacity(0.12), lineWidth: 1)
                            )
                        }
                        .disabled(isReverting)

                        if revertFailed {
                            Text("Couldn't restore the original plan — check the connection and try again.")
                                .font(.system(size: 11, weight: .regular))
                                .foregroundStyle(DS.Colors.danger)
                                .transition(.opacity)
                        }
                    }
                    .padding(.top, 6)
                    .animation(DS.Animation.quick, value: revertFailed)
                }
                .padding(20)
            }
            .navigationTitle("Adaptation Telemetry")
            .navigationBarTitleDisplayMode(.inline)
            .glassSheet()
        }
        .presentationDetents([.fraction(0.88), .large])
        .presentationDragIndicator(.visible)
    }
    
    private func telemetryMetricTile(label: String, value: String, subtitle: String, isWarning: Bool) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(label)
                .font(.system(size: 9, weight: .bold))
                .tracking(0.8)
                .foregroundStyle(DS.Colors.outline)
            Text(value)
                .font(.system(size: 15, weight: .bold))
                .foregroundStyle(isWarning ? DS.Colors.warning : DS.Colors.primaryText)
            Text(subtitle)
                .font(.system(size: 9, weight: .medium))
                .foregroundStyle(isWarning ? DS.Colors.warning.opacity(0.8) : DS.Colors.outline)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(10)
        .background(Color.white.opacity(0.03))
        .clipShape(RoundedRectangle(cornerRadius: 8))
    }
    
    private func comparisonWorkoutRow(_ w: Workout, isAccent: Bool) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack(spacing: 8) {
                Image(systemName: w.sportIcon)
                    .font(.system(size: 13, weight: .semibold))
                    .foregroundStyle(isAccent ? DS.Colors.accent : DS.Colors.outline)
                Text(w.title)
                    .font(.system(size: 13, weight: .bold))
                    .foregroundStyle(DS.Colors.primaryText)
                Spacer()
                if let t = w.totalTime {
                    Text(t)
                        .font(.system(size: 12, weight: .bold))
                        .foregroundStyle(isAccent ? DS.Colors.accent : DS.Colors.outline)
                }
            }
            if let hr = w.hrTarget {
                Text("Target HR: \(hr)")
                    .font(.system(size: 10))
                    .foregroundStyle(DS.Colors.outline)
            }
            if !w.steps.isEmpty {
                HStack(spacing: 6) {
                    ForEach(Array(w.steps.prefix(4).enumerated()), id: \.offset) { _, step in
                        Text("\(step.type.prefix(4).uppercased()) \(step.duration)")
                            .font(.system(size: 8, weight: .medium))
                            .foregroundStyle(DS.Colors.outline)
                            .padding(.horizontal, 5)
                            .padding(.vertical, 2)
                            .background(Color.white.opacity(0.04))
                            .clipShape(RoundedRectangle(cornerRadius: 4))
                    }
                }
            }
        }
    }
}
