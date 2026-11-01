import Foundation
import Observation
import SwiftUI
import UIKit
import CryptoKit

/// Runs an export: starts the Shortcut, steers it window by window, keeps the
/// chosen chat and drops everything else on arrival. The Shortcut stays a plain
/// loop; all the tuning is here.
@Observable
@MainActor
final class ExportEngine {

    enum Phase: Equatable { case idle, opening, reading, paused, finished }

    private enum Window {
        static let aim = 300.0
        static let full = 450
        /// iOS never hands back more than about this many from one Find.
        static let ceiling = 500
        static let minMs = 20.0 * 60 * 1000
        static let maxMs = 60.0 * 864e5
        static let quietStopMs = 365.0 * 864e5
        static let floorMs = 1_167_609_600_000.0 // 2007
        static let hopCeiling = 4000
        static let hopFloor = 800
        /// The Shortcut's loop ends after 400 passes, so a run hands over first.
        static let passes = 350
        static let fileBytes = 200 << 20
        static let fileWindows = 150
        static let fileTries = 3
    }

    static let quietLimit: TimeInterval = 90
    static let shortcutName = "Unbubble Export"
    static let greyMirrorLink = URL(string: "https://apps.apple.com/app/id6799236359")!
    static let shortcutLink = URL(string: "https://www.icloud.com/shortcuts/0e0ba46174fc41ab8ba55d1cb21ead01")!

    private(set) var phase: Phase = .idle
    private(set) var progress = ExportProgress()
    private(set) var lastDelivery = Date.distantPast
    var errorLine: String?
    var shortcutAdded: Bool {
        didSet { UserDefaults.standard.set(shortcutAdded, forKey: "shortcutAdded") }
    }

    private let receiver = LocalReceiver()
    private let run = BackgroundRun()
    /// How many of each message are stored, by content. A window can repeat a
    /// message from the one before; two messages alike inside one window really
    /// were sent twice, and both stay.
    private var stored: [SHA256Digest: Int] = [:]
    private var watcher: Task<Void, Never>?
    private var restartedAt: Int?
    private var background: UIBackgroundTaskIdentifier = .invalid
    private var cameBack = false
    private var startsOnReturn = false
    private var stream: FileHandle?

    /// Out of the phone's backups: an export is the reader's until they share it.
    nonisolated static let folder: URL = {
        var url = URL.applicationSupportDirectory.appending(path: "export", directoryHint: .isDirectory)
        try? FileManager.default.createDirectory(at: url, withIntermediateDirectories: true)
        var values = URLResourceValues()
        values.isExcludedFromBackup = true
        try? url.setResourceValues(values)
        return url
    }()
    private static var recordsURL: URL { folder.appending(path: "messages.jsonl") }
    private static var savedFilesURL: URL { folder.appending(path: "attachments.jsonl") }
    private static var progressURL: URL { folder.appending(path: "progress.json") }
    nonisolated static var attachmentsFolder: URL { folder.appending(path: "attachments", directoryHint: .isDirectory) }

    var sides: Sides { Sides(labels: progress.labels.keys, recipients: progress.recipients, chat: progress.chat) }
    var messageCount: Int { progress.labels.values.reduce(0, +) }
    var mine: Int { count(of: .me) }
    var theirs: Int { count(of: .them) }
    var unknown: Int { count(of: .unknown) }
    var busiestDay: Int { progress.days.values.max() ?? 0 }
    var activeDays: Int { progress.days.count }
    var hasExport: Bool { messageCount > 0 }
    var reachedBack: Date? { progress.endMs > 0 && progress.read > 0 ? Date(timeIntervalSince1970: progress.endMs / 1000) : nil }
    var isFetchingFiles: Bool { progress.files.map { !$0.isDone } ?? false }

    private func count(of side: Sides.Side) -> Int {
        let sides = sides
        return progress.labels.reduce(0) { $0 + (sides.side(of: $1.key) == side ? $1.value : 0) }
    }

