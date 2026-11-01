import Foundation

/// One message as it was read. Stored only in this app's container, one JSON
/// line each, until the reader exports or clears it.
struct ExportedMessage: Codable, Sendable {
    /// Seconds since 1970.
    let time: TimeInterval
    /// The date as iOS reported it, with its zone ("2024-11-01T04:04:00-05:00").
    let stamp: String
    /// The sender as Messages labels them. Blank for a contact who isn't saved.
    let label: String
    let text: String
    let kind: String

    var isReaction: Bool { kind.lowercased().contains("reaction") }
    /// iOS leaves an object-replacement character where an attachment sits, and
    /// a photo sent alone has no text at all.
    var showsAttachment: Bool { !isReaction && (text.isEmpty || text.contains("\u{FFFC}")) }
}

/// A photo or file saved for a message, matched to it by date.
struct SavedAttachment: Codable, Sendable {
    let time: TimeInterval
    let file: String
    let bytes: Int
}

/// What a run of attachments still has to visit: the stretches of the chat's
/// history that hold its messages, newest first.
struct FilePass: Codable, Sendable {
    struct Window: Codable, Sendable {
        let from: Double
        let to: Double
    }

    var windows: [Window]
    var index = 0
    /// Starts of the current window that never finished. A file iOS can't hand
    /// over must not stop every run at the same place.
    var tries = 0
    var skipped = 0
    var saved = 0
    var bytes = 0
    var runBytes = 0
    var runWindows = 0
    var stoppedEarly = false

    var isDone: Bool { index >= windows.count }

    /// Everything in the chat, in stretches short enough that a Find stays
    /// well under iOS's limit. `times` are seconds, newest first.
    static func plan(times: [TimeInterval]) -> FilePass {
        let gap: TimeInterval = 10 * 60
        let span: TimeInterval = 45 * 60
        let most = 150
        var windows: [Window] = []
        var newest: TimeInterval?
        var oldest: TimeInterval = 0
        var count = 0
        func close() {
            guard let newest else { return }
            windows.append(Window(from: (oldest - 2) * 1000, to: (newest + 2) * 1000))
        }
        for time in times {
            if let start = newest, oldest - time <= gap, start - time <= span, count < most {
                oldest = time
                count += 1
            } else {
                close()
                newest = time
                oldest = time
                count = 1
            }
        }
        close()
        return FilePass(windows: windows)
    }
}

/// Where a read has got to, saved after every block so a stopped run resumes.
struct ExportProgress: Codable, Sendable {
    var chat = ""
    var recipients: [String] = []
    var token = ""
    var endMs: Double = 0
    var spanMs: Double = 12 * 864e5
    var emptyMs: Double = 0
    var density: Double = 0
    var runRead = 0
    var runPasses = 0
    var cleanHop = false
    var hopTarget = 2000
    var read = 0
    /// Messages per normalized sender label.
    var labels: [String: Int] = [:]
    var days: [String: Int] = [:]
    var oldest: TimeInterval?
    var newest: TimeInterval?
    /// Messages iOS returned that couldn't be lined up, and busy stretches where
    /// iOS may have stopped short. Both are said out loud on the record.
    var unread = 0
    var capped = 0
    var files: FilePass?
    var finished = false
}

/// A finished read, ready to be written out.
struct ExportRecord: Sendable {
    let chat: String
    let messages: [ExportedMessage]
    let sides: Sides
    /// By the message's position in `messages`.
    let attachments: [Int: [SavedAttachment]]
    let unread: Int
    let capped: Int

    func side(of message: ExportedMessage) -> Sides.Side { sides.side(of: message.label) }

    func sender(of message: ExportedMessage) -> String {
        let name = message.label.trimmingCharacters(in: .whitespacesAndNewlines)
        if !name.isEmpty { return name }
        return side(of: message) == .them ? chat : "Me"
    }

    var attachmentCount: Int { attachments.values.reduce(0) { $0 + $1.count } }

    /// Attachments carry their message's date, to the second. Where several
    /// messages share that second, the one with a gap for an attachment has it.
    static func matched(_ saved: [SavedAttachment], to messages: [ExportedMessage]) -> [Int: [SavedAttachment]] {
        var bySecond: [Int: [Int]] = [:]
        for (index, message) in messages.enumerated() { bySecond[Int(message.time), default: []].append(index) }
        var matched: [Int: [SavedAttachment]] = [:]
        for item in saved {
            guard let candidates = bySecond[Int(item.time)] else { continue }
            let owner = candidates.first { messages[$0].showsAttachment }
                ?? candidates.first { !messages[$0].isReaction }
                ?? candidates[0]
            matched[owner, default: []].append(item)
        }
        return matched
    }
}
