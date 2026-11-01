import SwiftUI

/// Unbubble's look, taken from its icon: near-black ground, warm paper squares
/// and one cyan signal.
enum Theme {
    static let ground = Color(red: 5 / 255, green: 6 / 255, blue: 7 / 255)
    static let card = Color(white: 0.09)
    static let line = Color.white.opacity(0.12)
    static let ink = Color(red: 228 / 255, green: 223 / 255, blue: 210 / 255)
    static let quiet = Color(red: 174 / 255, green: 168 / 255, blue: 154 / 255)
    static let signal = Color(red: 32 / 255, green: 215 / 255, blue: 239 / 255)
    static let mono = Font.system(.footnote, design: .monospaced)
}

struct PrimaryButton: View {
    let title: String
    let action: () -> Void
    var body: some View {
        Button(action: action) {
            Text(title).font(.headline).foregroundStyle(Theme.ground)
                .frame(maxWidth: .infinity, minHeight: 54)
                .background(Theme.signal, in: RoundedRectangle(cornerRadius: 14))
        }
    }
}

struct QuietButton: View {
    let title: String
    let action: () -> Void
    var body: some View {
        Button(action: action) {
            Text(title).font(.subheadline.weight(.semibold)).foregroundStyle(Theme.ink)
                .frame(maxWidth: .infinity, minHeight: 48)
                .overlay(RoundedRectangle(cornerRadius: 14).stroke(Theme.line))
        }
    }
}

/// Message bubbles rising, yours on one side and theirs on the other, each one
/// flattening into a plain line near the top: a conversation coming out of its
/// bubbles. The mix on screen is the real mix of who sent what. Still under
/// Reduce Motion.
struct UnbubbleMark: View {
    let mine: Int
    let theirs: Int
    @Environment(\.accessibilityReduceMotion) private var holdsStill

    private let visible = 9
    private let beat = 1.5

    var body: some View {
        TimelineView(.periodic(from: .now, by: 1.0 / 24)) { timeline in
            Canvas { context, size in
                let clock = holdsStill ? 40 : timeline.date.timeIntervalSinceReferenceDate / beat
                for offset in 0...visible {
                    bubble(&context, in: size, number: Int(clock) + offset, clock: clock)
                }
            }
        }
        .frame(height: 230)
        .accessibilityLabel("Export in progress")
    }

    private func bubble(_ context: inout GraphicsContext, in size: CGSize, number: Int, clock: Double) {
        let height = (Double(number) - clock + 1) / Double(visible)
        let travelled = 1 - height
        guard (0...1).contains(travelled) else { return }
        let scatter = Self.scatter(number)
        let total = mine + theirs
        let isMine = scatter < (total > 0 ? Double(mine) / Double(total) : 0.5)
        let flat = Self.ease(travelled, from: 0.6, to: 0.9)
        let length = size.width * (0.34 + 0.38 * Self.scatter(number &* 7 &+ 3))
        let tall = 30 - 27 * flat
        let inset = 8.0
        let start = isMine ? size.width - length - inset : inset
        let shape = CGRect(x: start + (inset - start) * flat, y: (size.height - tall) * height, width: length, height: tall)
        let fade = Self.ease(travelled, from: 0, to: 0.1) * (1 - Self.ease(travelled, from: 0.92, to: 1))
        context.opacity = fade
        context.fill(Path(roundedRect: shape, cornerRadius: tall / 2), with: .color(isMine ? Theme.signal : Theme.card.opacity(0.6)))
        if !isMine {
            context.stroke(Path(roundedRect: shape, cornerRadius: tall / 2), with: .color(Theme.ink.opacity(0.4)), lineWidth: 1)
        }
    }

    /// The same number always lands in the same place.
    private static func scatter(_ number: Int) -> Double {
        let value = abs(sin(Double(number) * 12.9898) * 43758.5453)
        return value - value.rounded(.down)
    }

    private static func ease(_ value: Double, from low: Double, to high: Double) -> Double {
        let part = min(max((value - low) / (high - low), 0), 1)
        return part * part * (3 - 2 * part)
    }
}
