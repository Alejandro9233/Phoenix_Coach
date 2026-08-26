import SwiftUI

/// Deterministic race-day pacing — per-5K splits from the stored target with
/// a conservative first 5K, waypoint times, and HR caps from the watch's LTHR
/// zones. All numbers are Python (pace_model.race_pacing); this sheet only
/// renders. A 3:10 attempt is mostly lost by going out at sub-3 pace — the
/// first row exists to stop that.
struct RacePlanSheet: View {
    let race: RaceStatus
    let pacing: RacePacing
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            ZStack {
                DS.Colors.background.ignoresSafeArea()
                ScrollView {
                    VStack(alignment: .leading, spacing: 20) {
                        headerCard
                        splitsCard
                        waypointsCard
                        if let caps = pacing.hrCaps {
                            hrCapsCard(caps)
                        }
                    }
                    .padding(16)
                }
            }
            .navigationTitle("Race Plan")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button { dismiss() } label: {
                        Image(systemName: "xmark")
                            .font(.system(size: 13, weight: .semibold))
                            .foregroundStyle(DS.Colors.outline)
                    }
                }
            }
        }
        .presentationDetents([.fraction(0.88), .large])
        .presentationDragIndicator(.visible)
    }

    private func header(_ title: String) -> some View {
        Text(title)
            .font(.system(size: 10, weight: .bold))
            .tracking(1.5)
            .foregroundStyle(DS.Colors.outline)
    }

    private var headerCard: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(race.raceName ?? race.raceDistance ?? "Race")
                .font(.system(size: 22, weight: .light))
                .foregroundStyle(.white)
            HStack(spacing: 20) {
                statColumn("TARGET", pacing.target ?? "—")
                statColumn("FIRST 5K", pacing.first5kPace ?? "—")
                statColumn("CRUISE", pacing.cruisePace ?? "—")
            }
            Text("Go out easy — the first 5K is deliberately slower; the cruise pace earns it back.")
                .font(.system(size: 12, weight: .light))
                .foregroundStyle(DS.Colors.onSurface)
                .fixedSize(horizontal: false, vertical: true)
        }
        .padding(16)
        .frame(maxWidth: .infinity, alignment: .leading)
        .glassCard()
    }

    private func statColumn(_ label: String, _ value: String) -> some View {
        VStack(alignment: .leading, spacing: 3) {
            Text(label)
                .font(.system(size: 9, weight: .bold))
                .tracking(1.2)
                .foregroundStyle(DS.Colors.outline)
            Text(value)
                .font(.system(size: 17, weight: .light))
                .foregroundStyle(.white)
        }
    }

    private var splitsCard: some View {
        VStack(alignment: .leading, spacing: 10) {
            header("SPLITS")
            HStack {
                Text("KM").frame(width: 44, alignment: .leading)
                Text("PACE").frame(maxWidth: .infinity, alignment: .leading)
                Text("SPLIT").frame(width: 60, alignment: .trailing)
                Text("TOTAL").frame(width: 70, alignment: .trailing)
            }
            .font(.system(size: 9, weight: .bold))
            .tracking(1.0)
            .foregroundStyle(DS.Colors.outline)
            ForEach(pacing.splits ?? []) { split in
                HStack {
                    Text(split.toKm.map { String(format: "%g", $0) } ?? "—")
                        .frame(width: 44, alignment: .leading)
                        .foregroundStyle(.white)
                    Text(split.pace ?? "—")
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .foregroundStyle(DS.Colors.onSurface)
                    Text(split.split ?? "—")
                        .frame(width: 60, alignment: .trailing)
                        .foregroundStyle(DS.Colors.onSurface)
                    Text(split.cumulative ?? "—")
                        .frame(width: 70, alignment: .trailing)
                        .foregroundStyle(.white)
                }
                .font(.system(size: 14, weight: .light))
            }
        }
        .padding(16)
        .frame(maxWidth: .infinity, alignment: .leading)
        .glassCard()
    }

    private var waypointsCard: some View {
        VStack(alignment: .leading, spacing: 10) {
            header("WAYPOINTS")
            HStack(spacing: 0) {
                ForEach(pacing.waypoints ?? []) { waypoint in
                    VStack(spacing: 4) {
                        Text((waypoint.label ?? "").uppercased())
                            .font(.system(size: 9, weight: .bold))
                            .tracking(1.2)
                            .foregroundStyle(DS.Colors.outline)
                        Text(waypoint.time ?? "—")
                            .font(.system(size: 20, weight: .ultraLight))
                            .foregroundStyle(.white)
                    }
                    .frame(maxWidth: .infinity)
                }
            }
        }
        .padding(16)
        .frame(maxWidth: .infinity, alignment: .leading)
        .glassCard()
    }

    private func hrCapsCard(_ caps: RaceHrCaps) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            header("HR CAPS · FROM WATCH LTHR ZONES")
            capRow("To 10K", caps.first10k.map { "under \($0) bpm" } ?? "—",
                   note: "settle in, spend nothing")
            capRow("To 30K", caps.to30k.map { "under \($0) bpm" } ?? "—",
                   note: "marathon effort")
            capRow("Final stretch", "no cap", note: "race it home")
        }
        .padding(16)
        .frame(maxWidth: .infinity, alignment: .leading)
        .glassCard()
    }

    private func capRow(_ label: String, _ value: String, note: String) -> some View {
        HStack {
            Text(label)
                .font(.system(size: 14, weight: .light))
                .foregroundStyle(.white)
            Spacer()
            VStack(alignment: .trailing, spacing: 2) {
                Text(value)
                    .font(.system(size: 14, weight: .medium))
                    .foregroundStyle(DS.Colors.accent)
                Text(note)
                    .font(.system(size: 10, weight: .light))
                    .foregroundStyle(DS.Colors.outline)
            }
        }
    }
}
