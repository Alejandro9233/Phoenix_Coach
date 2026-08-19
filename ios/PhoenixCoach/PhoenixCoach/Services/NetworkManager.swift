import Foundation
import Combine

/// Handles all communication with the Phoenix Coach backend.
/// Automatically discovers the Mac backend on the local network.
class NetworkManager: ObservableObject {
    static let shared = NetworkManager()
    
    /// The base URL for the backend API. Persisted in UserDefaults and supports simulator detection.
    @Published var baseURL: String {
        didSet {
            UserDefaults.standard.set(baseURL, forKey: "backend_base_url")
        }
    }
    @Published var isConnected: Bool = false
    @Published var isOllamaConnected: Bool = false
    
    private let session: URLSession
    private let decoder: JSONDecoder
    
    init() {
        let defaultURL = "https://phoenix-coach.onrender.com"
        
        let lastDefault = UserDefaults.standard.string(forKey: "last_default_url")
        if lastDefault != defaultURL {
            UserDefaults.standard.set(defaultURL, forKey: "last_default_url")
            UserDefaults.standard.removeObject(forKey: "backend_base_url")
        }
        
        let savedURL = UserDefaults.standard.string(forKey: "backend_base_url")
        self.baseURL = savedURL ?? defaultURL
        
        let config = URLSessionConfiguration.default
        config.timeoutIntervalForRequest = 180  // Streaming can take a while
        config.timeoutIntervalForResource = 300
        self.session = URLSession(configuration: config)
        self.decoder = JSONDecoder()
        
        Task {
            await checkConnection()
        }
    }
    
    /// Reset the base URL to its environment-appropriate default.
    func resetToDefaultURL() {
        let defaultURL = "https://phoenix-coach.onrender.com"
        self.baseURL = defaultURL
        Task {
            await checkConnection()
        }
    }
    
    // MARK: - Health Check
    
    func checkConnection() async {
        guard let url = URL(string: "\(baseURL)/health") else { return }
        do {
            let (data, response) = try await session.data(from: url)
            if let http = response as? HTTPURLResponse, http.statusCode == 200 {
                if let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any] {
                    let llmObj = json["llm"] as? [String: Any]
                    let llmStatus = llmObj?["status"] as? String
                    await MainActor.run {
                        isConnected = true
                        isOllamaConnected = llmStatus == "connected"
                    }
                    return
                }
            }
            await MainActor.run {
                isConnected = true
                isOllamaConnected = false
            }
        } catch {
            let defaultURL = "https://phoenix-coach.onrender.com"
            
            if baseURL != defaultURL, let fallbackUrl = URL(string: "\(defaultURL)/health") {
                do {
                    let (data, response) = try await session.data(from: fallbackUrl)
                    if let http = response as? HTTPURLResponse, http.statusCode == 200 {
                        if let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any] {
                            let llmObj = json["llm"] as? [String: Any]
                            let llmStatus = llmObj?["status"] as? String
                            await MainActor.run {
                                self.baseURL = defaultURL
                                isConnected = true
                                isOllamaConnected = llmStatus == "connected"
                            }
                            return
                        }
                    }
                } catch {
                    // Both failed
                }
            }
            