    init() {
        shortcutAdded = UserDefaults.standard.bool(forKey: "shortcutAdded")
        if let data = try? Data(contentsOf: Self.progressURL),
           let saved = try? JSONDecoder().decode(ExportProgress.self, from: data) {
            progress = saved
            phase = saved.finished && !isFetchingFiles ? .finished : (saved.read > 0 ? .paused : .idle)
        }
        receiver.start { request in await self.handle(request) }
        run.onExpired = { [weak self] in self?.runStopped() }
        #if DEBUG
        // Drives the receiver with no Shortcut behind it: scripted requests in
        // the Simulator, which shares this Mac's loopback address.
        let arguments = ProcessInfo.processInfo.arguments
        if let index = arguments.firstIndex(of: "-ub-token"), index + 1 < arguments.count {
            progress.token = arguments[index + 1]
            phase = .opening
            watch()
        }
        if arguments.contains("-ub-files") {
            startFilesForTest()
        }
        if arguments.contains("-ub-write-files") { writeTestFiles() }
        #endif
    }

    // MARK: Starting

    func addShortcut() async {
        shortcutAdded = true
        await UIApplication.shared.open(Self.shortcutLink)
    }

    /// The first export needs the Shortcut, and only the reader can add it, so
    /// this opens Apple's add screen and carries on when they come back.
    func start() async {
        guard shortcutAdded else {
            startsOnReturn = true
            await addShortcut()
            return
        }
        clear()
        progress.token = UUID().uuidString
        await launch()
    }

    func resume() async {
        await launch()
    }

    /// Goes back through the chat for its photos and files. A separate run,
    /// because most exports don't want them and the text is read much faster
    /// without them.
    func fetchFiles() async {
        guard let messages = try? readMessages(), !messages.isEmpty else { return }
        progress.files = FilePass.plan(times: messages.reversed().map(\.time))
        progress.token = UUID().uuidString
        save()
        await launch()
    }

    /// Ends a run of attachments, whole or cut short, keeping what has arrived.
    func stopFiles() {
        if var pass = progress.files {
            pass.stoppedEarly = !pass.isDone
            pass.index = pass.windows.count
            progress.files = pass
        }
        progress.token = ""
        phase = .finished
        run.finish()
        updateIdleTimer()
        save()
    }

    private func launch() async {
        guard !progress.token.isEmpty else { return }
        errorLine = nil
        phase = .opening
        receiver.restart()
        run.start(title: progress.chat.isEmpty ? "Exporting messages" : "Exporting \(progress.chat)")
        var input = progress.token
        if !progress.chat.isEmpty { input += "\n" + progress.chat.replacingOccurrences(of: "\n", with: " ") }
        guard let link = Self.runLink(input: input) else { return }
        guard UIApplication.shared.canOpenURL(link) else {
            errorLine = "Shortcuts isn't on this iPhone. Install it from the App Store, then try again."
            phase = .idle
            return
        }
        // iOS only opens another app for one that's on screen.
        if await UIApplication.shared.open(link) == false { phase = .paused }
        watch()
    }

    /// `input` reaches the Shortcut as text: the token, then the chat if there is one.
    private static func runLink(input: String) -> URL? {
        var link = URLComponents()
        link.scheme = "shortcuts"
        link.host = "run-shortcut"
        link.queryItems = [URLQueryItem(name: "name", value: shortcutName), URLQueryItem(name: "input", value: "text"),
                           URLQueryItem(name: "text", value: input)]
        // A plus left bare could be read as a space, and a phone number can start with one.
        link.percentEncodedQuery = link.percentEncodedQuery?.replacingOccurrences(of: "+", with: "%2B")
        return link.url
    }

    /// Stops reading and keeps what has arrived. A run still going is told so
    /// at its next request, by the token no longer matching.
    func finishNow() {
        progress.finished = true
        progress.token = ""
        phase = .finished
        run.finish()
        updateIdleTimer()
        try? stream?.synchronize()
        save()
    }

    func clear() {
        watcher?.cancel()
        run.finish()
        try? stream?.close()
        stream = nil
        for url in [Self.recordsURL, Self.savedFilesURL, Self.progressURL, Self.attachmentsFolder] {
            try? FileManager.default.removeItem(at: url)
        }
        let temporary = URL.temporaryDirectory
        for name in (try? FileManager.default.contentsOfDirectory(atPath: temporary.path)) ?? [] where name.hasPrefix("Unbubble - ") {
            try? FileManager.default.removeItem(at: temporary.appending(path: name))
        }
        stored.removeAll()
        progress = ExportProgress()
        phase = .idle
        restartedAt = nil
        errorLine = nil
        updateIdleTimer()
    }

