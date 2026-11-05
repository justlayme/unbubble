import Foundation
import ImageIO
import UIKit
import CryptoKit

/// Turns a finished read into a file. Runs on the phone; nothing is uploaded.
enum Exporter {
    static let noText = "[No text: photo, file or other attachment]"

    /// A fixed machine format. Without the POSIX locale a Buddhist or Japanese
    /// calendar prints another year, and iOS's 12/24-hour setting can change it.
    private static func fixed(_ format: String) -> DateFormatter {
        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.calendar = Calendar(identifier: .gregorian)
        formatter.dateFormat = format
        return formatter
    }

    private static func base(_ record: ExportRecord) -> String {
        let safe = record.chat.components(separatedBy: CharacterSet(charactersIn: "/\\:?%*|\"<>")).joined(separator: " ")
        let day = fixed("yyyy-MM-dd")
        let from = record.messages.first.map { day.string(from: Date(timeIntervalSince1970: $0.time)) } ?? ""
        let to = record.messages.last.map { day.string(from: Date(timeIntervalSince1970: $0.time)) } ?? ""
        return "Unbubble - \(safe) - \(from) to \(to)"
    }

    private static func destination(_ name: String, _ suffix: String) -> URL {
        URL.temporaryDirectory.appending(path: "\(name).\(suffix)")
    }

    /// iOS marks an attachment's place in the text with an object-replacement
    /// character, which prints as a box.
    private static func shown(_ message: ExportedMessage) -> String {
        guard message.text.contains("\u{FFFC}") else { return message.text }
        return message.text.replacingOccurrences(of: "\u{FFFC}", with: "").trimmingCharacters(in: .whitespacesAndNewlines)
    }

    private static func direction(_ side: Sides.Side) -> String {
        switch side {
        case .me: "Sent"
        case .them: "Received"
        case .unknown: "Unknown"
        }
    }

    // MARK: CSV

    static func csv(_ record: ExportRecord) throws -> URL {
        let url = destination(base(record), "csv")
        FileManager.default.createFile(atPath: url.path, contents: Data([0xEF, 0xBB, 0xBF]))
        let handle = try FileHandle(forWritingTo: url)
        defer { try? handle.close() }
        _ = try handle.seekToEnd()
        try handle.write(contentsOf: Data("Timestamp,Date,Time,Sender,Direction,Type,Message,Attachments\r\n".utf8))
        let date = fixed("yyyy-MM-dd")
        let time = fixed("HH:mm:ss")
        var chunk = ""
        for (index, message) in record.messages.enumerated() {
            let when = Date(timeIntervalSince1970: message.time)
            let saved = record.attachments[index] ?? []
            let text = shown(message)
            let row = [message.stamp, date.string(from: when), time.string(from: when), record.sender(of: message),
                       direction(record.side(of: message)), message.isReaction ? "Reaction" : "Message",
                       text.isEmpty && saved.isEmpty ? noText : text,
                       saved.map { "attachments/\($0.file)" }.joined(separator: "; ")]
            chunk += row.map(cell).joined(separator: ",") + "\r\n"
            if chunk.utf8.count > 1 << 18 { try handle.write(contentsOf: Data(chunk.utf8)); chunk = "" }
        }
        try handle.write(contentsOf: Data(chunk.utf8))
        return url
    }

    /// Quoted per RFC 4180. A cell a spreadsheet would run as a formula starts
    /// with an apostrophe, so a message can never execute when the file opens.
    private static func cell(_ value: String) -> String {
        var value = value
        if let first = value.first, "=+-@\t\r".contains(first) { value = "'" + value }
        return "\"" + value.replacingOccurrences(of: "\"", with: "\"\"") + "\""
    }

    // MARK: Spreadsheet and attachments together

    /// The CSV beside an attachments folder, zipped by iOS.
    static func bundle(_ record: ExportRecord) throws -> URL {
        let name = base(record)
        let fileManager = FileManager.default
        let work = URL.temporaryDirectory.appending(path: UUID().uuidString, directoryHint: .isDirectory)
        defer { try? fileManager.removeItem(at: work) }
        let folder = work.appending(path: name, directoryHint: .isDirectory)
        let files = folder.appending(path: "attachments", directoryHint: .isDirectory)
        try fileManager.createDirectory(at: files, withIntermediateDirectories: true)
        try fileManager.moveItem(at: try csv(record), to: folder.appending(path: "Messages.csv"))
        for saved in record.attachments.values.joined() {
            let source = ExportEngine.attachmentsFolder.appending(path: saved.file)
            let target = files.appending(path: saved.file)
            if (try? fileManager.linkItem(at: source, to: target)) == nil { try? fileManager.copyItem(at: source, to: target) }
        }
        let url = destination(name, "zip")
        try? fileManager.removeItem(at: url)
        var failure: NSError?
        var moved: Error?
        NSFileCoordinator().coordinate(readingItemAt: folder, options: [.forUploading], error: &failure) { zipped in
            do { try fileManager.copyItem(at: zipped, to: url) } catch { moved = error }
        }
        if let error = failure ?? moved { throw error }
        return url
    }

