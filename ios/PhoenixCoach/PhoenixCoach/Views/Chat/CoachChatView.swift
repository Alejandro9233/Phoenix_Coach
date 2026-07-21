import SwiftUI
import Combine

struct CoachChatView: View {
    @StateObject private var network = NetworkManager.shared
    @StateObject private var llm = LocalLLMManager.shared
    
    @State private var messages: [ChatMessage] = []
    @State private var inputText = ""
    @State private var isLoading = false
    @State private var isStreaming = false
    @FocusState private var isInputFocused: Bool
    
    // Context Data
    @State private var profile: AthleteProfile?
    @State private var dashboard: DashboardResponse?
    @State private var plan: WeeklyPlanResponse?
    
    var body: some View {
        NavigationStack {
            VStack(spacing: 0) {
                // Connection status banner
                if !llm.isModelLoaded {
                    connectionBanner
                }
                
                // Messages
                ScrollViewReader { proxy in
                    ScrollView {
                        LazyVStack(spacing: 12) {
                            if messages.isEmpty {
                                welcomeMessage
                            }
                            ForEach(messages) { msg in
                                messageBubble(msg)
                                    .id(msg.id)
                            }
                            if isLoading && !isStreaming {
                                typingIndicator
                            }
                        }
                        .padding()
                    }
                    .onChange(of: messages.count) {
                        scrollToBottom(proxy: proxy)
                    }
                    .onChange(of: messages.last?.content) {
                        scrollToBottom(proxy: proxy)
                    }
                }
                
                Divider()
                
                // Input bar
                inputBar
            }
            .navigationTitle("Coach Phoenix")
            .background(Color(.systemBackground))
            .task {
                await network.checkConnection()
                
                // Fetch context silently
                async let p = try? network.fetchAthleteProfile()
                async let d = try? network.fetchDashboard()
                async let w = try? network.fetchWeeklyPlan()
                
                let (profileResult, dashResult, planResult) = await (p, d, w)
                self.profile = profileResult
                self.dashboard = dashResult
                self.plan = planResult
                
                // Start loading the local model
                await llm.loadModel()
            }
            .onDisappear {
                llm.unloadModel()
            }
        }
    }
    
    private func scrollToBottom(proxy: ScrollViewProxy) {
        if let last = messages.last {
            withAnimation(.easeOut(duration: 0.15)) {
                proxy.scrollTo(last.id, anchor: .bottom)
            }
        }
    }
    
    // MARK: - Components
    
    private var connectionBanner: some View {
        HStack(spacing: 8) {
            Image(systemName: "cpu")
                .font(.caption)
            Text(llm.isDownloading ? llm.statusMessage : "Coach Brain Loading...")
                .font(.caption.bold())
            Spacer()
            if llm.isDownloading {
                ProgressView(value: llm.downloadProgress)
                    .progressViewStyle(.linear)
                    .frame(width: 60)
            }
        }
        .foregroundStyle(.white)
        .padding(.horizontal, 16)
        .padding(.vertical, 8)
        .background(Color.blue.opacity(0.85))
    }
    
    private var welcomeMessage: some View {
        VStack(spacing: 12) {
            Image(systemName: "flame.fill")
                .font(.system(size: 48))
                .foregroundStyle(.orange)
            Text("Ask Your Coach")
                .font(.title2.bold())
            Text("I know your training data, recovery metrics, and coaching principles. Ask me anything about your training.")
                .font(.subheadline)
                .foregroundStyle(.secondary)
                .multilineTextAlignment(.center)
            
            // Suggestion chips
            VStack(spacing: 8) {
                suggestionChip("Should I train hard today?")
                suggestionChip("What's my weekly load looking like?")
                suggestionChip("Plan my next 3 running sessions")
                suggestionChip("How should I prepare for my marathon?")
            }
            .padding(.top, 8)
        }
        .padding(32)
    }
    
    private func suggestionChip(_ text: String) -> some View {
        Button {
            inputText = text
            Task { await sendMessage() }
        } label: {
            Text(text)
                .font(.subheadline)
                .padding(.horizontal, 16)
                .padding(.vertical, 10)
                .frame(maxWidth: .infinity)
                .background(.ultraThinMaterial)
                .clipShape(RoundedRectangle(cornerRadius: 12))
        }
        .buttonStyle(.plain)
    }
    