    private func updateIdleTimer() {
        UIApplication.shared.isIdleTimerDisabled = UIApplication.shared.applicationState == .active
            && (phase == .opening || phase == .reading)
    }

    func sceneChanged(to scene: ScenePhase) {
        switch scene {
        case .background:
            // Shortcuts flashes up at each restart; stay able to answer through it.
            cameBack = true
            if background == .invalid {
                background = UIApplication.shared.beginBackgroundTask { [weak self] in self?.endBackground() }
            }
        case .active:
            endBackground()
            if cameBack {
                receiver.restart()
                if startsOnReturn {
                    startsOnReturn = false
                    Task { await start() }
                }
            }
            cameBack = false
        default: break
        }
        updateIdleTimer()
    }

    private func endBackground() {
        if background != .invalid { UIApplication.shared.endBackgroundTask(background) }
        background = .invalid
    }

    private func runStopped() {
        guard phase == .reading || phase == .opening else { return }
        phase = .paused
        save()
    }

    /// A run iOS ended leaves nothing to restart it, so this screen does, once
    /// per stopping point.
    private func watch() {
        watcher?.cancel()
        watcher = Task { [weak self] in
            while !Task.isCancelled {
                try? await Task.sleep(for: .seconds(5))
                guard let self else { return }
                guard self.phase == .reading,
                      Date().timeIntervalSince(self.lastDelivery) > Self.quietLimit else { continue }
                self.phase = .paused
                self.save()
                let place = self.progress.read + (self.progress.files?.index ?? 0)
                if UIApplication.shared.applicationState == .active, self.restartedAt != place {
                    self.restartedAt = place
                    await self.launch()
                }
            }
        }
    }

    // MARK: Requests from the Shortcut

    nonisolated private func handle(_ request: LocalReceiver.Request) async -> Data {
        await receive(request)
    }

    private func receive(_ request: LocalReceiver.Request) -> Data {
        let path = request.path
        guard request.token == progress.token, !progress.token.isEmpty else {
            // Not this export: nothing is kept, and the run is walked to its end.
            if let file = request.file { try? FileManager.default.removeItem(at: file) }
            return Self.json(path.hasSuffix("/finalize") ? Self.done : ["next": "finalize", "hop": 0])
        }
        lastDelivery = Date()
        if path.hasSuffix("/file") { return saveFile(request) }
        let body = (try? JSONSerialization.jsonObject(with: request.body)) as? [String: Any] ?? [:]
        switch path {
        case let path where path.hasSuffix("/begin"): return begin(body)
        case let path where path.hasSuffix("/batch"): return batch(body)
        case let path where path.hasSuffix("/files"): return windowFinished(body)
        case let path where path.hasSuffix("/finalize"):
            if progress.files != nil { stopFiles() } else { finishNow() }
            return Self.json(Self.done)
        default: return Self.json(["error": "Unknown request."])
        }
    }

    private static let done: [String: Any] = ["next": "", "returnUrl": "unbubble://done"]

    private func begin(_ body: [String: Any]) -> Data {
        let name = (body["conversationName"] as? String) ?? ""
        if progress.chat.isEmpty {
            progress.chat = name
            progress.recipients = ((body["recipients"] as? String) ?? "").components(separatedBy: .newlines)
                .map { $0.trimmingCharacters(in: .whitespaces) }.filter { !$0.isEmpty }
        }
        phase = .reading
        updateIdleTimer()
        if var pass = progress.files {
            pass.tries += 1
            if pass.tries > Window.fileTries {
                pass.index += 1
                pass.skipped += 1
                pass.tries = 1
            }
            pass.runBytes = 0
            pass.runWindows = 0
            progress.files = pass
            save()
            return Self.json(fileWindow(pass))
        }
        if progress.endMs == 0 {
            let last = Self.date(body["lastActive"] as? String) ?? Date()
            progress.endMs = (last.timeIntervalSince1970 + 2 * 86400) * 1000
        }
        if progress.cleanHop { progress.hopTarget = min(Window.hopCeiling, Int(Double(progress.hopTarget) * 1.25)) }
        else if progress.runRead > 0 { progress.hopTarget = max(Window.hopFloor, min(progress.hopTarget, progress.runRead) / 2) }
        progress.runRead = 0
        progress.runPasses = 0
        progress.cleanHop = false
        save()
        var reply = plan()
        reply["hop"] = 0
        return Self.json(reply)
    }