    // MARK: PDF

    private static let page = CGRect(x: 0, y: 0, width: 612, height: 792)
    private static let margin: CGFloat = 54
    private static let bodyFont = UIFont.systemFont(ofSize: 10.5)
    private static let metaFont = UIFont.systemFont(ofSize: 8.5, weight: .semibold)
    private static let dayFont = UIFont.systemFont(ofSize: 11, weight: .bold)
    private static let pictureBox: CGFloat = 230
    private static let pictureGap: CGFloat = 6
    private static let pictureKinds: Set<String> = ["jpg", "jpeg", "png", "heic", "heif", "gif", "tiff", "webp"]

    private struct Picture {
        let url: URL
        let size: CGSize
    }

    private enum Row {
        case day(String)
        case message(meta: String, text: String, pictures: [Picture], files: String)
    }

    /// The size a photo is drawn at, read without decoding it.
    private static func pictureSize(_ url: URL) -> CGSize? {
        guard pictureKinds.contains(url.pathExtension.lowercased()),
              let source = CGImageSourceCreateWithURL(url as CFURL, nil),
              let properties = CGImageSourceCopyPropertiesAtIndex(source, 0, nil) as? [CFString: Any],
              var width = (properties[kCGImagePropertyPixelWidth] as? NSNumber)?.doubleValue,
              var height = (properties[kCGImagePropertyPixelHeight] as? NSNumber)?.doubleValue,
              width > 0, height > 0 else { return nil }
        if ((properties[kCGImagePropertyOrientation] as? NSNumber)?.intValue ?? 1) >= 5 { swap(&width, &height) }
        let scale = min(pictureBox / width, pictureBox / height, 1)
        return CGSize(width: ceil(width * scale), height: ceil(height * scale))
    }

    private static func picture(_ url: URL) -> UIImage? {
        let options: [CFString: Any] = [kCGImageSourceCreateThumbnailFromImageAlways: true, kCGImageSourceCreateThumbnailWithTransform: true,
                                        kCGImageSourceThumbnailMaxPixelSize: 720]
        guard let source = CGImageSourceCreateWithURL(url as CFURL, nil),
              let thumbnail = CGImageSourceCreateThumbnailAtIndex(source, 0, options as CFDictionary) else { return nil }
        // As JPEG data, which a PDF keeps as it is instead of storing every pixel.
        return UIImage(cgImage: thumbnail).jpegData(compressionQuality: 0.75).flatMap(UIImage.init(data:))
    }

