import Foundation

/// Which side each message is on. Apple's Message has no "is from me", so sides
/// come from the sender labels. Checked against Messages' own database: a saved
/// contact's messages carry the chat's name, the reader's carry the reader's own
/// name, and a contact who isn't saved has a blank label, on their messages and
/// in the chat's recipients alike.
struct Sides: Sendable {
    enum Side: Sendable { case me, them, unknown }

    private let contact: Set<String>
    private let unresolved: Set<String>

    /// `labels` are normalized sender labels, every one the chat has.
    init(labels: some Sequence<String>, recipients: [String], chat: String) {
        let themKeys = Set((recipients + [chat]).flatMap(Self.aliasKeys))
        func isContact(_ label: String) -> Bool { Self.aliasKeys(label).contains { themKeys.contains($0) } }
        let labels = Array(Set(labels))
        var contact = Set(labels.filter(isContact))
        var unresolved = Set<String>()
        let others = labels.filter { !$0.isEmpty && !isContact($0) && !Self.selfLabels.contains($0) }
        if !contact.isEmpty {
            // A second phone or email beside a matched label is the same
            // contact on another handle, when a label is left for the reader.
            if labels.contains(where: { !isContact($0) && !Self.isHandle($0) }) {
                contact.formUnion(others.filter(Self.isHandle))
            }
        } else if recipients.isEmpty {
            if labels.contains("") { contact.insert("") }
        } else if labels.contains(where: { $0.isEmpty || Self.selfLabels.contains($0) }) {
            if others.count == 1 { contact.insert(others[0]) }
        } else if others.count > 1 {
            let named = others.filter { label in
                label.split(separator: " ").contains { word in
                    word.count > 2 && themKeys.contains { $0.split(separator: " ").contains(word) }
                }
            }
            if named.count == 1 { contact.insert(named[0]) } else { unresolved.formUnion(others) }
        }
        self.contact = contact
        self.unresolved = unresolved
    }

    func side(of label: String) -> Side {
        let label = Self.normalized(label)
        if unresolved.contains(label) { return .unknown }
        return contact.contains(label) ? .them : .me
    }

    private static let selfLabels: Set<String> = ["me", "myself", "you"]
    private static let bidiMarks = CharacterSet(charactersIn: "\u{200E}\u{200F}\u{202A}\u{202B}\u{202C}\u{202D}\u{202E}\u{2066}\u{2067}\u{2068}\u{2069}")

    static func normalized(_ value: String) -> String {
        let plain = String(String.UnicodeScalarView(value.precomposedStringWithCompatibilityMapping.unicodeScalars.filter { !bidiMarks.contains($0) }))
        return plain.split(whereSeparator: \.isWhitespace).joined(separator: " ").lowercased()
    }

    private static func isHandle(_ label: String) -> Bool {
        label.contains("@") || isPhoneLike(label)
    }

    private static func isPhoneLike(_ value: String) -> Bool {
        value.count >= 7 && value.allSatisfy { "+0123456789 ().-".contains($0) }
    }

    /// "+1 (555) 010-2000" in one place and "+15550102000" in another are one number.
    private static func aliasKeys(_ value: String) -> [String] {
        var base = normalized(value)
        for scheme in ["tel:", "sms:", "mailto:", "imessage:"] where base.hasPrefix(scheme) {
            base.removeFirst(scheme.count)
            break
        }
        guard !base.isEmpty else { return [] }
        var keys = [base]
        let digits = base.filter(\.isNumber)
        if digits.count >= 7, base.allSatisfy({ "+0123456789 ().-".contains($0) }) {
            keys.append("tel:\(digits)")
            if digits.count == 11, digits.hasPrefix("1") { keys.append("tel:\(digits.dropFirst())") }
            if digits.count == 10 { keys.append("tel:1\(digits)") }
            if digits.count >= 9 { keys.append("tel-tail:\(digits.suffix(9))") }
        }
        return keys
    }
}
