import SwiftUI

/// The History tab — a newest-first ledger of what the system did: every
/// refresh (what synced, what the body said, why the plan did or didn't
/// change) and every plan rewrite (who touched it, what changed, what was
/// stripped). Answers "idk what happened after i refresh" durably; the Today
/// debrief card shows the same event in the moment.
struct HistoryView: View {
    @State private var events: [HistoryEvent] = []
    @State private var nextBefore: String?
    @State private var isLoading = false
    @State private var isLoadingMore = false
    @State private var errorMessage: String?
    @State private var selectedEvent: HistoryEvent?

    var body: some View {
        NavigationStack {
            ZStack {
                DS.Colors.background.ignoresSafeArea()
                RadialGradient(
                    gradient: Gradient(colors: [DS.Colors.accent.opacity(0.12), .clear]),
                    center: .top, startRadius: 0, endRadius: 400
                )
                .ignoresSafeArea()

                ScrollView {
                    VStack(alignment: .leading, spacing: 24) {
                        VStack(alignment: .leading, spacing: 6) {
                            Text("History")
                                .font(.system(size: 30, weight: .light))
                                .foregroundStyle(.white)
                                .tracking(-0.5)
                            Text("Every sync and every plan change, newest first.")
                                .font(.system(size: 15, weight: .light))
                                .foregroundStyle(DS.Colors.onSurface)
                        }
                        .padding(.top, 8)

                        if isLoading && events.isEmpty {
                            ProgressView()
                                .tint(DS.Colors.accent)
                                .frame(maxWidth: .infinity)
                                .padding(60)
                        } else if let error = errorMessage, events.isEmpty {
                            emptyState(
                                icon: "clock.badge.exclamationmark",
                                title: "History unavailable",
                                message: error
                            )
                        } else if events.isEmpty {
                            emptyState(
                                icon: "list.bullet.rectangle",
                                title: "Nothing logged yet",
                                message: "Refresh on the Today tab — every sync and plan change lands here."
                            )
                        } else {
                            feedSections
                            loadOlderFooter
                        }
                    }
                    .padding(.horizontal, 16)
                    .padding(.bottom, 24)
                }
                .refreshable { await load(reset: true) }
            }
        }
        .task { if events.isEmpty { await load(reset: true) } }
        .sheet(item: $selectedEvent) { event in
            if event.type == "refresh" {
                RefreshDebriefSheet(event: event)
            } else {
                PlanDiffSheet(event: event)
            }
        }
    }

    // MARK: - Sections grouped by the frozen local day

    private var groupedByDay: [(day: String, events: [HistoryEvent])] {
        var order: [String] = []
        var buckets: [String: [HistoryEvent]] = [:]
        for event in events {
            let day = event.localDay ?? String((event.at ?? "").prefix(10))
            if buckets[day] == nil { order.append(day) }
            buckets[day, default: []].append(event)
        }
        return order.map { ($0, buckets[$0] ?? []) }
    }

    private var feedSections: some View {
        LazyVStack(alignment: .leading, spacing: 14) {
            ForEach(groupedByDay, id: \.day) { group in
                Text(HistoryFormat.dayLabel(group.day))
                    .font(.system(size: 10, weight: .bold))
                    .tracking(1.5)
                    .foregroundStyle(DS.Colors.outline)
                    .padding(.top, 6)
                ForEach(group.events) { event in
                    Button { selectedEvent = event } label: {
                        if event.type == "refresh" {
                            RefreshEventRow(event: event)
                        } else {
                            PlanChangeRow(event: event)
                        }
                    }
                    .buttonStyle(.plain)
                }
            }
        }
    }

    private var loadOlderFooter: some View {
        Group {
            if nextBefore != nil {
                Button {
                    Task { await loadMore() }
                } label: {
                    HStack(spacing: 6) {
                        if isLoadingMore {
                            ProgressView().tint(DS.Colors.accent)
                        } else {
                            Image(systemName: "arrow.down")
                                .font(.system(size: 11, weight: .semibold))
                        }
                        Text("LOAD OLDER")
                            .font(.system(size: 11, weight: .bold))
                            .tracking(1.5)
                    }
                    .foregroundStyle(DS.Colors.outline)
                    .frame(maxWidth: .infinity)
                    .padding(.vertical, 14)
                }
                .buttonStyle(.plain)
            }
        }
    }

