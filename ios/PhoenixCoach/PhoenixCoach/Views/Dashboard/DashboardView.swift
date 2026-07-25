import SwiftUI

struct DashboardView: View {
    @State private var rpe: Double = 5
    @State private var motivation: Double = 3
    @State private var soreness: Double = 2
    @State private var sleepQuality: Double = 3
    @State private var notes: String = ""
    @State private var isSubmitting = false
    @State private var submitSuccess = false
    @State private var errorMessage: String? = nil
    
    var body: some View {
        NavigationStack {
            ZStack {
                DS.Colors.background
                    .ignoresSafeArea()
                
                ScrollView {
                    VStack(spacing: 24) {
                        // Header
                        VStack(spacing: 8) {
                            Text("How are you feeling today?")
                                .font(.headline)
                                .foregroundStyle(DS.Colors.primaryText)
                            Text("This data helps Phoenix adapt your upcoming workouts.")
                                .font(.caption)
                                .foregroundStyle(DS.Colors.outline)
                                .multilineTextAlignment(.center)
                        }
                        .padding(.top, 10)
                        
                        // RPE Slider
                        VStack(alignment: .leading, spacing: 8) {
                            HStack {
                                Text("RPE (Effort)")
                                    .font(.subheadline.bold())
                                    .foregroundStyle(DS.Colors.primaryText)
                                Spacer()
                                Text("\(Int(rpe))/10")
                                    .font(.headline)
                                    .foregroundStyle(DS.Colors.accent)
                            }
                            Slider(value: $rpe, in: 1...10, step: 1)
                                .tint(DS.Colors.accent)
                            HStack {
                                Text("Very Easy")
                                    .font(.caption2)
                                    .foregroundStyle(DS.Colors.outline)
                                Spacer()
                                Text("Max Effort")
                                    .font(.caption2)
                                    .foregroundStyle(DS.Colors.outline)
                            }
                        }
                        .padding()
                        .glassCard()
                        
                        // Motivation & Soreness
                        HStack(spacing: 16) {
                            // Motivation
                            VStack(alignment: .leading, spacing: 8) {
                                HStack {
                                    Text("Motivation")
                                        .font(.subheadline.bold())
                                        .foregroundStyle(DS.Colors.primaryText)
                                    Spacer()
                                    Text("\(Int(motivation))/5")
                                        .font(.headline)
                                        .foregroundStyle(DS.Colors.accent)
                                }
                                Slider(value: $motivation, in: 1...5, step: 1)
                                    .tint(.green)
                            }
                            .padding()
                            .glassCard()
                            
                            // Soreness
                            VStack(alignment: .leading, spacing: 8) {
                                HStack {
                                    Text("Soreness")
                                        .font(.subheadline.bold())
                                        .foregroundStyle(DS.Colors.primaryText)
                                    Spacer()
                                    Text("\(Int(soreness))/5")
                                        .font(.headline)
                                        .foregroundStyle(DS.Colors.accent)
                                }
                                Slider(value: $soreness, in: 1...5, step: 1)
                                    .tint(.red)
                            }
                            .padding()
                            .glassCard()
                        }
                        
                        // Sleep Quality
                        VStack(alignment: .leading, spacing: 8) {
                            HStack {
                                Text("Sleep Quality")
                                    .font(.subheadline.bold())
                                    .foregroundStyle(DS.Colors.primaryText)
                                Spacer()
                                Text("\(Int(sleepQuality))/5")
                                    .font(.headline)
                                    .foregroundStyle(DS.Colors.accent)
                            }
                            Slider(value: $sleepQuality, in: 1...5, step: 1)
                                .tint(.blue)
                        }
                        .padding()
                        .glassCard()
                        
                        // Notes
                        VStack(alignment: .leading, spacing: 8) {
                            Text("General Notes")
                                .font(.subheadline.bold())
                                .foregroundStyle(DS.Colors.primaryText)
                            
                            TextEditor(text: $notes)
                                .frame(height: 100)
                                .padding(8)
                                .scrollContentBackground(.hidden)
                                .background(Color.black.opacity(0.3))
                                .clipShape(RoundedRectangle(cornerRadius: 8))
                                .foregroundStyle(DS.Colors.primaryText)
                        }
                        .padding()
                        .glassCard()
                        
                        if let error = errorMessage {
                            Text(error)
                                .font(.caption)
                                .foregroundStyle(.red)
                        }
                        
                        Button(action: submitFeedback) {
                            HStack {
                                if isSubmitting {
                                    ProgressView()
                                        .tint(.black)
                                } else if submitSuccess {
                                    Image(systemName: "checkmark.circle.fill")
                                    Text("Saved")
                                } else {
                                    Text("Save Journal")
                                }
                            }
                            .font(.headline)
                            .foregroundStyle(.black)
                            .frame(maxWidth: .infinity)
                            .padding()
                            .background(submitSuccess ? Color.green : DS.Colors.accent)
                            .clipShape(Capsule())
                        }
                        .disabled(isSubmitting || submitSuccess)
                        .padding(.top, 10)
                    }
                    .padding()
                }
            }
            .navigationTitle("Journal")
            .onChange(of: rpe) { _ in resetSuccess() }
            .onChange(of: motivation) { _ in resetSuccess() }
            .onChange(of: soreness) { _ in resetSuccess() }
            .onChange(of: sleepQuality) { _ in resetSuccess() }
            .onChange(of: notes) { _ in resetSuccess() }
        }
    }
    
    private func resetSuccess() {
        if submitSuccess {
            submitSuccess = false
        }
    }
    
    private func submitFeedback() {
        guard !isSubmitting else { return }
        isSubmitting = true
        errorMessage = nil
        
        let entry = FeedbackEntry(
            rpe: Int(rpe),
            motivation: Int(motivation),
            soreness: Int(soreness),
            sleepQuality: Int(sleepQuality),
            notes: notes,
            strengthExercises: []
        )
        
        Task {
            do {
                try await NetworkManager.shared.submitFeedback(entry)
                await MainActor.run {
                    self.submitSuccess = true
                    self.isSubmitting = false
                    UIImpactFeedbackGenerator(style: .medium).impactOccurred()
                }
            } catch {
                await MainActor.run {
                    self.errorMessage = "Failed to save: \(error.localizedDescription)"
                    self.isSubmitting = false
                }
            }
        }
    }
}

#Preview {
    DashboardView()
        .preferredColorScheme(.dark)
}
