import SwiftUI

/// Confirmation card for a recovered injury — the mirror of IssueProposalCard.
///
/// The backend matched the athlete's message ("calf is fine now") to a specific
/// active injury and worked out which remaining days are rest *because of it*.
/// Nothing is written until Confirm: "feels better" one morning and actually
/// ready to load the calf again are different claims, and only the athlete can
/// make the second one.
struct RecoveryProposalCard: View {
    let proposal: RecoveryProposal
    /// Called with whether to rebuild the freed days. The parent owns the
    /// network call so the card stays presentational.
    let onConfirm: (Bool) -> Void
    let onDismiss: () -> Void

    @State private var rebuild = true
    @State private var isSubmitting = false

    private var hasRebuildDays: Bool { !proposal.rebuildDays.isEmpty }

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            header
            Divider().overlay(DS.Colors.outline.opacity(0.3))
            summary

            if hasRebuildDays {
                rebuildToggle
            }

            actions
        }
        .padding(16)
        .background(DS.Colors.surface)
        .clipShape(RoundedRectangle(cornerRadius: 16))
        .overlay(
            RoundedRectangle(cornerRadius: 16)
                .stroke(DS.Colors.success.opacity(0.4), lineWidth: 1)
        )
    }

    // MARK: - Sections

    private var header: some View {
        HStack(spacing: 8) {
            Image(systemName: "checkmark.seal.fill")
                .foregroundStyle(DS.Colors.success)
            Text("Recovered?")
                .font(.system(size: 15, weight: .bold))
                .foregroundStyle(DS.Colors.primaryText)
            Spacer()
        }
    }

    private var summary: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack(spacing: 6) {
                Text(proposal.injury.bodyPart)
                    .font(.system(size: 14, weight: .semibold))
                    .foregroundStyle(DS.Colors.primaryText)
                if !proposal.injury.affectedSports.isEmpty {
                    Text("·")
                        .foregroundStyle(DS.Colors.outline)
                    Text("unblocks " + proposal.injury.affectedSports.joined(separator: " / "))
                        .font(.system(size: 13))
                        .foregroundStyle(DS.Colors.outline)
                }
            }

            Text(hasRebuildDays
                 ? "Marks it resolved. \(proposal.rebuildDays.joined(separator: ", ")) are rest only because of this injury."
                 : "Marks it resolved so future planning stops working around it.")
                .font(.system(size: 12))
                .foregroundStyle(DS.Colors.onSurface)
                .fixedSize(horizontal: false, vertical: true)
        }
    }

    private var rebuildToggle: some View {
        Toggle(isOn: $rebuild) {
            VStack(alignment: .leading, spacing: 2) {
                Text("Rebuild \(proposal.rebuildDays.joined(separator: ", "))")
                    .font(.system(size: 13, weight: .semibold))
                    .foregroundStyle(DS.Colors.primaryText)
                Text("The coach fills them with real sessions again, easing back in.")
                    .font(.system(size: 11))
                    .foregroundStyle(DS.Colors.outline)
            }
        }
        .tint(DS.Colors.accent)
        .padding(10)
        .background(DS.Colors.background)
        .clipShape(RoundedRectangle(cornerRadius: 10))
        .disabled(isSubmitting)
    }

    private var actions: some View {
        HStack(spacing: 10) {
            Button(action: onDismiss) {
                Text("Not yet")
                    .font(.system(size: 13, weight: .semibold))
                    .frame(maxWidth: .infinity)
                    .padding(.vertical, 10)
                    .background(DS.Colors.background)
                    .foregroundStyle(DS.Colors.onSurface)
                    .clipShape(RoundedRectangle(cornerRadius: 10))
            }
            .buttonStyle(.plain)
            .disabled(isSubmitting)

            Button {
                isSubmitting = true
                onConfirm(rebuild && hasRebuildDays)
            } label: {
                HStack(spacing: 6) {
                    if isSubmitting {
                        ProgressView().controlSize(.small).tint(.black)
                    }
                    Text(isSubmitting ? "Updating..." : "Confirm")
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
}
