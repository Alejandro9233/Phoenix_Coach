import SwiftUI

// MARK: - Refresh debrief sheet
//
// Everything one refresh found and did, frozen at sync time. Shared by the
// History feed and the Today debrief card — same HistoryEvent either way.

struct RefreshDebriefSheet: View {
    let event: HistoryEvent
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            ZStack {
                DS.Colors.background.ignoresSafeArea()
                ScrollView {
                    VStack(alignment: .leading, spacing: 20) {
                        syncSection
                        if let activities = event.newActivities, !activities.isEmpty {
                            activitiesSection(activities)
                        }
                        recoverySection
                        if let triggers = event.triggers, !triggers.isEmpty {
                            triggersSection(triggers)
                        }
                        adaptationSection
                        if let week = event.weekAfter {
                            weekAfterSection(week)
                        }
                        discussButton
                    }
                    .padding(16)
                }
            }
            .navigationTitle("Sync Debrief")
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

    private var syncSection: some View {
        VStack(alignment: .leading, spacing: 10) {
            header("SYNC")
            HStack {
                StatusCapsule(
                    label: event.syncStatus == "ok" ? "SYNCED" : "PARTIAL",
                    color: event.syncStatus == "ok" ? DS.Colors.success : DS.Colors.warning
                )
                Spacer()
                if let time = HistoryFormat.time(event.at) {
                    Text(time)
                        .font(.system(size: 13, weight: .light))
                        .foregroundStyle(DS.Colors.onSurface)
                }
            }
            if let message = event.syncMessage {
                Text(message)
                    .font(.system(size: 13, weight: .light))
                    .foregroundStyle(event.syncStatus == "ok" ? DS.Colors.onSurface : DS.Colors.warning)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
        .padding(16)
        .frame(maxWidth: .infinity, alignment: .leading)
        .glassCard()
    }

    private func activitiesSection(_ activities: [HistoryActivity]) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            header("NEW ACTIVITIES · AT SYNC TIME")
            ForEach(activities) { activity in
                VStack(alignment: .leading, spacing: 6) {
                    HStack {
                        Image(systemName: sportIcon(for: activity.sport ?? ""))
                            .font(.system(size: 13))
                            .foregroundStyle(DS.Colors.onSurface)
                        Text(activity.compliance?.workoutTitle ?? (activity.sport ?? "Activity").capitalized)
                            .font(.system(size: 15, weight: .medium))
                            .foregroundStyle(.white)
                        Spacer()
                        if let score = activity.compliance?.score {
                            StatusCapsule(
                                label: "\(score)%",
                                color: score >= 80 ? DS.Colors.success
                                    : score >= 50 ? DS.Colors.warning : DS.Colors.danger
                            )
                        }
                    }
                    HStack(spacing: 12) {
                        if let km = activity.distanceKm, km > 0 {
                            Text(String(format: "%.1f km", km))
                        }
                        if let minutes = activity.durationMin {
                            Text("\(minutes) min")
                        }
                        if let hr = activity.avgHr {
                            Text("\(hr) bpm")
                        }
                    }
                    .font(.system(size: 13, weight: .light))
                    .foregroundStyle(DS.Colors.onSurface)
                    if let notes = activity.compliance?.notes {
                        Text(notes)
                            .font(.system(size: 12, weight: .light))
                            .foregroundStyle(DS.Colors.outline)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                }
                .padding(12)
                .frame(maxWidth: .infinity, alignment: .leading)
                .background(Color.white.opacity(0.03))
                .clipShape(RoundedRectangle(cornerRadius: 12))
            }
        }
        .padding(16)
        .frame(maxWidth: .infinity, alignment: .leading)
        .glassCard()
    }

    private var recoverySection: some View {
        VStack(alignment: .leading, spacing: 10) {
            header("RECOVERY")
            if event.recoveryStale == true {
                HStack(spacing: 8) {
                    StatusCapsule(label: "STALE", color: DS.Colors.warning)
                    Text(event.staleReason ?? "Recovery data was not from today.")
                        .font(.system(size: 12, weight: .light))
                        .foregroundStyle(DS.Colors.warning)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
            HStack(spacing: 0) {
                recoveryTile("HRV", value: event.recovery?.hrvMs.map { "\(Int($0))" },
                             delta: event.recoveryDelta?.hrvMs)
                recoveryTile("RHR", value: event.recovery?.restingHr.map { "\($0)" },
                             delta: event.recoveryDelta?.restingHr, invertGood: true)
                recoveryTile("FORM", value: event.recovery?.tib.map { String(format: "%.0f", $0) },
                             delta: event.recoveryDelta?.tib)
            }
        }
        .padding(16)
        .frame(maxWidth: .infinity, alignment: .leading)
        .glassCard()
    }

    private func recoveryTile(_ label: String, value: String?, delta: Double?,
                              invertGood: Bool = false) -> some View {
        VStack(spacing: 4) {
            Text(label)
                .font(.system(size: 9, weight: .bold))
                .tracking(1.2)
                .foregroundStyle(DS.Colors.outline)
            Text(value ?? "—")
                .font(.system(size: 26, weight: .ultraLight))
                .foregroundStyle(.white)
            if let delta = delta, delta != 0, event.recoveryStale != true {
                let improving = invertGood ? delta < 0 : delta > 0
                Text(String(format: "%+.0f", delta))
                    .font(.system(size: 11, weight: .medium))
                    .foregroundStyle(improving ? DS.Colors.success : DS.Colors.warning)
            } else {
                Text(" ").font(.system(size: 11))
            }
        }
        .frame(maxWidth: .infinity)
    }

    private func triggersSection(_ triggers: [RefreshTrigger]) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            header("ADAPTATION TRIGGERS")
            ForEach(triggers) { trigger in
                HStack {
                    Image(systemName: trigger.fired == true
                          ? "exclamationmark.triangle.fill" : "checkmark.circle")
                        .font(.system(size: 12))
                        .foregroundStyle(trigger.fired == true ? DS.Colors.warning : DS.Colors.outline)
                    Text(trigger.label)
                        .font(.system(size: 13, weight: .light))
                        .foregroundStyle(trigger.fired == true ? .white : DS.Colors.onSurface)
                    Spacer()
                    Text(triggerDetail(trigger))
                        .font(.system(size: 12, weight: .light))
                        .foregroundStyle(DS.Colors.outline)
                }
            }
            Text("Fired triggers adapt today's workout automatically.")
                .font(.system(size: 11, weight: .light))
                .foregroundStyle(DS.Colors.outline)
        }
        .padding(16)
        .frame(maxWidth: .infinity, alignment: .leading)
        .glassCard()
    }

    private func triggerDetail(_ trigger: RefreshTrigger) -> String {
        guard let value = trigger.value else { return "no data" }
        let v = String(format: "%g", value)
        guard let threshold = trigger.threshold else { return v }
        return "\(v) / limit \(String(format: "%g", threshold))"
    }

    private var adaptationSection: some View {
        VStack(alignment: .leading, spacing: 10) {
            header("PLAN")
            if let error = event.adaptation?.error {
                HStack(spacing: 8) {
                    StatusCapsule(label: "ADAPT FAILED", color: DS.Colors.danger)
                }
                Text(error)
                    .font(.system(size: 12, weight: .light))
                    .foregroundStyle(DS.Colors.danger)
                    .fixedSize(horizontal: false, vertical: true)
            } else if event.adaptation?.adapted == true {
                StatusCapsule(label: "ADAPTED", color: DS.Colors.warning)
                ForEach(event.adaptation?.reasons ?? [], id: \.self) { reason in
                    Text("· \(reason)")
                        .font(.system(size: 13, weight: .light))
                        .foregroundStyle(DS.Colors.onSurface)
                }
                if let receipt = event.adaptation?.receipt {
                    receiptMiniDiff(receipt)
                }
            } else {
                Text(event.recoveryStale == true
                     ? "Adaptation skipped — recovery data was stale."
                     : "No changes — every trigger was clear.")
                    .font(.system(size: 13, weight: .light))
                    .foregroundStyle(DS.Colors.onSurface)
            }
        }
        .padding(16)
        .frame(maxWidth: .infinity, alignment: .leading)
        .glassCard()
    }

    private func receiptMiniDiff(_ receipt: PlanReceipt) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            ForEach(receipt.days ?? [], id: \.self) { day in
                VStack(alignment: .leading, spacing: 4) {
                    Text(day.uppercased())
                        .font(.system(size: 9, weight: .bold))
                        .tracking(1.2)
                        .foregroundStyle(DS.Colors.outline)
                    DiffColumn(title: "BEFORE", workouts: receipt.before?[day] ?? [], dimmed: true)
                    DiffColumn(title: "AFTER", workouts: receipt.after?[day] ?? [], dimmed: false)
                }
            }
        }
        .padding(12)
        .background(Color.white.opacity(0.03))
        .clipShape(RoundedRectangle(cornerRadius: 12))
    }

    private func weekAfterSection(_ week: WeekAfter) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            header("WEEK AFTER THIS SYNC")
            HStack {
                if let done = week.runKmDone, let target = week.runKmTarget, target > 0 {
                    VStack(alignment: .leading, spacing: 6) {
                        Text(String(format: "%.1f of %.0f km", done, target))
                            .font(.system(size: 15, weight: .light))
                            .foregroundStyle(.white)
                        RunProgressBar(done: done, target: target)
                    }
                }
                Spacer()
                if let completed = week.sessionsCompleted, let planned = week.sessionsPlanned {
                    Text("\(completed)/\(planned) sessions")
                        .font(.system(size: 13, weight: .light))
                        .foregroundStyle(DS.Colors.onSurface)
                }
            }
        }
        .padding(16)
        .frame(maxWidth: .infinity, alignment: .leading)
        .glassCard()
    }

    private var discussButton: some View {
        Button {
            ChatPrefill.pending = "Review my latest sync — what should I take from it?"
            dismiss()
            NotificationCenter.default.post(name: NSNotification.Name("OpenCoachChat"), object: nil)
        } label: {
            HStack {
                Image(systemName: "message.fill")
                    .font(.system(size: 13))
                Text("DISCUSS WITH COACH")
                    .font(.system(size: 12, weight: .bold))
                    .tracking(1.5)
            }
            .foregroundStyle(.black)
            .frame(maxWidth: .infinity)
            .padding(.vertical, 14)
            .background(DS.Colors.accent)
            .clipShape(RoundedRectangle(cornerRadius: 12))
        }
        .buttonStyle(.plain)
    }
}

