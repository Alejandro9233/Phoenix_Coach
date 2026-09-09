import SwiftUI

/// Injury Log — the athlete's only direct handle on what the plan is allowed
/// to schedule.
///
/// `constraint_enforcer` strips every workout whose sport appears in an Active
/// row's `affected_sports`, so these fields are not a diary: they are the
/// restriction. Until 2026-09-09 a logged injury could only be resolved or
/// deleted, which meant a partial recovery — ankle fine on the bike, not yet
/// for running — had no way to be expressed. The athlete argued with the coach
/// in chat instead, and the coach answered from the stale row.
///
/// Editing exists so the record can track the body. Three things it fixes:
///   - sports are toggles emitting the enforcer's own tokens, not free text
///     ("cycling,running" blocked the bike when "run" was meant)
///   - `expected_recovery_date` is reachable, so a restriction can lift itself
///     or explicitly wait for the athlete
///   - Delete actually deletes; it used to drop the row from the local array
///     only, so it came back on the next fetch still stripping sessions
struct InjuryLogView: View {
    @StateObject private var network = NetworkManager.shared
    @State private var injuries: [Injury] = []
    @State private var isLoading = true
    @State private var errorMessage: String?
    @State private var showAddSheet = false
    @State private var editingInjury: Injury?

    var body: some View {
        ZStack {
            DS.Colors.background.ignoresSafeArea()

            if isLoading {
                loadingState
            } else if let error = errorMessage {
                errorState(error)
            } else {
                content
            }
        }
        .navigationTitle("Injury Log")
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            ToolbarItem(placement: .topBarTrailing) {
                Button {
                    showAddSheet = true
                } label: {
                    Image(systemName: "plus")
                        .font(.system(size: 15, weight: .semibold))
                        .foregroundStyle(DS.Colors.accent)
                        .frame(width: 44, height: 44)
                        .contentShape(Rectangle())
                }
                .accessibilityLabel("Log an injury")
            }
        }
        .sheet(isPresented: $showAddSheet) {
            InjuryFormSheet(editing: nil) {
                Task { await loadInjuries() }
            }
        }
        .sheet(item: $editingInjury) { injury in
            InjuryFormSheet(editing: injury) {
                Task { await loadInjuries() }
            }
        }
        .task {
            await loadInjuries()
        }
    }

    // MARK: - States

    /// Skeleton over the real layout, not a spinner on blank: a cold backend
    /// takes 30-60s to answer and the shape is what makes the wait survivable.
    private var loadingState: some View {
        ScrollView(showsIndicators: false) {
            VStack(spacing: DS.Spacing.l) {
                ForEach(0..<2, id: \.self) { _ in
                    InjuryCard(injury: .placeholder)
                }
            }
            .padding(.horizontal, DS.Spacing.page)
            .padding(.top, DS.Spacing.l)
        }
        .redacted(reason: .placeholder)
        .accessibilityLabel("Loading injuries")
    }

    private func errorState(_ error: String) -> some View {
        VStack(spacing: DS.Spacing.m) {
            Text("Couldn't load injuries")
                .font(.system(size: 15, weight: .semibold))
                .foregroundStyle(.white)
            Text(error)
                .font(.footnote)
                .foregroundStyle(DS.Colors.outline)
                .multilineTextAlignment(.center)
            Button("Retry") {
                Task { await loadInjuries() }
            }
            .font(.system(size: 13, weight: .semibold))
            .foregroundStyle(DS.Colors.onAccent)
            .padding(.horizontal, DS.Spacing.xl)
            .frame(minHeight: 44)
            .background(DS.Colors.accent, in: Capsule())
        }
        .padding(.horizontal, DS.Spacing.page)
    }

    private var content: some View {
        List {
            if injuries.isEmpty {
                ContentUnavailableView(
                    "No Injury History",
                    systemImage: "bandage",
                    description: Text("Log a niggle and the plan stops scheduling around it.")
                )
                .padding(.top, 40)
                .listRowBackground(Color.clear)
                .listRowSeparator(.hidden)
            } else {
                ForEach(injuries) { injury in
                    Button {
                        editingInjury = injury
                    } label: {
                        InjuryCard(injury: injury)
                    }
                    .buttonStyle(.plain)
                    .accessibilityHint("Opens this injury for editing")
                    .listRowInsets(EdgeInsets(top: 0, leading: 0, bottom: DS.Spacing.l, trailing: 0))
                    .listRowBackground(Color.clear)
                    .listRowSeparator(.hidden)
                    .swipeActions(edge: .trailing, allowsFullSwipe: true) {
                        Button(role: .destructive) {
                            Task { await deleteInjury(injury) }
                        } label: {
                            Label("Delete", systemImage: "trash")
                        }
                    }
                    // Recovery used to have no exit here but delete,
                    // which throws away the history the coach reads.
                    .swipeActions(edge: .leading, allowsFullSwipe: true) {
                        // Recovering too: an expired window parks the
                        // injury there until the athlete closes it out.
                        if ["active", "recovering"].contains(injury.status?.lowercased()) {
                            Button {
                                Task { await resolveInjury(injury) }
                            } label: {
                                Label("Resolved", systemImage: "checkmark.seal.fill")
                            }
                            .tint(DS.Colors.success)
                        }
                    }
                }
            }
        }
        .listStyle(.plain)
        .scrollContentBackground(.hidden)
        .padding(.horizontal, DS.Spacing.page)
        .refreshable { await loadInjuries() }
    }

    // MARK: - Actions

    private func loadInjuries() async {
        errorMessage = nil
        do {
            injuries = try await network.fetchInjuries()
            isLoading = false
        } catch {
            errorMessage = error.localizedDescription
            isLoading = false
        }
    }

    /// Mark an active injury resolved, keeping the row — history feeds the
    /// coach's context. This does not rebuild plan days; chat's recovery card
    /// (or Replan) does that.
    private func resolveInjury(_ injury: Injury) async {
        var updated = injury
        updated.status = "Resolved"
        do {
            try await network.updateInjury(updated)
            UINotificationFeedbackGenerator().notificationOccurred(.success)
            await loadInjuries()
        } catch {
            UINotificationFeedbackGenerator().notificationOccurred(.error)
            errorMessage = error.localizedDescription
        }
    }

    private func deleteInjury(_ injury: Injury) async {
        guard let id = injury.id else { return }
        do {
            try await network.deleteInjury(id: id)
            UIImpactFeedbackGenerator(style: .medium).impactOccurred()
            await loadInjuries()
        } catch {
            // The row stays on screen on failure — the old local-only removal
            // made a failed delete look like a success.
            UINotificationFeedbackGenerator().notificationOccurred(.error)
            errorMessage = error.localizedDescription
        }
    }
}