    private func batch(_ body: [String: Any]) -> Data {
        let n = Int("\(body["n"] ?? 0)") ?? 0
        progress.runRead += n
        progress.runPasses += 1
        phase = .reading
        func list(_ key: String) -> [String] { String(describing: body[key] ?? "").components(separatedBy: "\u{241E}") }
        let times = list("t"), senders = list("s"), bodies = list("b"), kinds = list("ty"), chats = list("c")
        let aligned = n == 0 || (times.count == n && senders.count == n && bodies.count == n && kinds.count == n && chats.count == n)
        let room = progress.spanMs > Window.minMs
        if (n >= Window.full || !aligned) && room {
            progress.density = max(progress.density, Double(Window.full) / progress.spanMs)
            progress.spanMs = max(Window.minMs, (progress.spanMs / 3).rounded())
            save()
            return Self.json(next())
        }
        progress.read += n
        if aligned && n > 0 { keep(times, senders, bodies, kinds, chats) }
        if !aligned { progress.unread += n }
        if n >= Window.ceiling { progress.capped += 1 }
        progress.endMs -= progress.spanMs
        progress.emptyMs = n == 0 ? progress.emptyMs + progress.spanMs : 0
        let density = Double(max(n, 30)) / progress.spanMs
        progress.density = progress.density > 0 ? (progress.density + density) / 2 : density
        let scaled = Window.aim / progress.density
        progress.spanMs = min(Window.maxMs, max(Window.minMs, min(progress.spanMs * 1.5, max(progress.spanMs / 2, scaled)))).rounded()
        report()
        save()
        return Self.json(next())
    }

    private func report() {
        let top = progress.newest.map { $0 * 1000 } ?? progress.endMs
        let reached = 1 - (progress.endMs - Window.floorMs) / max(top - Window.floorMs, 1)
        var subtitle = "\(messageCount.formatted()) messages"
        if let back = reachedBack { subtitle += " · back to \(back.formatted(.dateTime.month(.abbreviated).year()))" }
        run.report(fraction: reached, title: "Exporting \(progress.chat)", subtitle: subtitle)
    }

    private func next() -> [String: Any] {
        if progress.emptyMs >= Window.quietStopMs || progress.endMs < Window.floorMs {
            return ["next": "finalize", "hop": 0]
        }
        var reply = plan()
        let hop = progress.runRead >= progress.hopTarget || progress.runPasses >= Window.passes
        if hop { progress.cleanHop = true; save() }
        reply["hop"] = hop ? 1 : 0
        return reply
    }

    private func plan() -> [String: Any] {
        ["next": "batch", "wf": Self.stamp(progress.endMs - progress.spanMs), "wt": Self.stamp(progress.endMs)]
    }

    /// Only the chosen chat goes further. Everything else ends here, unwritten.
    private func keep(_ times: [String], _ senders: [String], _ bodies: [String], _ kinds: [String], _ chats: [String]) {
        let chosen = Sides.normalized(progress.chat)
        openStream()
        loadStored()
        let encoder = JSONEncoder()
        var inWindow: [SHA256Digest: Int] = [:]
        for index in times.indices where Sides.normalized(chats[index]) == chosen {
            guard let date = Self.date(times[index]) else { progress.unread += 1; continue }
            let message = ExportedMessage(time: date.timeIntervalSince1970, stamp: times[index], label: senders[index],
                                          text: bodies[index], kind: kinds[index])
            let key = Self.key(message)
            inWindow[key, default: 0] += 1
            guard inWindow[key, default: 0] > stored[key, default: 0] else { continue }
            guard var line = try? encoder.encode(message) else { continue }
            stored[key] = inWindow[key]
            line.append(0x0A)
            try? stream?.write(contentsOf: line)
            progress.labels[Sides.normalized(senders[index]), default: 0] += 1
            progress.days[String(times[index].prefix(10)), default: 0] += 1
            progress.oldest = min(progress.oldest ?? date.timeIntervalSince1970, date.timeIntervalSince1970)
            progress.newest = max(progress.newest ?? date.timeIntervalSince1970, date.timeIntervalSince1970)
        }
    }