    private func emptyState(icon: String, title: String, message: String) -> some View {
        VStack(spacing: 12) {
            Image(systemName: icon)
                .font(.system(size: 32, weight: .light))
                .foregroundStyle(DS.Colors.outline)
            Text(title)
                .font(.system(size: 17, weight: .medium))
                .foregroundStyle(.white)
            Text(message)
                .font(.system(size: 13, weight: .light))
                .foregroundStyle(DS.Colors.onSurface)
                .multilineTextAlignment(.center)
        }
        .frame(maxWidth: .infinity)
        .padding(40)
        .glassCard()
    }

    // MARK: - Loading

    private func load(reset: Bool) async {
        isLoading = true
        errorMessage = nil
        do {
            let page = try await NetworkManager.shared.fetchHistory()
            await MainActor.run {
                events = page.events
                nextBefore = page.nextBefore
                isLoading = false
            }
        } catch {
            await MainActor.run {
                errorMessage = "Couldn't load the feed. Pull to retry."
                isLoading = false
            }
        }
    }

    private func loadMore() async {
        guard let cursor = nextBefore, !isLoadingMore else { return }
        isLoadingMore = true
        do {
            let page = try await NetworkManager.shared.fetchHistory(before: cursor)
            await MainActor.run {
                let known = Set(events.map(\.id))
                events.append(contentsOf: page.events.filter { !known.contains($0.id) })
                nextBefore = page.nextBefore
                isLoadingMore = false
            }
        } catch {
            await MainActor.run { isLoadingMore = false }
        }
    }
}

// MARK: - Shared formatting

enum HistoryFormat {
    static func dayLabel(_ isoDay: String) -> String {
        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.dateFormat = "yyyy-MM-dd"
        guard let date = formatter.date(from: isoDay) else { return isoDay }
        if Calendar.current.isDateInToday(date) { return "TODAY" }
        if Calendar.current.isDateInYesterday(date) { return "YESTERDAY" }
        let out = DateFormatter()
        out.locale = Locale(identifier: "en_US_POSIX")
        out.dateFormat = "EEE MMM d"
        return out.string(from: date).uppercased()
    }

    static func time(_ isoAt: String?) -> String? {
        guard let isoAt = isoAt else { return nil }
        let parser = ISO8601DateFormatter()
        guard let date = parser.date(from: isoAt) else { return nil }
        let out = DateFormatter()
        out.timeStyle = .short
        return out.string(from: date)
    }

    static func sourceBadge(_ source: String?) -> (label: String, color: Color) {
        switch source {
        case "generate": return ("GENERATED", DS.Colors.accent)
        case "regenerate": return ("REGENERATED", DS.Colors.accent)
        case "replan_remaining": return ("REPLANNED", DS.Colors.accent)
        case "adapt_today": return ("ADAPTED", DS.Colors.warning)
        case "profile_reenforce": return ("PROFILE", DS.Colors.outline)
        case "apply_issue": return ("INJURY", DS.Colors.danger)
        case "apply_recovery": return ("RECOVERY", DS.Colors.success)
        case "apply_travel": return ("TRAVEL", DS.Colors.outline)
        default: return ((source ?? "PLAN").uppercased(), DS.Colors.outline)
        }
    }

    static func refreshHeadline(_ event: HistoryEvent) -> String {
        let count = event.newActivityCount ?? 0
        var headline: String
        if count == 0 {
            headline = "Nothing new"
        } else if count == 1, let sport = event.newActivities?.first?.sport {
            let word = ["running": "run", "cycling": "ride", "swimming": "swim"][sport] ?? "activity"
            headline = "1 new \(word)"
        } else {
            headline = "\(count) new activities"
        }
        if event.adaptation?.adapted == true {
            headline += " · plan adapted"
        } else if event.adaptation?.error != nil {
            headline += " · adapt failed"
        }
        return headline
    }