// MARK: - Card

struct InjuryCard: View {
    let injury: Injury

    var body: some View {
        VStack(alignment: .leading, spacing: DS.Spacing.m) {
            HStack(alignment: .center, spacing: DS.Spacing.s) {
                Text(injury.bodyPart ?? "Unknown")
                    .textCase(.uppercase)
                    .font(.system(size: 13, weight: .bold))
                    .tracking(DS.Tracking.normal)
                    .foregroundStyle(.white)
                Spacer(minLength: DS.Spacing.s)
                statusBadge(status: injury.status ?? "Unknown")
            }

            if let severity = injury.severity {
                HStack(spacing: DS.Spacing.xs) {
                    Text("Severity")
                        .font(.system(size: 11))
                        .foregroundStyle(DS.Colors.outline)
                    Text("\(severity)/10")
                        .font(.system(size: 11, weight: .bold))
                        .monospacedDigit()
                        .foregroundStyle(.white)
                }
            }

            if !blockedLabel.isEmpty {
                HStack(spacing: DS.Spacing.xs) {
                    Image(systemName: "exclamationmark.triangle.fill")
                        .font(.system(size: 10))
                        .foregroundStyle(DS.Colors.warning)
                    Text("Blocks \(blockedLabel)")
                        .font(.system(size: 11, weight: .medium))
                        .foregroundStyle(DS.Colors.onSurface)
                }
            }

            if let notes = injury.notes, !notes.isEmpty {
                Text(notes)
                    .font(.footnote)
                    .foregroundStyle(DS.Colors.onSurface)
                    .fixedSize(horizontal: false, vertical: true)
            }

            VStack(alignment: .leading, spacing: DS.Spacing.xs) {
                if !reportedLabel.isEmpty {
                    Text("Reported \(reportedLabel)")
                        .font(.system(size: 10))
                        .foregroundStyle(DS.Colors.outline)
                }
                Text(recoveryLabel)
                    .font(.system(size: 10))
                    .foregroundStyle(DS.Colors.outline)
            }
        }
        .glassCard()
        .accessibilityElement(children: .combine)
    }

