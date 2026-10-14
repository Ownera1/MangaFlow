//
//  TranslateProcessor.swift
//  Aidoku
//

import Nuke
import UIKit

struct TranslationConfig: Sendable, Equatable {
    var serverURL: String
    var token: String
    var targetLang: String

    static func current() -> TranslationConfig? {
        let serverURL = UserDefaults.standard.string(forKey: "Translation.serverURL") ?? ""
        let storedLang = UserDefaults.standard.string(forKey: "Translation.targetLang") ?? ""
        let targetLang = storedLang.isEmpty ? "CHS" : storedLang
        guard !serverURL.isEmpty, let token = TranslationKeychain.token(), !token.isEmpty else {
            return nil
        }
        return TranslationConfig(serverURL: serverURL, token: token, targetLang: targetLang)
    }
}

enum TranslationError: Error, Equatable {
    case notConfigured
    case encodeFailed
    case badStatus(Int)
    case decodeFailed
}

struct TranslationClient: Sendable {
    var session: URLSession

    func upload(_ jpeg: Data, config: TranslationConfig) async throws -> Data {
        guard let url = Self.endpoint(serverURL: config.serverURL, targetLang: config.targetLang) else {
            throw TranslationError.notConfigured
        }
        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.timeoutInterval = 300
        request.setValue("image/jpeg", forHTTPHeaderField: "Content-Type")
        request.setValue("Bearer \(config.token)", forHTTPHeaderField: "Authorization")
        request.httpBody = jpeg

        let (data, response) = try await session.data(for: request)
        guard let http = response as? HTTPURLResponse else {
            throw TranslationError.badStatus(-1)
        }
        guard http.statusCode == 200 else {
            throw TranslationError.badStatus(http.statusCode)
        }
        return data
    }

    static func endpoint(serverURL: String, targetLang: String) -> URL? {
        let trimmed = serverURL.trimmingCharacters(in: .whitespacesAndNewlines)
        guard let base = URL(string: trimmed), !trimmed.isEmpty else { return nil }
        guard var components = URLComponents(url: base, resolvingAgainstBaseURL: false) else { return nil }
        components.path = base.appendingPathComponent("translate").path
        components.queryItems = [URLQueryItem(name: "target_lang", value: targetLang)]
        return components.url
    }
}

struct TranslateProcessor: ImageProcessing {
    var session: URLSession = .shared
    /// Non-nil in tests. Production reads UserDefaults and the keychain at process time.
    var config: TranslationConfig?

    var identifier: String {
        let server = config?.serverURL ?? UserDefaults.standard.string(forKey: "Translation.serverURL") ?? ""
        let storedLang = config?.targetLang ?? UserDefaults.standard.string(forKey: "Translation.targetLang") ?? ""
        let lang = storedLang.isEmpty ? "CHS" : storedLang
        return "com.github.Aidoku/Aidoku/translate?server=\(server)&lang=\(lang)"
    }

    func process(_ image: PlatformImage) -> PlatformImage? {
        nil
    }

    func process(_ container: ImageContainer, context _: ImageProcessingContext) throws -> ImageContainer {
        // ponytail: blocks one Nuke processing slot for the whole upload (up to 300s).
        // The queue is small; if page loads stall, run translation on its own pipeline.
        let image = try processImage(container.image)
        var container = container
        container.image = image
        return container
    }

    /// Throwing path for callers outside Nuke. On failure the caller keeps the original image.
    func processImage(_ image: UIImage) throws -> UIImage {
        try BlockingThrowingTask { try await self.translate(image) }.get()
    }

    func translate(_ image: UIImage) async throws -> UIImage {
        do {
            let config = try resolvedConfig()
            guard let jpeg = image.jpegData(compressionQuality: 0.92) else {
                throw TranslationError.encodeFailed
            }
            let data = try await TranslationClient(session: session).upload(jpeg, config: config)
            guard let decoded = UIImage(data: data), let cgImage = decoded.cgImage else {
                throw TranslationError.decodeFailed
            }
            // jpegData bakes orientation into the pixels. Reapplying a non-.up orientation rotates twice.
            // ponytail: manga pages are .up. Render upright before upload if a rotated page lands here.
            return UIImage(cgImage: cgImage, scale: image.scale, orientation: image.imageOrientation)
        } catch {
            LogManager.logger.error("Page translation failed: \(Self.safeDescription(of: error))")
            throw error
        }
    }

    static func fallbackRequest(after error: Error, request: ImageRequest) -> ImageRequest? {
        guard case let ImagePipeline.Error.processingFailed(processor, _, _) = error,
              processor is TranslateProcessor
        else { return nil }
        var request = request
        let count = request.processors.count
        request.processors.removeAll { $0 is TranslateProcessor }
        guard request.processors.count < count else { return nil }
        return request
    }

    private func resolvedConfig() throws -> TranslationConfig {
        if let config {
            guard !config.serverURL.isEmpty, !config.token.isEmpty else {
                throw TranslationError.notConfigured
            }
            return config
        }
        guard let config = TranslationConfig.current() else {
            throw TranslationError.notConfigured
        }
        return config
    }

    private static func safeDescription(of error: Error) -> String {
        if let error = error as? TranslationError {
            return String(describing: error)
        }
        if let urlError = error as? URLError {
            return "URLError \(urlError.code.rawValue)"
        }
        return String(describing: type(of: error))
    }
}
