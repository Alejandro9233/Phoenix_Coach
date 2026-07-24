import SwiftUI

struct InjuryLogView: View {
    @StateObject private var network = NetworkManager.shared
    @State private var injuries: [Injury] = []
    @State private var isLoading = true
    @State private var errorMessage: String?
    @State private var showAddSheet = false
    
    var body: some View {
        ZStack {
            DS.Colors.background.ignoresSafeArea()
            
            if isLoading {
                ProgressView()
                    .controlSize(.large)
                    .tint(DS.Colors.accent)
            } else if let error = errorMessage {
                VStack {
                    Text("Error")
                        .font(.headline)
                        .foregroundStyle(.white)
                    Text(error)
                        .font(.subheadline)
                        .foregroundStyle(.gray)
                    Button("Retry") {
                        Task { await loadInjuries() }
                    }
                    .padding(.top)
                }
            } else {
                List {
                    if injuries.isEmpty {
                        ContentUnavailableView(
                            "No Injury History",
                            systemImage: "bandage",
                            description: Text("You are currently injury-free.")
                        )
                        .padding(.top, 40)
                        .listRowBackground(Color.clear)
                        .listRowSeparator(.hidden)
                    } else {
                        ForEach(injuries) { injury in
                            InjuryCard(injury: injury)
                                .listRowInsets(EdgeInsets(top: 0, leading: 0, bottom: 16, trailing: 0))
                                .listRowBackground(Color.clear)
                                .listRowSeparator(.hidden)
                                .swipeActions(edge: .trailing, allowsFullSwipe: true) {
                                    Button(role: .destructive) {
                                        deleteInjury(injury)
                                    } label: {
                                        Label("Delete", systemImage: "trash")
                                    }
                                }
                        }
                    }
                }
                .listStyle(.plain)
                .scrollContentBackground(.hidden)
                .padding(.horizontal)
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
                        .foregroundStyle(DS.Colors.accent)
                }
            }
        }
        .sheet(isPresented: $showAddSheet) {
            AddInjurySheet {
                Task { await loadInjuries() }
            }
        }
        .task {
            await loadInjuries()
        }
    }
    
    private func loadInjuries() async {
        isLoading = true
        errorMessage = nil
        do {
            injuries = try await network.fetchInjuries()
            isLoading = false
        } catch {
            errorMessage = error.localizedDescription
            isLoading = false
        }
    }
    
    private func deleteInjury(_ injury: Injury) {
        // In a real app, make a network request to delete.
        // For now, delete locally and trigger haptic
        UIImpactFeedbackGenerator(style: .medium).impactOccurred()
        withAnimation {
            injuries.removeAll { $0.id == injury.id }
        }
    }
}

struct InjuryCard: View {
    let injury: Injury
    
    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack {
                Text(injury.bodyPart?.uppercased() ?? "UNKNOWN")
                    .font(.system(size: 14, weight: .black))
                    .tracking(1.0)
                    .foregroundStyle(.white)
                Spacer()
                statusBadge(status: injury.status ?? "Unknown")
            }
            
            if let severity = injury.severity {
                HStack(spacing: 4) {
                    Text("Severity:")
                        .font(.system(size: 12))
                        .foregroundStyle(DS.Colors.outline)
                    Text("\(severity)/10")
                        .font(.system(size: 12, weight: .bold))
                        .foregroundStyle(DS.Colors.accent)
                }
            }
            
            if let sports = injury.affectedSports, !sports.isEmpty {
                HStack(spacing: 4) {
                    Image(systemName: "exclamationmark.triangle.fill")
                        .font(.system(size: 10))
                        .foregroundStyle(DS.Colors.warning)
                    Text("Impacts: \(sports)")
                        .font(.system(size: 12, weight: .medium))
                        .foregroundStyle(DS.Colors.onSurface)
                }
            }
            
            if let notes = injury.notes, !notes.isEmpty {
                Text(notes)
                    .font(.system(size: 13))
                    .foregroundStyle(DS.Colors.outline)
                    .fixedSize(horizontal: false, vertical: true)
            }
            