    /// The enforcer's tokens read as jargon on a card. "bike" is what makes the
    /// plan drop a ride, so the label has to say which sessions vanish.
    private var blockedLabel: String {
        let names = ["swim": "swimming", "bike": "cycling",
                     "run": "running", "strength": "gym"]
        let tokens = (injury.affectedSports ?? "")
            .split(separator: ",")
            .map { $0.trimmingCharacters(in: .whitespaces).lowercased() }
            .filter { !$0.isEmpty }
        guard !tokens.isEmpty else { return "" }
        return tokens.map { names[$0] ?? $0 }.joined(separator: ", ")
    }

    private var reportedLabel: String { Self.displayDate(injury.dateReported) }

    private var recoveryLabel: String {
        guard let raw = injury.expectedRecoveryDate, !raw.isEmpty else {
            return "Blocks training until you resolve it"
        }
        return "Lifts on \(Self.displayDate(raw))"
    }

    /// en_US_POSIX to parse, en_US to display: the app's copy is English, and
    /// an unlocalized style on an es_MX phone splices Spanish into it.
    private static let parser: DateFormatter = {
        let f = DateFormatter()
        f.locale = Locale(identifier: "en_US_POSIX")
        f.dateFormat = "yyyy-MM-dd"
        return f
    }()

    private static let display: DateFormatter = {
        let f = DateFormatter()
        f.locale = Locale(identifier: "en_US")
        f.dateStyle = .medium
        f.timeStyle = .none
        return f
    }()

    static func displayDate(_ raw: String?) -> String {
        guard let raw, !raw.isEmpty else { return "" }
        let dayPart = String(raw.split(separator: "T").first ?? "")
        guard let date = parser.date(from: dayPart) else { return dayPart }
        return display.string(from: date)
    }

    @ViewBuilder
    private func statusBadge(status: String) -> some View {
        let color: Color = status.lowercased() == "active" ? DS.Colors.danger :
                           status.lowercased() == "recovering" ? DS.Colors.warning :
                           DS.Colors.success

        Text(status)
            .textCase(.uppercase)
            .font(.system(size: 10, weight: .bold))
            .tracking(DS.Tracking.normal)
            .padding(.horizontal, DS.Spacing.s)
            .padding(.vertical, DS.Spacing.xs)
            .background(color.opacity(0.2))
            .foregroundStyle(color)
            .clipShape(Capsule())
    }
}

extension Injury {
    /// Shape-only stand-in for the redacted loading state.
    static let placeholder = Injury(
        id: -1, dateReported: "2026-01-01", bodyPart: "Left Calf",
        status: "Active", severity: 5, notes: "Loading the injury history.",
        affectedSports: "run", expectedRecoveryDate: nil
    )
}

// MARK: - Form

/// Create or edit one injury. Same sheet both ways: the fields are identical,
/// and a separate edit screen was the thing whose absence sent the athlete to
/// argue with the coach in chat.
struct InjuryFormSheet: View {
    @Environment(\.dismiss) private var dismiss
    @StateObject private var network = NetworkManager.shared

    /// nil creates, non-nil edits that row.
    let editing: Injury?
    var onSave: () -> Void

    @State private var bodyPart = ""
    @State private var status = "Active"
    @State private var severity: Double = 5
    @State private var notes = ""
    @State private var blocked: Set<String> = []
    @State private var liftsAutomatically = false
    @State private var liftDate = Date()
    @State private var isSaving = false
    @State private var errorMessage: String?

    private let statuses = ["Active", "Recovering", "Resolved"]

