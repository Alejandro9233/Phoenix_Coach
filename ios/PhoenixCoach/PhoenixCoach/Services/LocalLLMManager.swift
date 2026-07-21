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
    
    private init() {}
    
    /// Loads the model from Hugging Face hub (downloads if necessary)
    func loadModel() async {
        guard !isModelLoaded else { return }
        
        isDownloading = true
        statusMessage = "Preparing to download model..."
        
        do {
            let config = ModelConfiguration(id: "mlx-community/Llama-3.2-1B-Instruct-4bit")
            
            let container = try await #huggingFaceLoadModelContainer(configuration: config) { progress in
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
                    
                    let fullPrompt = "SYSTEM INSTRUCTIONS: \(systemPrompt)\n\nUSER PROMPT: \(prompt)"
                    let stream = try await session.streamResponse(to: fullPrompt)
                    
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
        MLX.GPU.clearCache()
    }
}