/// Single-series meter: white fill on a recessive track, value carried by the
/// adjacent text (never text in series color on the mark).
struct RunProgressBar: View {
    let done: Double
    let target: Double

    var body: some View {
        GeometryReader { geo in
            ZStack(alignment: .leading) {
                Capsule()
                    .fill(Color.white.opacity(0.10))
                Capsule()
                    .fill(DS.Colors.accent)
                    .frame(width: max(6, geo.size.width * min(1.0, done / target)))
            }
        }
        .frame(height: 5)
    }
}

// MARK: - Plan diff sheet

struct PlanDiffSheet: View {
    let event: HistoryEvent
    @Environment(\.dismiss) private var dismiss
    @State private var selectedDay: String?

    private var days: [String] { event.days ?? [] }

    var body: some View {
        NavigationStack {
            ZStack {
                DS.Colors.background.ignoresSafeArea()
                ScrollView {
                    VStack(alignment: .leading, spacing: 20) {
                        headerSection
                        if days.count > 1 { dayPicker }
                        if let day = selectedDay ?? days.first {
                            dayDiff(day)
                        }
                        if let stripped = event.stripped, !stripped.isEmpty {
                            strippedSection(stripped)
                        }
                        if event.afterSource != nil && event.afterSource != "receipt" {
                            Text(event.afterSource == "reconstructed"
                                 ? "The \"after\" state was reconstructed from later plan changes."
                                 : "The \"after\" state shows the current plan — this change predates exact snapshots.")
                                .font(.system(size: 11, weight: .light))
                                .foregroundStyle(DS.Colors.outline)
                        }
                    }
                    .padding(16)
                }
            }
            .navigationTitle("Plan Change")
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

    private var headerSection: some View {
        let badge = HistoryFormat.sourceBadge(event.source)
        return VStack(alignment: .leading, spacing: 8) {
            HStack {
                StatusCapsule(label: badge.label, color: badge.color)
                Spacer()
                if let time = HistoryFormat.time(event.at) {
                    Text(time)
                        .font(.system(size: 12, weight: .light))
                        .foregroundStyle(DS.Colors.outline)
                }
            }
            Text(event.reason ?? "Plan updated")
                .font(.system(size: 17, weight: .light))
                .foregroundStyle(.white)
                .fixedSize(horizontal: false, vertical: true)
        }
        .padding(16)
        .frame(maxWidth: .infinity, alignment: .leading)
        .glassCard()
    }

    private var dayPicker: some View {
        ScrollView(.horizontal, showsIndicators: false) {
            HStack(spacing: 8) {
                ForEach(days, id: \.self) { day in
                    let isSelected = (selectedDay ?? days.first) == day
                    Button { selectedDay = day } label: {
                        Text(day.prefix(3).uppercased())
                            .font(.system(size: 11, weight: .bold))
                            .tracking(1.2)
                            .foregroundStyle(isSelected ? .black : DS.Colors.onSurface)
                            .padding(.horizontal, 14)
                            .padding(.vertical, 7)
                            .background(isSelected ? DS.Colors.accent : Color.white.opacity(0.06))
                            .clipShape(Capsule())
                    }
                    .buttonStyle(.plain)
                }
            }
        }
    }

    private func dayDiff(_ day: String) -> some View {
        VStack(alignment: .leading, spacing: 12) {
            DiffCard(title: "BEFORE", workouts: event.before?[day] ?? [], accent: false)
            DiffCard(title: "AFTER", workouts: event.after?[day] ?? [], accent: true)
        }
    }

    private func strippedSection(_ stripped: [StrippedWorkout]) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("STRIPPED BY ENFORCEMENT")
                .font(.system(size: 10, weight: .bold))
                .tracking(1.5)
                .foregroundStyle(DS.Colors.danger)
            ForEach(stripped) { item in
                VStack(alignment: .leading, spacing: 2) {
                    Text("\(item.day ?? "") — \(item.title ?? "")")
                        .font(.system(size: 13, weight: .light))
                        .strikethrough()
                        .foregroundStyle(DS.Colors.danger)
                    if let reason = item.reason {
                        Text(reason)
                            .font(.system(size: 11, weight: .light))
                            .foregroundStyle(DS.Colors.outline)
                    }
                }
            }
        }
        .padding(16)
        .frame(maxWidth: .infinity, alignment: .leading)
        .glassCard()
    }
}

