import Foundation
import Combine
import SwiftUI
import MLX
import MLXLLM
import MLXLMCommon
import MLXHuggingFace
import HuggingFace
import Tokenizers

/// Manages the local on-device MLX Large Language Model for coaching.
@MainActor
class LocalLLMManager: ObservableObject {
    static let shared = LocalLLMManager()
    
    @Published var isDownloading = false
    @Published var downloadProgress: Double = 0.0
    @Published var isModelLoaded = false
    @Published var statusMessage = "Ready"
    
    private var modelContainer: ModelContainer?
    
    struct HubBridge: MLXLMCommon.Downloader {
        private let upstream: HuggingFace.HubClient
        init(_ upstream: HuggingFace.HubClient) { self.upstream = upstream }
        public func download(id: String, revision: String?, matching patterns: [String], useLatest: Bool, progressHandler: @Sendable @escaping (Foundation.Progress) -> Void) async throws -> URL {
            guard let repoID = HuggingFace.Repo.ID(rawValue: id) else { throw HuggingFaceDownloaderError.invalidRepositoryID(id) }
            return try await upstream.downloadSnapshot(of: repoID, revision: revision ?? "main", matching: patterns, progressHandler: { @MainActor progress in progressHandler(progress) })
        }
    }

    struct TokenizerBridge: MLXLMCommon.Tokenizer {
        private let upstream: any Tokenizers.Tokenizer
        init(_ upstream: any Tokenizers.Tokenizer) { self.upstream = upstream }
        func encode(text: String, addSpecialTokens: Bool) -> [Int] { upstream.encode(text: text, addSpecialTokens: addSpecialTokens) }
        func decode(tokenIds: [Int], skipSpecialTokens: Bool) -> String { upstream.decode(tokens: tokenIds, skipSpecialTokens: skipSpecialTokens) }
        func convertTokenToId(_ token: String) -> Int? { upstream.convertTokenToId(token) }
        func convertIdToToken(_ id: Int) -> String? { upstream.convertIdToToken(id) }
        var bosToken: String? { upstream.bosToken }
        var eosToken: String? { upstream.eosToken }
        var unknownToken: String? { upstream.unknownToken }
        func applyChatTemplate(messages: [[String: any Sendable]], tools: [[String: any Sendable]]?, additionalContext: [String: any Sendable]?) throws -> [Int] {
            do { return try upstream.applyChatTemplate(messages: messages, tools: tools, additionalContext: additionalContext) } catch { throw MLXLMCommon.TokenizerError.missingChatTemplate }
        }
    }

    struct TransformersLoader: MLXLMCommon.TokenizerLoader {
        public init() {}
        public func load(from directory: URL) async throws -> any MLXLMCommon.Tokenizer {
            let upstream = try await Tokenizers.AutoTokenizer.from(modelFolder: directory)
            return TokenizerBridge(upstream)
        }
    }
    
    private init() {}
    
    /// Loads the model from Hugging Face hub (downloads if necessary)
    func loadModel() async {
        guard !isModelLoaded else { return }
        
        isDownloading = true
        statusMessage = "Preparing to download model..."
        
        do {
            let config = ModelConfiguration(id: "mlx-community/Llama-3.2-1B-Instruct-4bit")
            
            let container = try await LLMModelFactory.shared.loadContainer(
                from: HubBridge(HuggingFace.HubClient()),
                using: TransformersLoader(),
                configuration: config
            ) { progress in
                Task { @MainActor in
                    self.statusMessage = "Downloading: \(Int(progress.fractionCompleted * 100))%"
                    self.downloadProgress = progress.fractionCompleted
                }
            }
            
            self.modelContainer = container
            self.isModelLoaded = true
            self.isDownloading = false
            self.statusMessage = "Model Ready"
            
        } catch {
            self.isDownloading = false
            self.statusMessage = "Error loading model: \(error.localizedDescription)"
            print("MLX Load Error: \(error)")
        }
    }
    
    /// Generates a streaming response using the local model
    func generateStream(prompt: String, systemPrompt: String) -> AsyncThrowingStream<String, Error> {
        AsyncThrowingStream { continuation in
            Task {
                guard let container = self.modelContainer else {
                    continuation.finish(throwing: NSError(domain: "LLMManager", code: 1, userInfo: [NSLocalizedDescriptionKey: "Model not loaded"]))
                    return
                }
                
                do {
                    // ChatSession is the recommended 3.x way to stream
                    let session = ChatSession(container)
                    
                    let fullPrompt = """
                    <|begin_of_text|><|start_header_id|>system<|end_header_id|>

                    \(systemPrompt)<|eot_id|><|start_header_id|>user<|end_header_id|>

                    \(prompt)<|eot_id|><|start_header_id|>assistant<|end_header_id|>
                    
                    """
                    let stream = session.streamResponse(to: fullPrompt)
                    
                    for try await chunk in stream {
                        continuation.yield(chunk)
                    }
                    
                    continuation.finish()
                } catch {
                    continuation.finish(throwing: error)
                }
            }
        }
    }
    
    /// Unload the model to free up RAM when not in use
    func unloadModel() {
        self.modelContainer = nil
        self.isModelLoaded = false
        self.statusMessage = "Model Unloaded"
        MLX.Memory.clearCache()
    }
}
