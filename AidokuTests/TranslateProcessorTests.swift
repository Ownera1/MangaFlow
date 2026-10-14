//
//  TranslateProcessorTests.swift
//  Aidoku
//

@testable import Aidoku
import Foundation
import Nuke
import Testing
import UIKit

@Suite(.serialized)
struct TranslateProcessorTests {
    static let png = Data(base64Encoded: "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=")

    private func session() -> URLSession {
        let config = URLSessionConfiguration.ephemeral
        config.protocolClasses = [TranslationURLProtocol.self]
        config.urlCache = nil
        config.requestCachePolicy = .reloadIgnoringLocalCacheData
        return URLSession(configuration: config)
    }

    private func processor(lang: String = "CHS", server: String = "https://translate.test", token: String = "secret") -> TranslateProcessor {
        TranslateProcessor(
            session: session(),
            config: TranslationConfig(serverURL: server, token: token, targetLang: lang)
        )
    }

    private func process(_ processor: TranslateProcessor) async throws -> UIImage {
        let data = try #require(Self.png)
        let image = try #require(UIImage(data: data))
        let container = ImageContainer(image: image)
        let request = ImageRequest(url: URL(string: "https://translate.test/page.png")!)
        let context = ImageProcessingContext(
            request: request,
            response: ImageResponse(container: container, request: request),
            isCompleted: true
        )
        // BlockingThrowingTask waits on a semaphore; run it off the cooperative pool.
        return try await withCheckedThrowingContinuation { continuation in
            DispatchQueue.global(qos: .userInitiated).async {
                do {
                    continuation.resume(returning: try processor.process(container, context: context).image)
                } catch {
                    continuation.resume(throwing: error)
                }
            }
        }
    }

    @Test("POST /translate with target_lang, bearer token, and a jpeg body")
    func successRequest() async throws {
        TranslationURLProtocol.reset(mode: .png)
        let image = try await process(processor())
        #expect(image.cgImage != nil)
        #expect(TranslationURLProtocol.requestCount == 1)
        let request = try #require(TranslationURLProtocol.lastRequest)
        #expect(request.method == "POST")
        #expect(request.path == "/translate")
        #expect(request.targetLang == "CHS")
        #expect(request.contentType == "image/jpeg")
        #expect(request.authorization == "Bearer secret")
    }

    @Test("HTTP 500 throws")
    func serverError() async {
        TranslationURLProtocol.reset(mode: .status(500))
        let error = await thrownError { try await process(processor()) }
        #expect(error as? TranslationError == .badStatus(500))
    }

    @Test("network errors throw")
    func networkError() async {
        TranslationURLProtocol.reset(mode: .network)
        let error = await thrownError { try await process(processor()) }
        #expect(error is URLError)
    }

    @Test("missing server or token throws before any request")
    func notConfigured() async {
        TranslationURLProtocol.reset(mode: .png)
        let error = await thrownError { try await process(processor(server: "", token: "")) }
        #expect(error as? TranslationError == .notConfigured)
        #expect(TranslationURLProtocol.requestCount == 0)
    }

    @Test("identifier changes with target_lang and server")
    func identifierChanges() {
        let simplified = processor()
        let traditional = processor(lang: "CHT")
        let otherServer = processor(server: "https://other.test")
        #expect(simplified.identifier != traditional.identifier)
        #expect(simplified.identifier.contains("lang=CHS"))
        #expect(traditional.identifier.contains("lang=CHT"))
        #expect(simplified.identifier.contains("https://translate.test"))
        #expect(simplified.identifier != otherServer.identifier)
    }

    private func thrownError<T>(_ body: () async throws -> T) async -> Error? {
        do {
            _ = try await body()
            return nil
        } catch {
            return error
        }
    }
}

private struct CapturedTranslationRequest: Sendable {
    var method: String?
    var path: String?
    var targetLang: String?
    var contentType: String?
    var authorization: String?
}

private final class TranslationURLProtocol: URLProtocol, @unchecked Sendable {
    enum Mode: Sendable {
        case png
        case status(Int)
        case network
    }

    private static let lock = NSLock()
    private static var mode: Mode = .png
    private static var _requestCount = 0
    private static var _lastRequest: CapturedTranslationRequest?

    static var requestCount: Int {
        lock.lock()
        defer { lock.unlock() }
        return _requestCount
    }

    static var lastRequest: CapturedTranslationRequest? {
        lock.lock()
        defer { lock.unlock() }
        return _lastRequest
    }

    static func reset(mode: Mode) {
        lock.lock()
        self.mode = mode
        _requestCount = 0
        _lastRequest = nil
        lock.unlock()
    }

    override class func canInit(with request: URLRequest) -> Bool {
        true
    }

    override class func canonicalRequest(for request: URLRequest) -> URLRequest {
        request
    }

    override func startLoading() {
        let components = request.url.flatMap { URLComponents(url: $0, resolvingAgainstBaseURL: false) }
        let captured = CapturedTranslationRequest(
            method: request.httpMethod,
            path: request.url?.path,
            targetLang: components?.queryItems?.first { $0.name == "target_lang" }?.value,
            contentType: request.value(forHTTPHeaderField: "Content-Type"),
            authorization: request.value(forHTTPHeaderField: "Authorization")
        )
        let mode: Mode = {
            Self.lock.lock()
            defer { Self.lock.unlock() }
            Self._requestCount += 1
            Self._lastRequest = captured
            return Self.mode
        }()

        switch mode {
            case .network:
                client?.urlProtocol(self, didFailWithError: URLError(.networkConnectionLost))
            case .png, .status:
                let status = if case let .status(code) = mode { code } else { 200 }
                let response = HTTPURLResponse(
                    url: request.url ?? URL(string: "https://translate.test/translate")!,
                    statusCode: status,
                    httpVersion: nil,
                    headerFields: ["Content-Type": "image/png"]
                )!
                client?.urlProtocol(self, didReceive: response, cacheStoragePolicy: .notAllowed)
                if case .png = mode, let data = TranslateProcessorTests.png {
                    client?.urlProtocol(self, didLoad: data)
                }
                client?.urlProtocolDidFinishLoading(self)
        }
    }

    override func stopLoading() {}
}