// MARK: - Shared diff pieces

struct DiffCard: View {
    let title: String
    let workouts: [ReceiptWorkout]
    let accent: Bool

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack {
                Text(title)
                    .font(.system(size: 10, weight: .bold))
                    .tracking(1.5)
                    .foregroundStyle(accent ? DS.Colors.accent : DS.Colors.outline)
                if accent {
                    StatusCapsule(label: "ACTIVE", color: DS.Colors.accent)
                }
            }
            if workouts.isEmpty {
                Text("Rest day")
                    .font(.system(size: 13, weight: .light))
                    .foregroundStyle(DS.Colors.outline)
            } else {
                ForEach(workouts) { workout in
                    HStack {
                        Image(systemName: sportIcon(for: workout.sport ?? ""))
                            .font(.system(size: 12))
                            .foregroundStyle(DS.Colors.onSurface)
                            .frame(width: 18)
                        Text(workout.title ?? "—")
                            .font(.system(size: 14, weight: accent ? .medium : .light))
                            .foregroundStyle(accent ? .white : DS.Colors.onSurface)
                        Spacer()
                        if let time = workout.totalTime {
                            Text(time)
                                .font(.system(size: 12, weight: .light))
                                .foregroundStyle(DS.Colors.outline)
                        }
                    }
                }
            }
        }
        .padding(14)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Color.white.opacity(accent ? 0.05 : 0.02))
        .clipShape(RoundedRectangle(cornerRadius: 12))
        .overlay(
            RoundedRectangle(cornerRadius: 12)
                .stroke(accent ? Color.white.opacity(0.25) : Color.white.opacity(0.08), lineWidth: 1)
        )
    }
}

struct DiffColumn: View {
    let title: String
    let workouts: [ReceiptWorkout]
    let dimmed: Bool

    var body: some View {
        HStack(alignment: .top, spacing: 8) {
            Text(title)
                .font(.system(size: 9, weight: .bold))
                .tracking(1.0)
                .foregroundStyle(DS.Colors.outline)
                .frame(width: 48, alignment: .leading)
            VStack(alignment: .leading, spacing: 2) {
                if workouts.isEmpty {
                    Text("Rest")
                        .font(.system(size: 12, weight: .light))
                        .foregroundStyle(DS.Colors.outline)
                } else {
                    ForEach(workouts) { workout in
                        Text("\(workout.title ?? "—")\(workout.totalTime.map { " · \($0)" } ?? "")")
                            .font(.system(size: 12, weight: .light))
                            .foregroundStyle(dimmed ? DS.Colors.outline : .white)
                    }
                }
            }
        }
    }
}