    static func refreshStatLine(_ event: HistoryEvent) -> String? {
        var parts: [String] = []
        let km = (event.newActivities ?? []).compactMap(\.distanceKm).reduce(0, +)
        if km > 0 { parts.append(String(format: "%.1f KM", km)) }
        if let hrv = event.recovery?.hrvMs {
            var s = "HRV \(Int(hrv))"
            if let d = event.recoveryDelta?.hrvMs, d != 0 {
                s += String(format: " (%+.0f)", d)
            }
            parts.append(s)
        }
        if let rhr = event.recovery?.restingHr { parts.append("RHR \(rhr)") }
        return parts.isEmpty ? nil : parts.joined(separator: " · ")
    }
}

struct StatusCapsule: View {
    let label: String
    let color: Color

    var body: some View {
        Text(label)
            .font(.system(size: 9, weight: .bold))
            .tracking(1.0)
            .foregroundStyle(.black)
            .padding(.horizontal, 8)
            .padding(.vertical, 3)
            .background(color)
            .clipShape(Capsule())
    }
}

// MARK: - Feed rows

struct RefreshEventRow: View {
    let event: HistoryEvent

    private var isQuiet: Bool {
        (event.newActivityCount ?? 0) == 0
            && event.adaptation?.adapted != true
            && event.syncStatus == "ok"
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack {
                StatusCapsule(
                    label: event.syncStatus == "ok" ? "SYNCED" : "PARTIAL",
                    color: event.syncStatus == "ok" ? DS.Colors.success : DS.Colors.warning
                )
                Text(HistoryFormat.refreshHeadline(event))
                    .font(.system(size: 15, weight: isQuiet ? .light : .medium))
                    .foregroundStyle(isQuiet ? DS.Colors.onSurface : .white)
                Spacer()
                if let time = HistoryFormat.time(event.at) {
                    Text(time)
                        .font(.system(size: 11, weight: .light))
                        .foregroundStyle(DS.Colors.outline)
                }
            }
            if !isQuiet, let stats = HistoryFormat.refreshStatLine(event) {
                Text(stats)
                    .font(.system(size: 13, weight: .light))
                    .foregroundStyle(DS.Colors.onSurface)
                    .tracking(0.5)
            }
            if event.recoveryStale == true, let reason = event.staleReason {
                Text(reason)
                    .font(.system(size: 11, weight: .light))
                    .foregroundStyle(DS.Colors.warning)
                    .lineLimit(2)
            } else if event.syncStatus != "ok", let message = event.syncMessage {
                Text(message)
                    .font(.system(size: 11, weight: .light))
                    .foregroundStyle(DS.Colors.warning)
                    .lineLimit(2)
            }
        }
        .padding(isQuiet ? 12 : 16)
        .frame(maxWidth: .infinity, alignment: .leading)
        .glassCard()
    }
}

struct PlanChangeRow: View {
    let event: HistoryEvent

    var body: some View {
        let badge = HistoryFormat.sourceBadge(event.source)
        VStack(alignment: .leading, spacing: 8) {
            HStack {
                StatusCapsule(label: badge.label, color: badge.color)
                Spacer()
                if let time = HistoryFormat.time(event.at) {
                    Text(time)
                        .font(.system(size: 11, weight: .light))
                        .foregroundStyle(DS.Colors.outline)
                }
            }
            Text(event.reason ?? "Plan updated")
                .font(.system(size: 15, weight: .medium))
                .foregroundStyle(.white)
                .fixedSize(horizontal: false, vertical: true)
            if let days = event.days, !days.isEmpty {
                Text(days.map { $0.prefix(3).uppercased() }.joined(separator: " · "))
                    .font(.system(size: 10, weight: .bold))
                    .tracking(1.2)
                    .foregroundStyle(DS.Colors.outline)
            }
            if let stripped = event.stripped, !stripped.isEmpty {
                Text("\(stripped.count) workout\(stripped.count == 1 ? "" : "s") stripped — \(stripped.first?.reason ?? "")")
                    .font(.system(size: 11, weight: .light))
                    .foregroundStyle(DS.Colors.danger)
                    .lineLimit(2)
            }
        }
        .padding(16)
        .frame(maxWidth: .infinity, alignment: .leading)
        .glassCard()
    }
}