    private static func key(_ message: ExportedMessage) -> SHA256Digest {
        SHA256.hash(data: Data("\(message.stamp)\u{1F}\(message.label)\u{1F}\(message.text)".utf8))
    }

    private func openStream() {
        guard stream == nil else { return }
        let path = Self.recordsURL.path
        if !FileManager.default.fileExists(atPath: path) {
            FileManager.default.createFile(atPath: path, contents: nil, attributes: [.protectionKey: FileProtectionType.completeUnlessOpen])
        }
        stream = try? FileHandle(forWritingTo: Self.recordsURL)
        _ = try? stream?.seekToEnd()
    }

    /// After a restart of the app, what is already on disk still counts.
    private func loadStored() {
        guard stored.isEmpty, let saved = try? rawMessages() else { return }
        for message in saved { stored[Self.key(message), default: 0] += 1 }
    }

    // MARK: Photos and files

    private func fileWindow(_ pass: FilePass) -> [String: Any] {
        guard !pass.isDone else { return ["next": "finalize", "hop": 0] }
        let window = pass.windows[pass.index]
        let hop = pass.runBytes >= Window.fileBytes || pass.runWindows >= Window.fileWindows
        return ["next": "files", "wf": Self.stamp(window.from), "wt": Self.stamp(window.to), "hop": hop ? 1 : 0]
    }

    /// The current window has been walked; on to the next. One that came back
    /// near iOS's ceiling may have left messages out, so it is walked again in
    /// two halves. Files already saved are recognised.
    private func windowFinished(_ body: [String: Any]) -> Data {
        guard var pass = progress.files else { return Self.json(["next": "finalize", "hop": 0]) }
        let window = pass.isDone ? nil : pass.windows[pass.index]
        if let window, (Int("\(body["n"] ?? 0)") ?? 0) >= Window.full, window.to - window.from > 2 * 60 * 1000 {
            let middle = ((window.from + window.to) / 2).rounded()
            pass.windows.replaceSubrange(pass.index...pass.index, with: [.init(from: middle, to: window.to), .init(from: window.from, to: middle)])
        } else {
            pass.index += 1
        }
        pass.tries = 0
        pass.runWindows += 1
        progress.files = pass
        let fraction = Double(pass.index) / Double(max(pass.windows.count, 1))
        run.report(fraction: fraction, title: "Saving attachments", subtitle: "\(pass.saved.formatted()) saved")
        save()
        return Self.json(fileWindow(pass))
    }

    private func saveFile(_ request: LocalReceiver.Request) -> Data {
        let ok = Data("{}".utf8)
        guard let temp = request.file else { return ok }
        defer { try? FileManager.default.removeItem(at: temp) }
        func header(_ name: String) -> String { (request.headers[name] ?? "").removingPercentEncoding ?? "" }
        guard var pass = progress.files,
              Sides.normalized(header("x-unbubble-chat")) == Sides.normalized(progress.chat) else { return ok }
        let bytes = (try? FileManager.default.attributesOfItem(atPath: temp.path)[.size] as? Int) ?? 0
        guard bytes > 0, let date = Self.date(header("x-unbubble-date")) else { return ok }
        let folder = Self.attachmentsFolder
        try? FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
        let name = Self.fileName(date: date, original: header("x-unbubble-name"))
        let target = folder.appending(path: name)
        // A window read again after a stop sends its files again.
        if let existing = try? FileManager.default.attributesOfItem(atPath: target.path)[.size] as? Int, existing == bytes { return ok }
        let unique = FileManager.default.fileExists(atPath: target.path) ? folder.appending(path: "\(UUID().uuidString.prefix(6))-\(name)") : target
        guard (try? FileManager.default.moveItem(at: temp, to: unique)) != nil else { return ok }
        append(SavedAttachment(time: date.timeIntervalSince1970, file: unique.lastPathComponent, bytes: bytes))
        pass.saved += 1
        pass.bytes += bytes
        pass.runBytes += bytes
        progress.files = pass
        return ok
    }