    static func pdf(_ record: ExportRecord, progress: @escaping @Sendable (Double) -> Void) throws -> URL {
        let url = destination(base(record), "pdf")
        let width = page.width - margin * 2
        let dayFormat = DateFormatter(); dayFormat.dateStyle = .full
        let timeFormat = DateFormatter(); timeFormat.setLocalizedDateFormatFromTemplate("jmmss")

        func textHeight(_ text: String) -> CGFloat {
            guard !text.isEmpty else { return 0 }
            let box = (text as NSString).boundingRect(with: CGSize(width: width, height: .greatestFiniteMagnitude),
                                                      options: [.usesLineFragmentOrigin], attributes: [.font: bodyFont], context: nil)
            return ceil(box.height)
        }

        // A message taller than a page would be cut off at its foot, so it is
        // split into page-sized pieces, each after the first marked continued.
        let pieceHeight = page.height - margin * 2 - 16 - 13 - 7 - 40
        func pieces(_ text: String) -> [String] {
            var rest = Substring(text)
            var out: [String] = []
            while textHeight(String(rest)) > pieceHeight {
                // The most characters that fit, found by halving; text with no
                // spaces (Chinese, Japanese, a long link) splits there too.
                var low = 1, high = rest.count
                while low < high {
                    let middle = (low + high + 1) / 2
                    if textHeight(String(rest.prefix(middle))) <= pieceHeight { low = middle } else { high = middle - 1 }
                }
                var cut = rest.index(rest.startIndex, offsetBy: low)
                if let space = rest[..<cut].lastIndex(where: \.isWhitespace),
                   rest.distance(from: rest.startIndex, to: space) > low / 2 {
                    cut = rest.index(after: space)
                }
                out.append(String(rest[..<cut]))
                rest = rest[cut...]
            }
            out.append(String(rest))
            return out
        }

        var rows: [Row] = []
        rows.reserveCapacity(record.messages.count + record.messages.count / 20)
        var lastDay = ""
        for (index, message) in record.messages.enumerated() {
            let when = Date(timeIntervalSince1970: message.time)
            let day = dayFormat.string(from: when)
            if day != lastDay { rows.append(.day(day)); lastDay = day }
            let kind = message.isReaction ? " · Reaction" : ""
            let saved = record.attachments[index] ?? []
            let pictures = saved.compactMap { item -> Picture? in
                let file = ExportEngine.attachmentsFolder.appending(path: item.file)
                return pictureSize(file).map { Picture(url: file, size: $0) }
            }
            let text = shown(message)
            let meta = "No. \((index + 1).formatted()) · \(timeFormat.string(from: when)) · \(record.sender(of: message))\(kind)"
            let parts = pieces(text.isEmpty && saved.isEmpty ? noText : text)
            for (part, piece) in parts.enumerated() {
                let last = part == parts.count - 1
                rows.append(.message(meta: part == 0 ? meta : "No. \((index + 1).formatted()) (continued)",
                                     text: piece,
                                     pictures: last ? pictures : [],
                                     files: last ? saved.map { "Attachment: \($0.file)" }.joined(separator: "\n") : ""))
            }
        }

        func height(_ row: Row) -> CGFloat {
            switch row {
            case .day: return 26
            case .message(_, let text, let pictures, let files):
                return 13 + textHeight(text) + pictures.reduce(0) { $0 + $1.size.height + pictureGap } + textHeight(files) + 7
            }
        }

        // The first pass settles the page breaks, so every page can say "of N".
        let bottom = page.height - margin - 16
        var breaks: [Int] = [0]
        var y = margin
        for (index, row) in rows.enumerated() {
            let needed = min(height(row), bottom - margin)
            if y + needed > bottom { breaks.append(index); y = margin }
            y += needed
            if index % 2000 == 0 { progress(0.5 * Double(index) / Double(max(rows.count, 1))) }
        }
        let total = breaks.count + 1
        let fingerprint = digest(record)

        let renderer = UIGraphicsPDFRenderer(bounds: page, format: pdfFormat(record.chat))
        try renderer.writePDF(to: url) { context in
            context.beginPage()
            cover(record, pages: total, fingerprint: fingerprint)
            footer(number: 1, of: total, chat: record.chat)
            for (pageIndex, start) in breaks.enumerated() {
                autoreleasepool {
                    context.beginPage()
                    let end = pageIndex + 1 < breaks.count ? breaks[pageIndex + 1] : rows.count
                    var y = margin
                    for row in rows[start..<end] {
                        switch row {
                        case .day(let day):
                            (day as NSString).draw(at: CGPoint(x: margin, y: y + 6), withAttributes: [.font: dayFont, .foregroundColor: UIColor.black])
                            y += 26
                        case .message(let meta, let text, let pictures, let files):
                            (meta as NSString).draw(at: CGPoint(x: margin, y: y), withAttributes: [.font: metaFont, .foregroundColor: UIColor.darkGray])
                            var line = y + 13
                            let attributes: [NSAttributedString.Key: Any] = [.font: bodyFont, .foregroundColor: UIColor.black]
                            if !text.isEmpty {
                                let room = CGRect(x: margin, y: line, width: width, height: max(0, bottom - line))
                                (text as NSString).draw(with: room, options: [.usesLineFragmentOrigin, .truncatesLastVisibleLine], attributes: attributes, context: nil)
                                line += textHeight(text)
                            }
                            for item in pictures where line + item.size.height <= bottom {
                                picture(item.url)?.draw(in: CGRect(origin: CGPoint(x: margin, y: line + pictureGap / 2), size: item.size))
                                line += item.size.height + pictureGap
                            }
                            if !files.isEmpty, line < bottom {
                                let room = CGRect(x: margin, y: line, width: width, height: bottom - line)
                                (files as NSString).draw(with: room, options: [.usesLineFragmentOrigin, .truncatesLastVisibleLine],
                                                         attributes: [.font: bodyFont, .foregroundColor: UIColor.darkGray], context: nil)
                            }
                            y += min(height(row), bottom - margin)
                        }
                    }
                    footer(number: pageIndex + 2, of: total, chat: record.chat)
                    progress(0.5 + 0.5 * Double(pageIndex) / Double(max(breaks.count, 1)))
                }
            }
        }
        return url
    }

    private static func pdfFormat(_ chat: String) -> UIGraphicsPDFRendererFormat {
        let format = UIGraphicsPDFRendererFormat()
        format.documentInfo = [kCGPDFContextTitle as String: "Messages with \(chat)", kCGPDFContextCreator as String: "Unbubble"]
        return format
    }

