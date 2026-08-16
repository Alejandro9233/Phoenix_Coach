import SwiftUI

/// Confirmation card for a reported injury or soreness.
///
/// The backend detects the report and computes what it *would* change; this card
/// is the human gate before any of it is written. Nothing hits the database
/// until Confirm is tapped, because an LLM misreading "my legs are dead after
/// that tempo" as a three-day running ban would otherwise silently gut a week.
///
/// Per-day choice is deliberate: swapping a long run for a bike keeps the
/// aerobic load, resting drops it. Which one is right depends on how the calf
/// actually feels, and only the athlete knows that.
struct IssueProposalCard: View {
    let proposal: IssueProposal
    /// Called with the confirmed issue and one choice per day. The parent owns
    /// the network call so the card stays presentational.
    let onConfirm: (ReportedIssue, [String: String]) -> Void
    let onDismiss: () -> Void

    @State private var choices: [String: String] = [:]
    @State private var durationDays: Int
    @State private var severity: Int
    @State private var isSubmitting = false

    init(
        proposal: IssueProposal,
        onConfirm: @escaping (ReportedIssue, [String: String]) -> Void,
        onDismiss: @escaping () -> Void
    ) {
        self.proposal = proposal
        self.onConfirm = onConfirm
        self.onDismiss = onDismiss
        _durationDays = State(initialValue: proposal.issue.durationDays)
        _severity = State(initialValue: proposal.issue.severity)
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            header
            Divider().overlay(DS.Colors.outline.opacity(0.3))
            issueSummary
            Divider().overlay(DS.Colors.outline.opacity(0.3))

            Text("AFFECTED SESSIONS")
                .font(.system(size: 10, weight: .heavy))
                .tracking(1.6)
                .foregroundStyle(DS.Colors.outline)

            ForEach(proposal.affectedDays) { day in
                dayRow(day)
            }

            actions
        }
        .padding(16)
        .background(DS.Colors.surface)
        .clipShape(RoundedRectangle(cornerRadius: 16))
        .overlay(
            RoundedRectangle(cornerRadius: 16)
                .stroke(DS.Colors.warning.opacity(0.4), lineWidth: 1)
        )
        .onAppear(perform: seedDefaultChoices)
    }

    // MARK: - Sections

    private var header: some View {
        HStack(spacing: 8) {
            Image(systemName: "bandage.fill")
                .foregroundStyle(DS.Colors.warning)
            Text("Log this?")
                .font(.system(size: 15, weight: .bold))
                .foregroundStyle(DS.Colors.primaryText)
            Spacer()
        }
    }

    private var issueSummary: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(spacing: 6) {
                Text(proposal.issue.bodyPart)
                    .font(.system(size: 14, weight: .semibold))
                    .foregroundStyle(DS.Colors.primaryText)
                Text("·")
                    .foregroundStyle(DS.Colors.outline)
                Text(sportsLabel)
                    .font(.system(size: 13))
                    .foregroundStyle(DS.Colors.outline)
            }

            Stepper(value: $severity, in: 1...10) {
                HStack {
                    Text("Severity")
                        .font(.system(size: 13))
                        .foregroundStyle(DS.Colors.onSurface)
                    Spacer()
                    Text("\(severity)/10")
                        .font(.system(size: 13, weight: .semibold))
                        .foregroundStyle(severityColor)
                }
            }

            Stepper(value: $durationDays, in: 1...14) {
                HStack {
                    Text("Avoid for")
                        .font(.system(size: 13))
                        .foregroundStyle(DS.Colors.onSurface)
                    Spacer()
                    Text("\(durationDays) day\(durationDays == 1 ? "" : "s")")
                        .font(.system(size: 13, weight: .semibold))
                        .foregroundStyle(DS.Colors.primaryText)
                }
            }

            // Shortening the window here only narrows what Confirm writes. The
            // day list itself came from the original window, so lengthening it
            // needs a fresh report rather than a silently wrong preview.
            if durationDays < proposal.issue.durationDays {
                Text("Only days inside the new window will change.")
                    .font(.system(size: 11))
                    .foregroundStyle(DS.Colors.outline)
            }
        }
    }

    private func dayRow(_ day: AffectedDay) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: 6) {
                Text(day.isToday ? "\(day.day) (today)" : day.day)
                    .font(.system(size: 13, weight: .semibold))
                    .foregroundStyle(DS.Colors.primaryText)
                Spacer()
                if let blocked = day.blockedWorkouts.first {
                    Text(blocked.title)
                        .font(.system(size: 12))
                        .foregroundStyle(DS.Colors.outline)
                        .strikethrough(true, color: DS.Colors.outline)
                        .lineLimit(1)
                }
            }

            Picker("", selection: binding(for: day)) {
                ForEach(day.options) { option in
                    Text(option.label).tag(option.id)
                }
            }
            .pickerStyle(.segmented)
            .disabled(isSubmitting || !isInWindow(day))

            if let detail = day.options.first(where: { $0.id == choices[day.day] })?.detail {
                Text(detail)
                    .font(.system(size: 11))
                    .foregroundStyle(DS.Colors.outline)
            }
        }
        .padding(10)
        .background(DS.Colors.background.opacity(isInWindow(day) ? 1 : 0.4))
        .clipShape(RoundedRectangle(cornerRadius: 10))
        .opacity(isInWindow(day) ? 1 : 0.45)
    }

    private var actions: some View {
        HStack(spacing: 10) {
            Button(action: onDismiss) {
                Text("Not now")
                    .font(.system(size: 13, weight: .semibold))
                    .frame(maxWidth: .infinity)
                    .padding(.vertical, 10)
                    .background(DS.Colors.background)
                    .foregroundStyle(DS.Colors.onSurface)
                    .clipShape(RoundedRectangle(cornerRadius: 10))
            }
            .buttonStyle(.plain)
            .disabled(isSubmitting)

            Button(action: confirm) {
                HStack(spacing: 6) {
                    if isSubmitting {
                        ProgressView().controlSize(.small).tint(.black)
                    }
                    Text(isSubmitting ? "Updating plan..." : "Confirm")
                        .font(.system(size: 13, weight: .bold))
                }
                .frame(maxWidth: .infinity)
                .padding(.vertical, 10)
                .background(DS.Colors.accent)
                .foregroundStyle(.black)
                .clipShape(RoundedRectangle(cornerRadius: 10))
            }
            .buttonStyle(.plain)
            .disabled(isSubmitting)
        }
        .padding(.top, 2)
    }

    // MARK: - Logic

    private var sportsLabel: String {
        "no " + proposal.issue.affectedSports.joined(separator: " / ")
    }

    private var severityColor: Color {
        switch severity {
        case 1...3: return DS.Colors.success
        case 4...6: return DS.Colors.warning
        default: return DS.Colors.danger
        }
    }

    private func seedDefaultChoices() {
        guard choices.isEmpty else { return }
        for day in proposal.affectedDays {
            choices[day.day] = day.recommendedOption
        }
    }

    /// A day still inside the (possibly shortened) window.
    private func isInWindow(_ day: AffectedDay) -> Bool {
        guard let index = proposal.affectedDays.firstIndex(where: { $0.day == day.day }) else { return true }
        return index < durationDays
    }

    private func binding(for day: AffectedDay) -> Binding<String> {
        Binding(
            get: { choices[day.day] ?? day.recommendedOption },
            set: { choices[day.day] = $0 }
        )
    }

    private func confirm() {
        isSubmitting = true
        var issue = proposal.issue
        issue.severity = severity
        issue.durationDays = durationDays

        // Days trimmed out by shortening the window are simply not sent. The
        // backend still enforces the injury across its own window, so an
        // omission can never leave a forbidden session standing.
        let submitted = proposal.affectedDays
            .filter { isInWindow($0) }
            .reduce(into: [String: String]()) { result, day in
                result[day.day] = choices[day.day] ?? day.recommendedOption
            }

        onConfirm(issue, submitted)
    }
}
