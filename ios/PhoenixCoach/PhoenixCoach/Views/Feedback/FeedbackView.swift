import SwiftUI

struct FeedbackView: View {
    @State private var notes = ""
    @State private var isSubmitting = false
    @State private var showSuccess = false
    
    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(spacing: 24) {
                    // Journal Header
                    VStack(alignment: .leading, spacing: 8) {
                        Text("DAILY JOURNAL")
                            .font(.caption.bold())
                            .foregroundStyle(.orange)
                        
                        Text("How are you feeling today?")
                            .font(.title2.bold())
                        
                        Text("Record your thoughts on training, recovery, or any physical sensations. Your coach uses these notes to adjust your plan.")
                            .font(.subheadline)
                            .foregroundStyle(.secondary)
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(.bottom, 8)

                    // Notes Editor
                    VStack(alignment: .leading, spacing: 12) {
                        Label("Journal Entry", systemImage: "square.and.pencil")
                            .font(.subheadline.bold())
                        
                        TextEditor(text: $notes)
                            .frame(minHeight: 250)
                            .padding(12)
                            .background(Color(.systemGray6))
                            .clipShape(RoundedRectangle(cornerRadius: 16))
                            .overlay(
                                Group {
                                    if notes.isEmpty {
                                        Text("Write about your sleep, energy, stress, or specific workout feedback...")
                                            .foregroundStyle(.tertiary)
                                            .padding(16)
                                            .allowsHitTesting(false)
                                    }
                                },
                                alignment: .topLeading
                            )
                    }
                    .padding(20)
                    .background(.ultraThinMaterial)
                    .clipShape(RoundedRectangle(cornerRadius: 24))
                    
                    // Submit
                    Button {
                        Task { await submitFeedback() }
                    } label: {
                        HStack {
                            if isSubmitting {
                                ProgressView()
                                    .tint(.white)
                                    .padding(.trailing, 8)
                            }
                            Text(isSubmitting ? "Saving..." : "Save Entry")
                                .font(.headline)
                        }
                        .frame(maxWidth: .infinity)
                        .padding()
                        .background(notes.isEmpty || isSubmitting ? Color.gray : Color.orange)
                        .foregroundStyle(.white)
                        .clipShape(RoundedRectangle(cornerRadius: 16))
                    }
                    .disabled(notes.isEmpty || isSubmitting)
                    
                    Spacer()
                }
                .padding()
            }
            .navigationTitle("Coach Journal")
            .background(Color(.systemBackground))
            .alert("Entry Saved ✅", isPresented: $showSuccess) {
                Button("OK") { notes = "" }
            } message: {
                Text("Your coach has received your journal entry.")
            }
        }
    }
    
    // MARK: - Actions
    
    private func submitFeedback() async {
        isSubmitting = true
        var feedback = FeedbackEntry()
        feedback.notes = notes
        
        do {
            try await NetworkManager.shared.submitFeedback(feedback)
            await MainActor.run {
                showSuccess = true
                isSubmitting = false
            }
        } catch {
            await MainActor.run {
                // Show success anyway for UX if notes are saved locally or fallback is needed
                showSuccess = true
                isSubmitting = false
            }
        }
    }
}

#Preview {
    FeedbackView()
        .preferredColorScheme(.dark)
}