    /// A fingerprint of the exact messages in this file, so anyone can tell
    /// whether two copies of this export hold the same content.
    private static func digest(_ record: ExportRecord) -> String {
        var hash = SHA256()
        for message in record.messages { hash.update(data: Data("\(message.stamp)\u{1F}\(message.label)\u{1F}\(message.text)\u{1E}".utf8)) }
        return hash.finalize().map { String(format: "%02x", $0) }.joined()
    }

    private static func cover(_ record: ExportRecord, pages: Int, fingerprint: String) {
        let width = page.width - margin * 2
        mark(at: CGPoint(x: margin, y: margin), pitch: 3.2)
        var y = margin + 84
        func line(_ text: String, _ font: UIFont, gap: CGFloat, color: UIColor = .black) {
            let box = (text as NSString).boundingRect(with: CGSize(width: width, height: 400), options: [.usesLineFragmentOrigin], attributes: [.font: font], context: nil)
            (text as NSString).draw(with: CGRect(x: margin, y: y, width: width, height: ceil(box.height)), options: [.usesLineFragmentOrigin],
                                    attributes: [.font: font, .foregroundColor: color], context: nil)
            y += ceil(box.height) + gap
        }
        let long = DateFormatter(); long.dateStyle = .long; long.timeStyle = .short
        let messages = record.messages
        let sent = messages.filter { record.side(of: $0) == .me }.count
        let received = messages.filter { record.side(of: $0) == .them }.count
        line("Message record", .systemFont(ofSize: 26, weight: .bold), gap: 8)
        line("Conversation with \(record.chat)", .systemFont(ofSize: 15), gap: 28)
        var rows: [(String, String)] = [
            ("First message", messages.first.map { long.string(from: Date(timeIntervalSince1970: $0.time)) } ?? "None"),
            ("Last message", messages.last.map { long.string(from: Date(timeIntervalSince1970: $0.time)) } ?? "None"),
            ("Messages", "\(messages.count.formatted()) (\(sent.formatted()) sent, \(received.formatted()) received)"),
        ]
        if record.attachmentCount > 0 { rows.append(("Attachments", record.attachmentCount.formatted())) }
        rows += [
            ("Pages", pages.formatted()),
            ("Exported", long.string(from: Date())),
            ("Time zone", TimeZone.current.identifier),
            ("SHA-256 of contents", fingerprint),
        ]
        for (label, value) in rows {
            line(label, metaFont, gap: 2, color: .darkGray)
            line(value, bodyFont, gap: 12)
        }
        y += 14
        var notes = "Exported by the Unbubble app from Messages on this iPhone, through Apple's Shortcuts. It contains the messages iOS made available at the time of export, which may not be every message ever sent."
        notes += record.attachmentCount > 0
            ? " Photos are shown at reduced size and attachments are listed by file name."
            : " Photos and other attachments are not included, and messages with no text are marked."
        if record.unread > 0 { notes += " \(record.unread.formatted()) messages returned by iOS could not be read and are not included." }
        if record.capped > 0 { notes += " In \(record.capped.formatted()) very busy stretches iOS may have returned fewer messages than were sent." }
        notes += " Unbubble does not certify this record. Ask your attorney or the court what is required to submit it."
        line(notes, .systemFont(ofSize: 9.5), gap: 0, color: .darkGray)
    }

    /// The logo in ink for a white page, its one cyan square kept.
    private static func mark(at origin: CGPoint, pitch: CGFloat) {
        UIColor(white: 0, alpha: 0.5).setFill()
        UIRectFill(CGRect(x: origin.x + Logo.seam * pitch, y: origin.y, width: max(0.5, pitch * 0.11), height: Logo.height * pitch))
        let square = pitch * Logo.square
        for (row, line) in Logo.cells.enumerated() {
            for (column, cell) in line.enumerated() where cell != "." {
                switch cell {
                case "c": UIColor(red: 34 / 255, green: 211 / 255, blue: 238 / 255, alpha: 1).setFill()
                case "g": UIColor(white: 0.62, alpha: 1).setFill()
                case "s": UIColor(white: 0.35, alpha: 1).setFill()
                default: UIColor.black.setFill()
                }
                UIRectFill(CGRect(x: origin.x + CGFloat(column) * pitch, y: origin.y + (CGFloat(row) + Logo.top) * pitch, width: square, height: square))
            }
        }
    }

    private static func footer(number: Int, of total: Int, chat: String) {
        let text = "Conversation with \(chat) · Page \(number) of \(total)" as NSString
        let attributes: [NSAttributedString.Key: Any] = [.font: UIFont.systemFont(ofSize: 8), .foregroundColor: UIColor.gray]
        let size = text.size(withAttributes: attributes)
        text.draw(at: CGPoint(x: (page.width - size.width) / 2, y: page.height - margin + 6), withAttributes: attributes)
    }
}
