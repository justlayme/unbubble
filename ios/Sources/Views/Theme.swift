import SwiftUI

/// Unbubble's look, taken from its icon: near-black ground, warm paper squares,
/// a thin seam and one cyan signal. Nothing here is rounded.
enum Theme {
    static let ground = Color(red: 5 / 255, green: 6 / 255, blue: 7 / 255)
    static let ink = Color(red: 228 / 255, green: 223 / 255, blue: 210 / 255)
    static let soft = Color(red: 185 / 255, green: 181 / 255, blue: 171 / 255)
    static let dim = Color(red: 125 / 255, green: 122 / 255, blue: 114 / 255)
    static let signal = Color(red: 34 / 255, green: 211 / 255, blue: 238 / 255)
    static let line = Color.white.opacity(0.13)
    static let label = Font.system(size: 11, weight: .semibold, design: .monospaced)
    static let action = Font.system(size: 14, weight: .bold, design: .monospaced)
}

/// A small heading in capitals.
struct SmallLabel: View {
    let text: String
    var color = Theme.dim

    var body: some View {
        Text(text.uppercased()).font(Theme.label).tracking(1.6).foregroundStyle(color).lineLimit(1)
    }
}

struct Seam: View {
    var body: some View {
        Rectangle().fill(Theme.line).frame(height: 1)
    }
}

struct PrimaryButton: View {
    let title: String
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            HStack {
                Text(title.uppercased()).font(Theme.action).tracking(1.4)
                Spacer()
                PixelGlyph(shape: .arrow, color: Theme.ground).frame(width: 18)
            }
            .foregroundStyle(Theme.ground)
            .padding(.horizontal, 20)
            .frame(maxWidth: .infinity, minHeight: 60)
            .background(Theme.signal)
        }
    }
}

struct QuietButton: View {
    let title: String
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            Text(title.uppercased()).font(Theme.action).tracking(1.2).foregroundStyle(Theme.ink)
                .frame(maxWidth: .infinity, minHeight: 52)
                .overlay(Rectangle().stroke(Theme.line))
        }
    }
}