    private func append(_ saved: SavedAttachment) {
        guard var line = try? JSONEncoder().encode(saved) else { return }
        line.append(0x0A)
        let path = Self.savedFilesURL.path
        if !FileManager.default.fileExists(atPath: path) {
            FileManager.default.createFile(atPath: path, contents: nil, attributes: [.protectionKey: FileProtectionType.completeUnlessOpen])
        }
        guard let handle = try? FileHandle(forWritingTo: Self.savedFilesURL) else { return }
        defer { try? handle.close() }
        _ = try? handle.seekToEnd()
        try? handle.write(contentsOf: line)
    }

    private static func fileName(date: Date, original: String) -> String {
        let format = DateFormatter()
        format.locale = Locale(identifier: "en_US_POSIX")
        format.dateFormat = "yyyy-MM-dd HH.mm.ss"
        let safe = original.components(separatedBy: CharacterSet(charactersIn: "/\\:?%*|\"<>\n\r")).joined(separator: " ")
            .trimmingCharacters(in: .whitespaces)
        return "\(format.string(from: date)) \(safe.isEmpty ? "attachment" : String(safe.suffix(120)))"
    }

    // MARK: Files

    private func save() {
        if let data = try? JSONEncoder().encode(progress) {
            try? data.write(to: Self.progressURL, options: [.atomic, .completeFileProtectionUnlessOpen])
        }
    }

    private func rawMessages() throws -> [ExportedMessage] {
        try? stream?.synchronize()
        let data = try Data(contentsOf: Self.recordsURL)
        let decoder = JSONDecoder()
        return data.split(separator: 0x0A).compactMap { try? decoder.decode(ExportedMessage.self, from: Data($0)) }
    }

    private func readMessages() throws -> [ExportedMessage] {
        try rawMessages().sorted { $0.time < $1.time }
    }

    func record() throws -> ExportRecord {
        let messages = try readMessages()
        var saved: [SavedAttachment] = []
        if let data = try? Data(contentsOf: Self.savedFilesURL) {
            let decoder = JSONDecoder()
            saved = data.split(separator: 0x0A).compactMap { try? decoder.decode(SavedAttachment.self, from: Data($0)) }
        }
        return ExportRecord(chat: progress.chat, messages: messages, sides: sides, attachments: ExportRecord.matched(saved, to: messages),
                            unread: progress.unread, capped: progress.capped)
    }

    #if DEBUG
    private func startFilesForTest() {
        guard let messages = try? readMessages() else { return }
        progress.files = FilePass.plan(times: messages.reversed().map(\.time))
        progress.token = "test-token"
        phase = .opening
        save()
        watch()
    }

    private func writeTestFiles() {
        Task {
            guard let record = try? record() else { return }
            let target = URL.documentsDirectory.appending(path: "test-files", directoryHint: .isDirectory)
            try? FileManager.default.removeItem(at: target)
            try? FileManager.default.createDirectory(at: target, withIntermediateDirectories: true)
            for url in [try? Exporter.csv(record), try? Exporter.pdf(record) { _ in }, try? Exporter.bundle(record)].compactMap({ $0 }) {
                try? FileManager.default.copyItem(at: url, to: target.appending(path: url.lastPathComponent))
            }
        }
    }
    #endif

    // MARK: Helpers

    private static let isoFormatter = ISO8601DateFormatter()
    private static let isoFractional: ISO8601DateFormatter = {
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        return formatter
    }()

    private static func date(_ value: String?) -> Date? {
        guard let value else { return nil }
        return isoFormatter.date(from: value) ?? isoFractional.date(from: value)
    }

    /// Wall-clock text, which the Shortcut's Get Dates reads as local time.
    private static func stamp(_ ms: Double) -> String {
        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.dateFormat = "yyyy-MM-dd HH:mm:ss"
        return formatter.string(from: Date(timeIntervalSince1970: ms / 1000))
    }

    private static func json(_ value: [String: Any]) -> Data {
        (try? JSONSerialization.data(withJSONObject: value)) ?? Data("{}".utf8)
    }
}