            if !formattedDate.isEmpty {
                Text("Reported: \(formattedDate)")
                    .font(.system(size: 10))
                    .foregroundStyle(DS.Colors.outline.opacity(0.7))
                    .padding(.top, 4)
            }
        }
        .padding(16)
        .background(Color.white.opacity(0.04))
        .clipShape(RoundedRectangle(cornerRadius: 12))
        .overlay(
            RoundedRectangle(cornerRadius: 12)
                .strokeBorder(Color.white.opacity(0.08), lineWidth: 1)
        )
    }
    
    private var formattedDate: String {
        guard let dateStr = injury.dateReported else { return "" }
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        let date = formatter.date(from: dateStr) ?? ISO8601DateFormatter().date(from: dateStr)
        
        if let d = date {
            let outFormatter = DateFormatter()
            outFormatter.dateStyle = .medium
            return outFormatter.string(from: d)
        }
        
        if dateStr.contains("T") {
            return String(dateStr.split(separator: "T").first ?? "")
        }
        return dateStr
    }
    
    @ViewBuilder
    private func statusBadge(status: String) -> some View {
        let color: Color = status.lowercased() == "active" ? DS.Colors.danger :
                           status.lowercased() == "recovering" ? DS.Colors.warning :
                           DS.Colors.success
        
        Text(status.uppercased())
            .font(.system(size: 10, weight: .bold))
            .tracking(1.0)
            .padding(.horizontal, 8)
            .padding(.vertical, 4)
            .background(color.opacity(0.2))
            .foregroundStyle(color)
            .clipShape(Capsule())
    }
}

struct AddInjurySheet: View {
    @Environment(\.dismiss) var dismiss
    var onSave: () -> Void
    
    @State private var bodyPart = ""
    @State private var status = "Active"
    @State private var severity: Double = 5
    @State private var notes = ""
    @State private var affectedSports = ""
    @State private var isSaving = false
    @State private var errorMessage: String?
    
    let statuses = ["Active", "Recovering", "Resolved"]
    
    var body: some View {
        NavigationStack {
            ZStack {
                DS.Colors.background.ignoresSafeArea()
                
                Form {
                    Section {
                        TextField("Body Part (e.g. Left Knee)", text: $bodyPart)
                            .listRowBackground(DS.Colors.surface)
                            .foregroundStyle(.white)
                        
                        Picker("Status", selection: $status) {
                            ForEach(statuses, id: \.self) { Text($0) }
                        }
                        .listRowBackground(DS.Colors.surface)
                        .foregroundStyle(.white)
                        
                        VStack(alignment: .leading) {
                            Text("Severity: \(Int(severity))/10")
                                .foregroundStyle(.white)
                            Slider(value: $severity, in: 1...10, step: 1)
                                .tint(DS.Colors.accent)
                        }
                        .listRowBackground(DS.Colors.surface)
                        
                        TextField("Affected Sports (e.g. run, bike)", text: $affectedSports)
                            .listRowBackground(DS.Colors.surface)
                            .foregroundStyle(.white)
                    } header: {
                        Text("Details")
                    }
                    
                    Section {
                        TextEditor(text: $notes)
                            .frame(height: 100)
                            .listRowBackground(DS.Colors.surface)
                            .foregroundStyle(.white)
                    } header: {
                        Text("Notes")
                    }
                }
                .scrollContentBackground(.hidden)
            }
            .navigationTitle("New Injury")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Cancel") { dismiss() }
                        .foregroundStyle(DS.Colors.outline)
                }
                ToolbarItem(placement: .confirmationAction) {
                    Button("Save") {
                        Task { await saveInjury() }
                    }
                    .disabled(bodyPart.isEmpty || isSaving)
                    .foregroundStyle(DS.Colors.accent)
                }
            }
            .alert("Error Saving", isPresented: Binding(
                get: { errorMessage != nil },
                set: { if !$0 { errorMessage = nil } }
            )) {
                Button("OK", role: .cancel) { }
            } message: {
                if let msg = errorMessage {
                    Text(msg)
                }
            }
        }
        .preferredColorScheme(.dark)
    }
    
    private func saveInjury() async {
        isSaving = true
        let injury = Injury(
            dateReported: nil, // backend will set today
            bodyPart: bodyPart,
            status: status,
            severity: Int(severity),
            notes: notes,
            affectedSports: affectedSports
        )
        
        do {
            try await NetworkManager.shared.addInjury(injury)
            let haptic = UINotificationFeedbackGenerator()
            haptic.notificationOccurred(.success)
            isSaving = false
            dismiss()
            onSave()
        } catch {
            let haptic = UINotificationFeedbackGenerator()
            haptic.notificationOccurred(.error)
            errorMessage = error.localizedDescription
            isSaving = false
        }
    }
}
