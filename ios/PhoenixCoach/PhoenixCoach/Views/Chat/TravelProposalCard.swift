import SwiftUI

/// Confirmation card for travel days — the logistics sibling of the injury and
/// recovery cards.
///
/// The backend mapped the athlete's message ("traveling Friday and Saturday")
/// to dates in the remaining week and read what those days currently hold.
/// Nothing is written until Confirm: only the athlete knows whether a trip
/// really rules out every session or they'd rather keep the plan and wing it.
struct TravelProposalCard: View {
    let proposal: TravelProposal
    /// The parent owns the network call so the card stays presentational.
    let onConfirm: () -> Void
    let onDismiss: () -> Void

    @State private var isSubmitting = false

    private var hasDisplacedWork: Bool {
        proposal.affectedDays.contains { !$0.workouts.isEmpty }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            header
            Divider().overlay(DS.Colors.outline.opacity(0.3))
            summary

            if hasDisplacedWork {
                displacedList
            }

            actions
        }
        .padding(16)
        .background(DS.Colors.surface)
        .clipShape(RoundedRectangle(cornerRadius: 16))
        .overlay(
            RoundedRectangle(cornerRadius: 16)
                .stroke(DS.Colors.accent.opacity(0.4), lineWidth: 1)
        )
    }

    // MARK: - Sections

    private var header: some View {
        HStack(spacing: 8) {
            Image(systemName: "airplane")
                .foregroundStyle(DS.Colors.accent)
            Text("Traveling \(proposal.days.joined(separator: ", "))?")
                .font(.system(size: 15, weight: .bold))
                .foregroundStyle(DS.Colors.primaryText)
            Spacer()
        }
    }

    private var summary: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(proposal.rebuildDays.isEmpty
                 ? "Marks those days as rest. Nothing else is left in the week to move."
                 : "Marks those days as rest and rebuilds \(proposal.rebuildDays.joined(separator: ", ")) around the trip.")
                .font(.system(size: 13, weight: .semibold))
                .foregroundStyle(DS.Colors.primaryText)
                .fixedSize(horizontal: false, vertical: true)

            Text(proposal.priorityNote)
                .font(.system(size: 12))
                .foregroundStyle(DS.Colors.onSurface)
                .fixedSize(horizontal: false, vertical: true)
        }
    }

    private var displacedList: some View {
        VStack(alignment: .leading, spacing: 6) {
            ForEach(proposal.affectedDays.filter { !$0.workouts.isEmpty }, id: \.day) { affected in
                HStack(alignment: .top, spacing: 6) {
                    Text(affected.day)
                        .font(.system(size: 12, weight: .semibold))
                        .foregroundStyle(DS.Colors.primaryText)
                    Text(affected.workouts.joined(separator: ", "))
                        .font(.system(size: 12))
                        .foregroundStyle(DS.Colors.outline)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
        }
        .padding(10)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(DS.Colors.background)
        .clipShape(RoundedRectangle(cornerRadius: 10))
    }

    private var actions: some View {
        HStack(spacing: 10) {
            Button(action: onDismiss) {
                Text("Keep plan")
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
                onConfirm()
            } label: {
                HStack(spacing: 6) {
                    if isSubmitting {
                        ProgressView().controlSize(.small).tint(.black)
                    }
                    Text(isSubmitting ? "Rebuilding..." : "Confirm")
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
