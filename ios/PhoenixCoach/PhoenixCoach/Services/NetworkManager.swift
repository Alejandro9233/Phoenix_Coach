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
    
    /// Smart Refresh: scrape → ingest → evaluate recovery → auto-adapt if needed.
    /// Replaces the old pull-to-refresh + adapt-today flow with a single action.
    func smartRefresh() async throws -> SmartRefreshResponse {
        guard let url = URL(string: "\(baseURL)/smart-refresh") else {
            throw NetworkError.invalidURL
        }
        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        let (data, _) = try await session.data(for: request)
        return try decoder.decode(SmartRefreshResponse.self, from: data)
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
    
    /// Send a chat message and receive tokens as they stream from the LLM.
    /// Returns an AsyncThrowingStream that yields token strings as they arrive.
    func sendChatStream(message: String) -> AsyncThrowingStream<String, Error> {
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
                    let body = ["message": message]
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
                        
                        if let data = payload.data(using: .utf8),
                           let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
                           let token = json["token"] as? String {
                            continuation.yield(token)
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
    func sendChat(message: String) async throws -> String {
        guard let url = URL(string: "\(baseURL)/chat-sync") else {
            throw NetworkError.invalidURL
        }
        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        let body = ["message": message]
        request.httpBody = try JSONSerialization.data(withJSONObject: body)
        let (data, _) = try await session.data(for: request)
        if let json = try JSONSerialization.jsonObject(with: data) as? [String: Any],
           let reply = json["response"] as? String {
            return reply
        }
        return String(data: data, encoding: .utf8) ?? "No response"
    }
    
    // MARK: - Feedback
    
    func submitFeedback(_ feedback: FeedbackEntry) async throws {
        guard let url = URL(string: "\(baseURL)/feedback") else {
            throw NetworkError.invalidURL
        }
        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = try JSONEncoder().encode(feedback)
        let (_, response) = try await session.data(for: request)
        guard let http = response as? HTTPURLResponse, http.statusCode == 200 else {
            throw NetworkError.serverError
        }
    }
    
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
        var request = URLRequest(url: url)
        request.httpMethod = "PUT"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = try JSONEncoder().encode(profile)
        let (_, response) = try await session.data(for: request)
        guard let http = response as? HTTPURLResponse, http.statusCode == 200 else {
            throw NetworkError.serverError
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