    private func messageBubble(_ message: ChatMessage) -> some View {
        HStack {
            if message.role == .user { Spacer(minLength: 60) }
            
            VStack(alignment: message.role == .user ? .trailing : .leading, spacing: 4) {
                Text(message.content)
                    .font(.subheadline)
                    .padding(12)
                    .background(message.role == .user ? Color.orange : Color(.systemGray5))
                    .foregroundStyle(message.role == .user ? .white : .primary)
                    .clipShape(RoundedRectangle(cornerRadius: 16))
                
                Text(message.timestamp, style: .time)
                    .font(.caption2)
                    .foregroundStyle(.tertiary)
            }
            
            if message.role == .coach { Spacer(minLength: 60) }
        }
    }
    
    private var typingIndicator: some View {
        HStack {
            VStack(alignment: .leading, spacing: 8) {
                HStack(spacing: 5) {
                    ForEach(0..<3) { i in
                        Circle()
                            .fill(.orange)
                            .frame(width: 8, height: 8)
                            .scaleEffect(isLoading ? 1.0 : 0.5)
                            .opacity(isLoading ? 1.0 : 0.3)
                            .animation(
                                .easeInOut(duration: 0.6)
                                .repeatForever(autoreverses: true)
                                .delay(Double(i) * 0.2),
                                value: isLoading
                            )
                    }
                }
                
                ThinkingStatusText()
            }
            .padding(12)
            .background(Color(.systemGray5))
            .clipShape(RoundedRectangle(cornerRadius: 16))
            Spacer()
        }
    }
    
    private var inputBar: some View {
        HStack(spacing: 12) {
            TextField("Ask your coach...", text: $inputText, axis: .vertical)
                .textFieldStyle(.plain)
                .padding(12)
                .background(Color(.systemGray6))
                .clipShape(RoundedRectangle(cornerRadius: 20))
                .lineLimit(1...4)
                .focused($isInputFocused)
            
            Button {
                Task { await sendMessage() }
            } label: {
                Image(systemName: "arrow.up.circle.fill")
                    .font(.system(size: 32))
                    .foregroundStyle(inputText.isEmpty || isLoading ? .gray : .orange)
            }
            .disabled(inputText.isEmpty || isLoading)
        }
        .padding(.horizontal)
        .padding(.vertical, 8)
        .background(.bar)
    }
    
    // MARK: - Networking (Streaming)
    
    private func sendMessage() async {
        let text = inputText.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty else { return }
        
        let userMsg = ChatMessage(role: .user, content: text, timestamp: Date())
        messages.append(userMsg)
        inputText = ""
        isInputFocused = false
        isLoading = true
        isStreaming = false
        
        // Create an empty coach message that we'll fill with streamed tokens
        let coachMsgIndex = messages.count
        let coachMsg = ChatMessage(role: .coach, content: "", timestamp: Date())
        messages.append(coachMsg)
        
        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "en_US")
        formatter.dateFormat = "EEEE"
        let todayName = formatter.string(from: Date())
        
        let systemPrompt = ContextBuilder.buildSystemPrompt(
            profile: profile,
            dashboard: dashboard,
            plan: plan,
            todayName: todayName
        )
        
        do {
            let stream = llm.generateStream(prompt: text, systemPrompt: systemPrompt)
            
            for try await token in stream {
                await MainActor.run {
                    if !isStreaming {
                        isStreaming = true  // First token arrived — hide typing indicator
                    }
                    messages[coachMsgIndex].content += token
                }
            }
            
            await MainActor.run {
                isLoading = false
                isStreaming = false
                if messages[coachMsgIndex].content.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
                    messages[coachMsgIndex].content = "I'm having trouble thinking right now."
                }
            }
        } catch {
            await MainActor.run {
                messages[coachMsgIndex].content = "Local coach error: \(error.localizedDescription)"
                isLoading = false
                isStreaming = false
            }
        }
    }
}

// MARK: - Thinking Status Text

struct ThinkingStatusText: View {
    @State private var messageIndex = 0
    
    private let messages = [
        "Analyzing your data...",
        "Checking training load...",
        "Reviewing recovery metrics...",
        "Coach is thinking...",
    ]
    
    let timer = Timer.publish(every: 2.0, on: .main, in: .common).autoconnect()
    
    var body: some View {
        Text(messages[messageIndex])
            .font(.caption)
            .foregroundStyle(.secondary)
            .animation(.easeInOut(duration: 0.3), value: messageIndex)
            .onReceive(timer) { _ in
                messageIndex = (messageIndex + 1) % messages.count
            }
    }
}

#Preview {
    CoachChatView()
        .preferredColorScheme(.dark)
}