            await MainActor.run {
                isConnected = false
                isOllamaConnected = false
            }
        }
    }
    
    // MARK: - Coaching
    
    func fetchCoaching() async throws -> CoachingRecommendation {
        guard let url = URL(string: "\(baseURL)/coaching") else {
            throw NetworkError.invalidURL
        }
        let (data, _) = try await session.data(from: url)
        return try decoder.decode(CoachingRecommendation.self, from: data)
    }
    
    // MARK: - Pull to Refresh (Sync + Coaching)
    
    func pullToRefresh() async throws -> SyncResponse {
        guard let url = URL(string: "\(baseURL)/pull-to-refresh") else {
            throw NetworkError.invalidURL
        }
        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        let (data, _) = try await session.data(for: request)
        return try decoder.decode(SyncResponse.self, from: data)
    }
    
    /// Kicks off the deep refresh as a backend job and returns immediately.
    /// Joins the running job if one already exists. Poll smartRefreshStatus()
    /// until `state` is "done" or "error"; the job finishes server-side even
    /// if the app closes mid-scrape.
    func startSmartRefresh() async throws -> RefreshJobStatus {
        guard let url = URL(string: "\(baseURL)/smart-refresh/start") else {
            throw NetworkError.invalidURL
        }
        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        let (data, _) = try await session.data(for: request)
        return try decoder.decode(RefreshJobStatus.self, from: data)
    }

    func smartRefreshStatus() async throws -> RefreshJobStatus {
        guard let url = URL(string: "\(baseURL)/smart-refresh/status") else {
            throw NetworkError.invalidURL
        }
        let (data, _) = try await session.data(from: url)
        return try decoder.decode(RefreshJobStatus.self, from: data)
    }
    
    // MARK: - Dashboard
    
    private var cachedDashboardMemory: DashboardResponse? = nil
    private var lastDashboardFetch: Date? = nil
    
    func fetchDashboard(forceRefresh: Bool = false) async throws -> DashboardResponse {
        if !forceRefresh, let cached = cachedDashboardMemory, let lastFetch = lastDashboardFetch, Date().timeIntervalSince(lastFetch) < 300 {
            return cached
        }
        
        guard let url = URL(string: "\(baseURL)/dashboard") else {
            throw NetworkError.invalidURL
        }
        
        do {
            var request = URLRequest(url: url)
            request.timeoutInterval = 10 // Quicker timeout for offline fallback
            let (data, _) = try await session.data(for: request)
            UserDefaults.standard.set(data, forKey: "cached_dashboard")
            let response = try decoder.decode(DashboardResponse.self, from: data)
            self.cachedDashboardMemory = response
            self.lastDashboardFetch = Date()
            return response
        } catch {
            if let cachedData = UserDefaults.standard.data(forKey: "cached_dashboard"),
               let cachedResponse = try? decoder.decode(DashboardResponse.self, from: cachedData) {
                return cachedResponse
            }
            throw error
        }
    }
    
    // MARK: - Chat (Streaming via SSE)
    
    private struct ProposalEnvelope: Codable {
        let proposal: IssueProposal
    }

    private struct RecoveryEnvelope: Codable {
        let recovery: RecoveryProposal
    }

    private struct TravelEnvelope: Codable {
        let travel: TravelProposal
    }

    /// One item on the chat SSE stream.
    ///
    /// The backend can append an injury-triage, recovery, or travel proposal
    /// after the last token (see `issue_triage.py`), so the stream carries more
    /// than plain text.
    enum ChatStreamEvent {
        case token(String)
        case proposal(IssueProposal)
        case recovery(RecoveryProposal)
        case travel(TravelProposal)
    }

    /// Send a chat message and receive events as they stream from the LLM.
    func sendChatStream(message: String, sessionId: Int? = nil) -> AsyncThrowingStream<ChatStreamEvent, Error> {
        AsyncThrowingStream { continuation in
            Task {
                do {
                    guard let url = URL(string: "\(baseURL)/chat") else {
                        continuation.finish(throwing: NetworkError.invalidURL)
                        return
                    }
                    
                    var request = URLRequest(url: url)
                    request.httpMethod = "POST"
                    request.setValue("application/json", forHTTPHeaderField: "Content-Type")
                    request.setValue("text/event-stream", forHTTPHeaderField: "Accept")
                    var body: [String: Any] = ["message": message]
                    if let sid = sessionId {
                        body["session_id"] = sid
                    }
                    request.httpBody = try JSONSerialization.data(withJSONObject: body)
                    
                    let (bytes, response) = try await session.bytes(for: request)
                    
                    guard let http = response as? HTTPURLResponse, http.statusCode == 200 else {
                        continuation.finish(throwing: NetworkError.serverError)
                        return
                    }
                    
                    for try await line in bytes.lines {
                        // SSE format: "data: {json}" or "data: [DONE]"
                        guard line.hasPrefix("data: ") else { continue }
                        let payload = String(line.dropFirst(6))

                        if payload == "[DONE]" {
                            break
                        }

                        guard let data = payload.data(using: .utf8) else { continue }

                        if let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
                           let token = json["token"] as? String {
                            continuation.yield(.token(token))
                            continue
                        }

                        // An injury-triage or recovery proposal, sent after the
                        // last token. Decoding failure is non-fatal: the athlete
                        // still has the coach's written reply, just no card.
                        if let wrapper = try? JSONDecoder().decode(ProposalEnvelope.self, from: data) {
                            continuation.yield(.proposal(wrapper.proposal))
                        } else if let wrapper = try? JSONDecoder().decode(RecoveryEnvelope.self, from: data) {
                            continuation.yield(.recovery(wrapper.recovery))
                        } else if let wrapper = try? JSONDecoder().decode(TravelEnvelope.self, from: data) {
                            continuation.yield(.travel(wrapper.travel))
                        }
                    }
                    
                    continuation.finish()
                    
                } catch {
                    continuation.finish(throwing: error)
                }
            }
        }
    }
    
    /// Synchronous chat fallback — returns the full response at once.
    func sendChat(message: String, sessionId: Int? = nil) async throws -> String {
        guard let url = URL(string: "\(baseURL)/chat-sync") else {
            throw NetworkError.invalidURL
        }
        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        var body: [String: Any] = ["message": message]
        if let sid = sessionId {
            body["session_id"] = sid
        }
        request.httpBody = try JSONSerialization.data(withJSONObject: body)
        let (data, _) = try await session.data(for: request)
        if let json = try JSONSerialization.jsonObject(with: data) as? [String: Any],
           let reply = json["response"] as? String {
            return reply
        }
        return String(data: data, encoding: .utf8) ?? "No response"
    }
    
    // MARK: - Chat Sessions
    
    func fetchChatSessions() async throws -> [APIChatSession] {
        guard let url = URL(string: "\(baseURL)/chat/sessions") else {
            throw NetworkError.invalidURL
        }
        let (data, response) = try await session.data(from: url)
        guard let http = response as? HTTPURLResponse, http.statusCode == 200 else {
            throw NetworkError.serverError
        }
        return try decoder.decode([APIChatSession].self, from: data)
    }
    
    func fetchChatSessionHistory(sessionId: Int) async throws -> [APIChatMessage] {
        guard let url = URL(string: "\(baseURL)/chat/sessions/\(sessionId)") else {
            throw NetworkError.invalidURL
        }
        let (data, response) = try await session.data(from: url)
        guard let http = response as? HTTPURLResponse, http.statusCode == 200 else {
            throw NetworkError.serverError
        }
        return try decoder.decode([APIChatMessage].self, from: data)
    }
    
    // MARK: - Feedback
    
    
    // MARK: - Injuries
    
    func fetchInjuries() async throws -> [Injury] {
        guard let url = URL(string: "\(baseURL)/athlete/injuries") else {
            throw NetworkError.invalidURL
        }
        let (data, response) = try await session.data(from: url)
        guard let http = response as? HTTPURLResponse, http.statusCode == 200 else {
            throw NetworkError.serverError
        }
        return try decoder.decode([Injury].self, from: data)
    }
    
    func addInjury(_ injury: Injury) async throws {
        guard let url = URL(string: "\(baseURL)/athlete/injuries") else {
            throw NetworkError.invalidURL
        }
        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = try JSONEncoder().encode(injury)
        let (_, response) = try await session.data(for: request)
        guard let http = response as? HTTPURLResponse, http.statusCode == 200 else {
            throw NetworkError.serverError
        }
    }
    
    func updateInjury(_ injury: Injury) async throws {
        guard let id = injury.id, let url = URL(string: "\(baseURL)/athlete/injuries/\(id)") else {
            throw NetworkError.invalidURL
        }
        var request = URLRequest(url: url)
        request.httpMethod = "PUT"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = try JSONEncoder().encode(injury)
        let (_, response) = try await session.data(for: request)
        guard let http = response as? HTTPURLResponse, http.statusCode == 200 else {
            throw NetworkError.serverError
        }
    }
    
    // MARK: - Activity Analysis
    
    func fetchActivityAnalysis(activityID: String) async throws -> ActivityAnalysis {
        guard let url = URL(string: "\(baseURL)/activity/\(activityID)/analysis") else {
            throw NetworkError.invalidURL
        }
        let (data, _) = try await session.data(from: url)
        return try decoder.decode(ActivityAnalysis.self, from: data)
    }
    
    // MARK: - Athlete Profile
    
    func fetchAthleteProfile() async throws -> AthleteProfile {
        guard let url = URL(string: "\(baseURL)/athlete/profile") else {
            throw NetworkError.invalidURL
        }
        let (data, _) = try await session.data(from: url)
        return try decoder.decode(AthleteProfile.self, from: data)
    }
    
    func updateAthleteProfile(_ profile: AthleteProfile) async throws {
        guard let url = URL(string: "\(baseURL)/athlete/profile") else {
            throw NetworkError.invalidURL
        }
        // Always stamp the device's current timezone so the backend knows what
        // "today" means for this athlete, wherever they happen to be.
        var profile = profile
        profile.timezone = TimeZone.current.identifier

        var request = URLRequest(url: url)
        request.httpMethod = "PUT"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = try JSONEncoder().encode(profile)
        let (_, response) = try await session.data(for: request)
        guard let http = response as? HTTPURLResponse, http.statusCode == 200 else {
            throw NetworkError.serverError
        }
    }

    /// Push just the device timezone. Called on launch and whenever the app comes
    /// back to the foreground, so flying somewhere fixes dates without any user action.
    /// Silent by design — never surface a failure for this.
    func syncDeviceTimezone() async {
        let identifier = TimeZone.current.identifier
        guard UserDefaults.standard.string(forKey: "last_synced_timezone") != identifier else { return }
        guard let url = URL(string: "\(baseURL)/athlete/profile") else { return }

        var request = URLRequest(url: url)
        request.httpMethod = "PUT"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = try? JSONSerialization.data(withJSONObject: ["timezone": identifier])
        request.timeoutInterval = 15

        if let (_, response) = try? await session.data(for: request),
           let http = response as? HTTPURLResponse, http.statusCode == 200 {
            UserDefaults.standard.set(identifier, forKey: "last_synced_timezone")
        }
    }
    
    // MARK: - Weekly Plan
    
    func fetchWeeklyPlan() async throws -> WeeklyPlanResponse {
        guard let url = URL(string: "\(baseURL)/weekly-plan") else {
            throw NetworkError.invalidURL
        }
        do {
            var request = URLRequest(url: url)
            request.timeoutInterval = 10
            let (data, _) = try await session.data(for: request)
            UserDefaults.standard.set(data, forKey: "cached_weekly_plan")
            return try decoder.decode(WeeklyPlanResponse.self, from: data)
        } catch {
            if let cachedData = UserDefaults.standard.data(forKey: "cached_weekly_plan"),
               let cachedResponse = try? decoder.decode(WeeklyPlanResponse.self, from: cachedData) {
                return cachedResponse
            }
            throw error
        }
    }
    
    func adaptTodayWorkout() async throws -> DayPlan {
        guard let url = URL(string: "\(baseURL)/weekly-plan/adapt-today") else {
            throw NetworkError.invalidURL
        }
        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        let (data, response) = try await session.data(for: request)
        guard let http = response as? HTTPURLResponse, http.statusCode == 200 else {
            throw NetworkError.serverError
        }
        return try decoder.decode(DayPlan.self, from: data)
    }
    
    func fetchWeeklyPlanStatus() async throws -> WeeklyPlanStatusResponse {
        guard let url = URL(string: "\(baseURL)/weekly-plan/status") else {
            throw NetworkError.invalidURL
        }
        let (data, _) = try await session.data(from: url)
        return try decoder.decode(WeeklyPlanStatusResponse.self, from: data)
    }
    
    func regenerateWeeklyPlan() async throws -> WeeklyPlanResponse {
        guard let url = URL(string: "\(baseURL)/weekly-plan/regenerate") else {
            throw NetworkError.invalidURL
        }
        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        let (data, response) = try await session.data(for: request)
        guard let http = response as? HTTPURLResponse, http.statusCode == 200 else {
            throw NetworkError.serverError
        }
        return try decoder.decode(WeeklyPlanResponse.self, from: data)
    }
    
    struct ReplanResponseWrapper: Codable {
        let plan: WeeklyPlanResponse
    }
    
    func replanRemainingWeek() async throws -> WeeklyPlanResponse {
        guard let url = URL(string: "\(baseURL)/weekly-plan/replan-remaining") else {
            throw NetworkError.invalidURL
        }
        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        let (data, response) = try await session.data(for: request)
        guard let http = response as? HTTPURLResponse, http.statusCode == 200 else {
            throw NetworkError.serverError
        }
        let wrapper = try decoder.decode(ReplanResponseWrapper.self, from: data)
        return wrapper.plan
    }
    
    // MARK: - Injury / soreness triage

    /// Re-run a proposal after the athlete edits severity or duration on the card.
    /// Read-only — the plan is untouched until `applyIssue`.
    func previewIssue(issue: ReportedIssue) async throws -> IssueProposal? {
        guard let url = URL(string: "\(baseURL)/coach/issue/preview") else {
            throw NetworkError.invalidURL
        }
        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = try JSONEncoder().encode(IssuePreviewRequest(message: issue.notes ?? "", issue: issue))

        let (data, response) = try await session.data(for: request)
        guard let http = response as? HTTPURLResponse, http.statusCode == 200 else {
            throw NetworkError.serverError
        }
        return try decoder.decode(IssuePreviewResponse.self, from: data).proposal
    }

    /// Commit the confirmed issue. This is the call that writes the injury and
    /// rewrites the affected days.
    func applyIssue(issue: ReportedIssue, choices: [String: String]) async throws -> IssueApplyResult {
        guard let url = URL(string: "\(baseURL)/coach/issue/apply") else {
            throw NetworkError.invalidURL
        }
        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = try JSONEncoder().encode(IssueApplyRequest(issue: issue, choices: choices))

        let (data, response) = try await session.data(for: request)
        guard let http = response as? HTTPURLResponse, http.statusCode == 200 else {
            throw NetworkError.serverError
        }
        return try decoder.decode(IssueApplyResult.self, from: data)
    }

    private struct IssuePreviewRequest: Codable {
        let message: String
        let issue: ReportedIssue
    }

    private struct IssuePreviewResponse: Codable {
        let detected: Bool
        let proposal: IssueProposal?
    }

    private struct IssueApplyRequest: Codable {
        let issue: ReportedIssue
        let choices: [String: String]
    }

    /// Resolve a recovered injury and rebuild the remaining days it turned
    /// into rest. The resolve always sticks; a failed rebuild comes back as
    /// `rebuildError` with the plan untouched.
    func applyRecovery(injuryId: Int, rebuild: Bool = true) async throws -> RecoveryApplyResult {
        guard let url = URL(string: "\(baseURL)/coach/recovery/apply") else {
            throw NetworkError.invalidURL
        }
        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = try JSONSerialization.data(withJSONObject: ["injury_id": injuryId, "rebuild": rebuild])

        let (data, response) = try await session.data(for: request)
        guard let http = response as? HTTPURLResponse, http.statusCode == 200 else {
            throw NetworkError.serverError
        }
        return try decoder.decode(RecoveryApplyResult.self, from: data)
    }

    /// Rest confirmed travel days and rebuild the open remainder of the week.
    /// The block always sticks; a failed rebuild comes back as `rebuildError`.
    func applyTravel(dates: [String], note: String = "") async throws -> TravelApplyResult {
        guard let url = URL(string: "\(baseURL)/coach/travel/apply") else {
            throw NetworkError.invalidURL
        }
        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = try JSONSerialization.data(withJSONObject: ["dates": dates, "note": note])

        let (data, response) = try await session.data(for: request)
        guard let http = response as? HTTPURLResponse, http.statusCode == 200 else {
            throw NetworkError.serverError
        }
        return try decoder.decode(TravelApplyResult.self, from: data)
    }

    // MARK: - Training Context

    func fetchTrainingContext() async throws -> TrainingContext {
        guard let url = URL(string: "\(baseURL)/training-context") else {
            throw NetworkError.invalidURL
        }
        let (data, _) = try await session.data(from: url)
        return try decoder.decode(TrainingContext.self, from: data)
    }
    
    func fetchBlockCalendar() async throws -> BlockCalendarResponse {
        guard let url = URL(string: "\(baseURL)/block-calendar") else {
            throw NetworkError.invalidURL
        }
        let (data, _) = try await session.data(from: url)
        return try decoder.decode(BlockCalendarResponse.self, from: data)
    }
}

enum NetworkError: LocalizedError {
    case invalidURL
    case serverError
    case decodingError
    case ollamaOffline
    
    var errorDescription: String? {
        switch self {
        case .invalidURL: return "Invalid server URL"
        case .serverError: return "Server error"
        case .decodingError: return "Failed to parse response"
        case .ollamaOffline: return "Ollama is not running on your Mac"
        }
    }
}
