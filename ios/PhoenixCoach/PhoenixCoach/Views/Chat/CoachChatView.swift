import SwiftUI
import Combine

struct CoachChatView: View {
    @StateObject private var network = NetworkManager.shared
    
    @State private var messages: [ChatMessage] = []
    @State private var inputText = ""
    @State private var isLoading = false
    @State private var isStreaming = false
    @FocusState private var isInputFocused: Bool
    
    // Session state
    @State private var showHistory = false
    @State private var sessions: [APIChatSession] = []
    @State private var currentSessionId: Int? = nil
    
    var body: some View {
        ZStack(alignment: .leading) {
            NavigationStack {
                VStack(spacing: 0) {
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
                    .scrollDismissesKeyboard(.interactively)
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
            .navigationTitle(currentSessionId == nil ? "New Chat" : "Coach Phoenix")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .navigationBarLeading) {
                    Button {
                        withAnimation(.spring()) {
                            showHistory.toggle()
                        }
                    } label: {
                        Image(systemName: "line.3.horizontal")
                            .foregroundStyle(DS.Colors.accent)
                    }
                }
                
                ToolbarItem(placement: .navigationBarTrailing) {
                    Button {
                        startNewChat()
                    } label: {
                        Image(systemName: "square.and.pencil")
                            .foregroundStyle(DS.Colors.accent)
                    }
                }
            }
            .background(DS.Colors.background)
            .task {
                await network.checkConnection()
                await loadSessions()
            }
        }
        
        // Slide out drawer
        if showHistory {
            Color.black.opacity(0.4)
                .ignoresSafeArea()
                .onTapGesture {
                    withAnimation(.spring()) {
                        showHistory = false
                    }
                }
            
            ChatHistoryDrawer(
                sessions: sessions,
                currentSessionId: currentSessionId,
                onSelect: { session in
                    loadSessionHistory(sessionId: session.id)
                    withAnimation(.spring()) {
                        showHistory = false
                    }
                }
            )
            .frame(width: 280)
            .transition(.move(edge: .leading))
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
    
    private var welcomeMessage: some View {
        VStack(spacing: 12) {
            Image(systemName: "flame.fill")
                .font(.system(size: 48))
                .foregroundStyle(DS.Colors.accent)
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
            UIImpactFeedbackGenerator(style: .light).impactOccurred()
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
        .accessibilityLabel("Ask coach: \(text)")
    }
    
    private func messageBubble(_ message: ChatMessage) -> some View {
        HStack {
            if message.role == .user { Spacer(minLength: 60) }
            
            VStack(alignment: message.role == .user ? .trailing : .leading, spacing: 4) {
                Text(message.content)
                    .font(.subheadline)
                    .padding(12)
                    .background(
                        message.role == .user 
                            ? AnyShapeStyle(DS.Colors.accent) 
                            : (message.isError ? AnyShapeStyle(DS.Colors.danger.opacity(0.2)) : AnyShapeStyle(Material.ultraThinMaterial))
                    )
                    .foregroundStyle(message.role == .user ? .black : DS.Colors.primaryText)
                    .clipShape(RoundedRectangle(cornerRadius: 16))
                
                if message.isError {
                    Button(action: {
                        if let lastUserMsg = messages.last(where: { $0.role == .user }) {
                            messages.removeAll { $0.id == message.id }
                            inputText = lastUserMsg.content
                            Task { await sendMessage() }
                        }
                    }) {
                        HStack(spacing: 4) {
                            Image(systemName: "arrow.clockwise")
                            Text("Retry")
                        }
                        .font(.caption.bold())
                        .foregroundStyle(DS.Colors.accent)
                    }
                    .padding(.leading, 8)
                }
                
                // Injury triage: the coach's prose is above, the actionable
                // change is here. Nothing is written until Confirm.
                if let proposal = message.proposal {
                    IssueProposalCard(
                        proposal: proposal,
                        onConfirm: { issue, choices in
                            Task { await applyIssue(issue: issue, choices: choices, messageId: message.id) }
                        },
                        onDismiss: {
                            if let idx = messages.firstIndex(where: { $0.id == message.id }) {
                                withAnimation(DS.Animation.normal) {
                                    messages[idx].proposal = nil
                                    messages[idx].proposalOutcome = "Not logged — plan unchanged."
                                }
                            }
                        }
                    )
                    .padding(.top, 4)
                }

                if let outcome = message.proposalOutcome {
                    HStack(spacing: 6) {
                        Image(systemName: "checkmark.circle.fill")
                            .font(.caption)
                        Text(outcome)
                            .font(.caption)
                    }
                    .foregroundStyle(DS.Colors.outline)
                    .padding(.top, 2)
                }

                Text(message.timestamp, style: .time)
                    .font(.caption2)
                    .foregroundStyle(DS.Colors.outline)
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
                            .fill(DS.Colors.accent)
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
            .background(DS.Colors.surface)
            .clipShape(RoundedRectangle(cornerRadius: 16))
            Spacer()
        }
    }
    
    private var inputBar: some View {
        HStack(spacing: 12) {
            TextField("Ask your coach...", text: $inputText, axis: .vertical)
                .textFieldStyle(.plain)
                .padding(12)
                .background(DS.Colors.surface)
                .clipShape(RoundedRectangle(cornerRadius: 20))
                .lineLimit(1...4)
                .focused($isInputFocused)
            
            Button {
                UIImpactFeedbackGenerator(style: .medium).impactOccurred()
                Task { await sendMessage() }
            } label: {
                Image(systemName: "arrow.up.circle.fill")
                    .font(.system(size: 32))
                    .foregroundStyle(inputText.isEmpty || isLoading ? .gray : DS.Colors.accent)
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
        
        do {
            let stream = try await network.sendChatStream(message: text, sessionId: currentSessionId)
            
            for try await event in stream {
                await MainActor.run {
                    switch event {
                    case .token(let token):
                        if !isStreaming {
                            isStreaming = true  // First token arrived — hide typing indicator
                        }
                        messages[coachMsgIndex].content += token
                    case .proposal(let proposal):
                        // Arrives after the last token — see issue_triage.py.
                        messages[coachMsgIndex].proposal = proposal
                    }
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
                messages[coachMsgIndex].content = "I hit a snag. Let's try that again."
                messages[coachMsgIndex].isError = true
                isLoading = false
                isStreaming = false
            }
        }
    }
    
    /// Commit a confirmed injury proposal, then tell the rest of the app the
    /// plan moved so Today and the Block Calendar don't show stale sessions.
    private func applyIssue(issue: ReportedIssue, choices: [String: String], messageId: UUID) async {
        do {
            let result = try await network.applyIssue(issue: issue, choices: choices)

            var parts: [String] = []
            if !result.swappedDays.isEmpty {
                parts.append("swapped \(result.swappedDays.joined(separator: ", "))")
            }
            if !result.restDays.isEmpty {
                parts.append("rested \(result.restDays.joined(separator: ", "))")
            }
            let detail = parts.isEmpty ? "plan updated" : parts.joined(separator: ", ")

            await MainActor.run {
                if let idx = messages.firstIndex(where: { $0.id == messageId }) {
                    withAnimation(DS.Animation.normal) {
                        messages[idx].proposal = nil
                        messages[idx].proposalOutcome = "Logged \(result.bodyPart) — \(detail)."
                    }
                }
                UINotificationFeedbackGenerator().notificationOccurred(.success)
                NotificationCenter.default.post(name: NSNotification.Name("PlanUpdated"), object: nil)
            }
        } catch {
            await MainActor.run {
                // Deliberately vague on whether the injury was written: the
                // backend logs it before it rebuilds the plan, so a failure here
                // can land on either side of that commit.
                messages.append(ChatMessage(
                    role: .coach,
                    content: "I couldn't finish updating your plan. Check Profile → Injuries to see whether it was logged, then try again.",
                    timestamp: Date(),
                    isError: true
                ))
            }
        }
    }

    // MARK: - Session Management
    
    private func loadSessions() async {
        do {
            let fetched = try await network.fetchChatSessions()
            await MainActor.run {
                self.sessions = fetched
            }
        } catch {
            print("Failed to load sessions: \(error)")
        }
    }
    
    private func loadSessionHistory(sessionId: Int) {
        currentSessionId = sessionId
        isLoading = true
        Task {
            do {
                let history = try await network.fetchChatSessionHistory(sessionId: sessionId)
                await MainActor.run {
                    self.messages = history.map { apiMsg in
                        ChatMessage(
                            role: apiMsg.role == "user" ? .user : .coach,
                            content: apiMsg.content,
                            timestamp: ISO8601DateFormatter().date(from: apiMsg.createdAt) ?? Date()
                        )
                    }
                    self.isLoading = false
                }
            } catch {
                print("Failed to load history: \(error)")
                await MainActor.run {
                    self.isLoading = false
                }
            }
        }
    }
    
    private func startNewChat() {
        currentSessionId = nil
        messages = []
        Task {
            await loadSessions()
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