    /// Tokens are `constraint_enforcer.SPORT_TO_INJURY_TOKENS` aliases. Typing
    /// this field by hand is what produced "cycling,running" for an ankle that
    /// only needed running blocked, and the plan deleted every ride for three
    /// days.
    private let sportOptions: [(token: String, label: String, icon: String)] = [
        ("swim", "Swim", "figure.pool.swim"),
        ("bike", "Bike", "figure.outdoor.cycle"),
        ("run", "Run", "figure.run"),
        ("strength", "Gym", "dumbbell.fill"),
    ]

    private var isEditing: Bool { editing?.id != nil }
    private var canSave: Bool {
        !bodyPart.trimmingCharacters(in: .whitespaces).isEmpty && !isSaving
    }

    var body: some View {
        NavigationStack {
            ZStack {
                DS.Colors.background.ignoresSafeArea()

                Form {
                    Section {
                        TextField("Body Part (e.g. Left Ankle)", text: $bodyPart)
                            .textFieldStyle(.plain)
                            .listRowBackground(DS.Colors.surface)
                            .foregroundStyle(.white)

                        Picker("Status", selection: $status) {
                            ForEach(statuses, id: \.self) { Text($0) }
                        }
                        .listRowBackground(DS.Colors.surface)
                        .foregroundStyle(.white)

                        VStack(alignment: .leading, spacing: DS.Spacing.s) {
                            HStack {
                                Text("Severity")
                                    .foregroundStyle(.white)
                                Spacer()
                                Text("\(Int(severity))/10")
                                    .monospacedDigit()
                                    .foregroundStyle(DS.Colors.outline)
                            }
                            .font(.system(size: 13))
                            Slider(value: $severity, in: 1...10, step: 1)
                                .tint(DS.Colors.accent)
                        }
                        .listRowBackground(DS.Colors.surface)
                        .accessibilityElement(children: .combine)
                        .accessibilityValue("\(Int(severity)) out of 10")
                    } header: {
                        Text("Details")
                    }

                    Section {
                        HStack(spacing: DS.Spacing.s) {
                            ForEach(sportOptions, id: \.token) { option in
                                sportChip(option)
                            }
                        }
                        .listRowBackground(DS.Colors.surface)
                    } header: {
                        Text("Blocks")
                    } footer: {
                        Text(blocked.isEmpty
                             ? "Nothing selected — the plan schedules every sport as normal."
                             : "The plan removes these sessions while the injury is Active.")
                    }

                    Section {
                        Toggle("Lifts on a date", isOn: $liftsAutomatically.animation(DS.Animation.quick))
                            .tint(DS.Colors.success)
                            .listRowBackground(DS.Colors.surface)
                            .foregroundStyle(.white)

                        if liftsAutomatically {
                            DatePicker("Date",
                                       selection: $liftDate,
                                       displayedComponents: .date)
                                .datePickerStyle(.compact)
                                .listRowBackground(DS.Colors.surface)
                                .foregroundStyle(.white)
                                .transition(.opacity)
                        }
                    } header: {
                        Text("Recovery")
                    } footer: {
                        Text(liftsAutomatically
                             ? "On this date the restriction lifts by itself and training resumes."
                             : "The restriction holds until you resolve it here. Safer when you don't know how long it will take.")
                    }

                    Section {
                        TextEditor(text: $notes)
                            .frame(height: 100)
                            .scrollContentBackground(.hidden)
                            .listRowBackground(DS.Colors.surface)
                            .foregroundStyle(.white)
                    } header: {
                        Text("Notes")
                    } footer: {
                        Text("The coach reads these, and now sees how old they are.")
                    }
                }
                .scrollContentBackground(.hidden)
            }
            .navigationTitle(isEditing ? "Edit Injury" : "New Injury")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarLeading) {
                    Button("Cancel") { dismiss() }
                        .foregroundStyle(DS.Colors.outline)
                }
                ToolbarItem(placement: .topBarTrailing) {
                    if isSaving {
                        ProgressView().tint(DS.Colors.accent)
                    } else {
                        Button("Save") { Task { await save() } }
                            .font(.system(size: 15, weight: .semibold))
                            .foregroundStyle(canSave ? DS.Colors.accent : DS.Colors.outline)
                            .disabled(!canSave)
                    }
                }
            }
            .alert("Couldn't save",
                   isPresented: .constant(errorMessage != nil)) {
                Button("OK", role: .cancel) { errorMessage = nil }
            } message: {
                Text(errorMessage ?? "")
            }
        }
        .onAppear(perform: hydrate)
    }

    @ViewBuilder
    private func sportChip(_ option: (token: String, label: String, icon: String)) -> some View {
        let isOn = blocked.contains(option.token)
        Button {
            withAnimation(DS.Animation.quick) {
                if isOn { blocked.remove(option.token) } else { blocked.insert(option.token) }
            }
            UIImpactFeedbackGenerator(style: .light).impactOccurred()
        } label: {
            VStack(spacing: DS.Spacing.xs) {
                Image(systemName: option.icon)
                    .font(.system(size: 15))
                    .symbolRenderingMode(.hierarchical)
                Text(option.label)
                    .textCase(.uppercase)
                    .font(.system(size: 10, weight: .bold))
                    .tracking(DS.Tracking.normal)
            }
            .foregroundStyle(isOn ? DS.Colors.onAccent : DS.Colors.onSurface)
            .frame(maxWidth: .infinity, minHeight: 56)
            .background(isOn ? DS.Colors.accent : Color.white.opacity(0.06),
                        in: RoundedRectangle(cornerRadius: DS.Radius.medium))
            .overlay(
                RoundedRectangle(cornerRadius: DS.Radius.medium)
                    .strokeBorder(Color.white.opacity(isOn ? 0 : 0.12), lineWidth: 1)
            )
            .contentShape(RoundedRectangle(cornerRadius: DS.Radius.medium))
        }
        .buttonStyle(.plain)
        .accessibilityLabel(option.label)
        .accessibilityValue(isOn ? "Blocked" : "Allowed")
        .accessibilityAddTraits(isOn ? .isSelected : [])
    }

    private func hydrate() {
        guard let injury = editing else { return }
        bodyPart = injury.bodyPart ?? ""
        status = statuses.contains(injury.status ?? "") ? (injury.status ?? "Active") : "Active"
        severity = Double(injury.severity ?? 5)
        notes = injury.notes ?? ""
        blocked = Set(
            (injury.affectedSports ?? "")
                .split(separator: ",")
                .map { $0.trimmingCharacters(in: .whitespaces).lowercased() }
                .filter { !$0.isEmpty }
        )
        if let raw = injury.expectedRecoveryDate, !raw.isEmpty,
           let parsed = Self.isoDay.date(from: String(raw.split(separator: "T").first ?? "")) {
            liftsAutomatically = true
            liftDate = parsed
        }
    }

    private func save() async {
        isSaving = true
        defer { isSaving = false }

        let sports = sportOptions.map(\.token).filter { blocked.contains($0) }.joined(separator: ",")
        let recovery = liftsAutomatically ? Self.isoDay.string(from: liftDate) : nil

        do {
            if let id = editing?.id {
                // Explicit nulls matter here: clearing the date or the sport
                // list has to reach the server as null, not as an absent key
                // the endpoint skips.
                try await network.updateInjuryFields(id: id, fields: [
                    "body_part": bodyPart.trimmingCharacters(in: .whitespaces),
                    "status": status,
                    "severity": Int(severity),
                    "notes": notes.isEmpty ? nil : notes,
                    "affected_sports": sports.isEmpty ? nil : sports,
                    "expected_recovery_date": recovery,
                ])
            } else {
                try await network.addInjury(Injury(
                    id: nil,
                    dateReported: nil,
                    bodyPart: bodyPart.trimmingCharacters(in: .whitespaces),
                    status: status,
                    severity: Int(severity),
                    notes: notes.isEmpty ? nil : notes,
                    affectedSports: sports.isEmpty ? nil : sports,
                    expectedRecoveryDate: recovery
                ))
            }
            UINotificationFeedbackGenerator().notificationOccurred(.success)
            onSave()
            dismiss()
        } catch {
            UINotificationFeedbackGenerator().notificationOccurred(.error)
            errorMessage = error.localizedDescription
        }
    }

    /// Date-only, athlete's calendar day. `date.fromisoformat` on the server
    /// wants exactly this shape.
    private static let isoDay: DateFormatter = {
        let f = DateFormatter()
        f.locale = Locale(identifier: "en_US_POSIX")
        f.dateFormat = "yyyy-MM-dd"
        return f
    }()
}
