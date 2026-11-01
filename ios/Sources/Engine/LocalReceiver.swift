import Foundation
import Network

/// A small HTTP receiver on this iPhone's own loopback address. The Shortcut
/// posts what it reads here, so messages go from Shortcuts to this app and
/// never leave the phone. It answers on 127.0.0.1 only, and every request must
/// carry the token this app handed the Shortcut for the current export.
final class LocalReceiver: @unchecked Sendable {
    static let port: UInt16 = 47821

    struct Request: Sendable {
        let path: String
        let headers: [String: String]
        let body: Data
        /// Where the body went, for a request that carries a file.
        let file: URL?

        var token: String? { headers["x-unbubble-token"] }
    }

    typealias Handler = @Sendable (Request) async -> Data

    private let queue = DispatchQueue(label: "me.justlay.unbubble.receiver")
    private var listener: NWListener?
    private var handler: Handler?

    func start(handler: @escaping Handler) {
        queue.async { [self] in
            self.handler = handler
            open()
        }
    }

    /// iOS can take a listener away while the app is suspended.
    func restart() {
        queue.async { [self] in
            listener?.cancel()
            listener = nil
            open()
        }
    }

    private func open() {
        guard listener == nil else { return }
        let parameters = NWParameters.tcp
        parameters.allowLocalEndpointReuse = true
        parameters.requiredLocalEndpoint = .hostPort(host: "127.0.0.1", port: NWEndpoint.Port(rawValue: Self.port)!)
        guard let created = try? NWListener(using: parameters) else { return }
        created.newConnectionHandler = { [weak self] connection in self?.accept(connection) }
        created.stateUpdateHandler = { [weak self, weak created] state in
            guard let self, case .failed = state else { return }
            // The port can take a moment to come free.
            self.queue.asyncAfter(deadline: .now() + 0.2) {
                guard let created, self.listener === created else { return }
                created.cancel()
                self.listener = nil
                self.open()
            }
        }
        created.start(queue: queue)
        listener = created
    }

    private func accept(_ connection: NWConnection) {
        connection.start(queue: queue)
        read(connection, Incoming())
    }

    private func read(_ connection: NWConnection, _ incoming: Incoming) {
        connection.receive(minimumIncompleteLength: 1, maximumLength: 1 << 20) { [weak self] data, _, finished, error in
            guard let self else { return }
            if let data { incoming.add(data) }
            if incoming.takeContinueRequest() {
                connection.send(content: Data("HTTP/1.1 100 Continue\r\n\r\n".utf8), completion: .idempotent)
            }
            if let request = incoming.request {
                self.respond(to: request, on: connection)
            } else if finished || error != nil || incoming.isBroken {
                incoming.discard()
                connection.cancel()
            } else {
                self.read(connection, incoming)
            }
        }
    }

    private func respond(to request: Request, on connection: NWConnection) {
        guard let handler else { connection.cancel(); return }
        Task {
            let body = await handler(request)
            var reply = Data("HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: \(body.count)\r\nConnection: close\r\n\r\n".utf8)
            reply.append(body)
            connection.send(content: reply, completion: .contentProcessed { _ in connection.cancel() })
        }
    }

    /// One request as it arrives. A file's body goes straight to disk, so a
    /// long video never sits in memory.
    private final class Incoming {
        private var buffer = Data()
        private var path: String?
        private var headers: [String: String] = [:]
        private var body = Data()
        private var sink: FileHandle?
        private var file: URL?
        private var left = 0
        private var chunked = false
        private var chunkLeft = 0
        private var chunkEnding = false
        private var wantsContinue = false
        private var complete = false
        private(set) var isBroken = false

        var request: Request? {
            guard complete, let path else { return nil }
            try? sink?.close()
            sink = nil
            return Request(path: path, headers: headers, body: body, file: file)
        }

        func takeContinueRequest() -> Bool {
            defer { wantsContinue = false }
            return wantsContinue
        }

        func add(_ data: Data) {
            buffer.append(data)
            if path == nil { readHead() }
            guard path != nil, !complete else { return }
            if chunked { readChunks() } else { readBody() }
        }

        func discard() {
            try? sink?.close()
            sink = nil
            if let file { try? FileManager.default.removeItem(at: file) }
        }

        private func readHead() {
            guard let end = buffer.range(of: Data("\r\n\r\n".utf8)) else { return }
            guard let head = String(data: buffer[buffer.startIndex..<end.lowerBound], encoding: .utf8) else { isBroken = true; return }
            let lines = head.components(separatedBy: "\r\n")
            let parts = lines.first?.components(separatedBy: " ") ?? []
            guard parts.count >= 2 else { isBroken = true; return }
            for line in lines.dropFirst() {
                let pair = line.split(separator: ":", maxSplits: 1).map { $0.trimmingCharacters(in: .whitespaces) }
                if pair.count == 2 { headers[pair[0].lowercased()] = pair[1] }
            }
            left = Int(headers["content-length"] ?? "") ?? 0
            chunked = headers["transfer-encoding"]?.lowercased().contains("chunked") == true
            wantsContinue = headers["expect"]?.lowercased().contains("100-continue") == true
            buffer = Data(buffer[end.upperBound...])
            if parts[1].hasSuffix("/file") {
                let url = FileManager.default.temporaryDirectory.appending(path: UUID().uuidString)
                FileManager.default.createFile(atPath: url.path, contents: nil)
                file = url
                sink = try? FileHandle(forWritingTo: url)
            }
            path = parts[1]
        }

        private func keep(_ data: Data) {
            if let sink { try? sink.write(contentsOf: data) } else { body.append(data) }
        }

        private func readBody() {
            let take = min(left, buffer.count)
            keep(buffer.prefix(take))
            left -= take
            buffer = Data(buffer.dropFirst(take))
            complete = left == 0
        }

        private func readChunks() {
            let lineEnd = Data("\r\n".utf8)
            while !complete {
                if chunkLeft > 0 {
                    let take = min(chunkLeft, buffer.count)
                    guard take > 0 else { return }
                    keep(buffer.prefix(take))
                    chunkLeft -= take
                    buffer = Data(buffer.dropFirst(take))
                    chunkEnding = chunkLeft == 0
                } else if chunkEnding {
                    guard buffer.count >= 2 else { return }
                    buffer = Data(buffer.dropFirst(2))
                    chunkEnding = false
                } else {
                    guard let end = buffer.range(of: lineEnd) else { return }
                    let line = String(decoding: buffer[buffer.startIndex..<end.lowerBound], as: UTF8.self)
                    guard let size = Int(line.split(separator: ";").first?.trimmingCharacters(in: .whitespaces) ?? "", radix: 16) else {
                        isBroken = true
                        return
                    }
                    buffer = Data(buffer[end.upperBound...])
                    if size == 0 { complete = true } else { chunkLeft = size }
                }
            }
        }
    }
}
