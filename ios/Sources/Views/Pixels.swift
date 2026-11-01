import SwiftUI

/// Unbubble's logo, square for square: a speech bubble, a seam, and the same
/// message as a grid of data with one live cell. Everything else on screen is
/// drawn from the same squares.
enum Logo {
    static let cells = [
        "..ppppppp.........",
        ".p........ggggggg.",
        "p.s.s.s.s.gggggggg",
        "p.........ggggcggg",
        "p.s.s.s.s.gggggggg",
        "p.........gggggggg",
        "p.s.s.s.s.gggggggg",
        "p.........gggggggg",
        "p.s.s.s.s.gggggggg",
        "p.........gggggggg",
        "p.s.s.s.s.gggggggg",
        ".p........ggggggg.",
        "..ppppppp.........",
        "...pp.............",
        "...p..............",
        "..p...............",
    ].map(Array.init)
    /// In pitches: the distance from one square to the next.
    static let width: CGFloat = 17.56
    static let height: CGFloat = 18.8
    static let top: CGFloat = 1.62
    static let seam: CGFloat = 9.23
    static let square: CGFloat = 0.56
    static let textRows = [2, 4, 6, 8, 10]
    static let textColumns = [2, 4, 6, 8]
}

/// The logo. Alive, its messages cross the seam one square at a time.
struct PixelMark: View {
    var alive = false
    @Environment(\.accessibilityReduceMotion) private var holdsStill

    var body: some View {
        Group {
            if alive && !holdsStill {
                TimelineView(.animation(minimumInterval: 1.0 / 24)) { timeline in
                    mark(at: timeline.date.timeIntervalSinceReferenceDate)
                }
            } else {
                mark(at: nil)
            }
        }
        .aspectRatio(Logo.width / Logo.height, contentMode: .fit)
        .accessibilityHidden(true)
    }

    private func mark(at time: TimeInterval?) -> some View {
        Canvas { context, size in
            let pitch = size.width / Logo.width
            let square = pitch * Logo.square
            func place(_ column: CGFloat, _ row: CGFloat) -> CGRect {
                CGRect(x: column * pitch, y: (row + Logo.top) * pitch, width: square, height: square)
            }
            context.fill(Path(CGRect(x: Logo.seam * pitch, y: 0, width: max(1, pitch * 0.11), height: size.height)), with: .color(Theme.ink.opacity(0.5)))
            for (row, line) in Logo.cells.enumerated() {
                for (column, cell) in line.enumerated() where cell != "." {
                    var color = Theme.ink
                    if cell == "s" { color = Theme.soft }
                    if cell == "c" { color = Theme.signal }
                    if cell == "g" {
                        let wave = time.map { 0.82 + 0.18 * sin($0 * 1.3 + Double(column + row) * 0.55) } ?? 1
                        color = Theme.dim.opacity(wave)
                    }
                    context.fill(Path(place(CGFloat(column), CGFloat(row))), with: .color(color))
                }
            }
            guard let time else { return }
            for lane in 0..<5 {
                let clock = time * 0.85 + Double(lane) * 0.2
                let serial = Int(clock.rounded(.down)) &* 31 &+ lane &* 17
                let along = clock - clock.rounded(.down)
                let row = Logo.textRows[abs(serial) % Logo.textRows.count]
                let start = Logo.textColumns[abs(serial >> 3) % Logo.textColumns.count]
                let end = 10 + abs(serial >> 5) % 8
                let eased = along * along * (3 - 2 * along)
                let column = Double(start) + Double(end - start) * eased
                context.fill(Path(place(CGFloat(column), CGFloat(row))), with: .color(Theme.signal.opacity(sin(along * .pi))))
            }
        }
    }
}

/// Text in the logo's squares, five wide and seven tall.
private enum Letters {
    static let shapes: [Character: [String]] = [
        "0": ["01110", "10001", "10011", "10101", "11001", "10001", "01110"],
        "1": ["00100", "01100", "00100", "00100", "00100", "00100", "01110"],
        "2": ["01110", "10001", "00001", "00110", "01000", "10000", "11111"],
        "3": ["11110", "00001", "00001", "01110", "00001", "00001", "11110"],
        "4": ["00010", "00110", "01010", "10010", "11111", "00010", "00010"],
        "5": ["11111", "10000", "11110", "00001", "00001", "10001", "01110"],
        "6": ["00110", "01000", "10000", "11110", "10001", "10001", "01110"],
        "7": ["11111", "00001", "00010", "00100", "01000", "01000", "01000"],
        "8": ["01110", "10001", "10001", "01110", "10001", "10001", "01110"],
        "9": ["01110", "10001", "10001", "01111", "00001", "00010", "01100"],
        "U": ["10001", "10001", "10001", "10001", "10001", "10001", "01110"],
        "N": ["10001", "11001", "10101", "10011", "10001", "10001", "10001"],
        "B": ["11110", "10001", "10001", "11110", "10001", "10001", "11110"],
        "L": ["10000", "10000", "10000", "10000", "10000", "10000", "11111"],
        "E": ["11111", "10000", "10000", "11110", "10000", "10000", "11111"],
    ]
    /// Any separator a locale puts between thousands.
    static let separator = ["00", "00", "00", "00", "00", "01", "10"]

    static func shape(_ character: Character) -> [String] { shapes[character] ?? separator }
}

/// A line of text in squares, as large as `largest` allows and the width permits.
struct PixelText: View {
    let text: String
    var largest: CGFloat = 9
    var color = Theme.ink
    /// The last character in the signal colour: the number is still moving.
    var live = false

    var body: some View {
        Canvas { context, size in
            let glyphs = text.map(Letters.shape)
            let columns = glyphs.reduce(0) { $0 + $1[0].count + 1 } - 1
            let pitch = min(largest, size.width / CGFloat(max(columns, 1)))
            var left: CGFloat = 0
            for (index, glyph) in glyphs.enumerated() {
                let tint = live && index == glyphs.count - 1 ? Theme.signal : color
                for (row, line) in glyph.enumerated() {
                    for (column, bit) in line.enumerated() where bit == "1" {
                        let square = CGRect(x: left + CGFloat(column) * pitch, y: size.height - CGFloat(7 - row) * pitch,
                                            width: pitch * 0.8, height: pitch * 0.8)
                        context.fill(Path(square), with: .color(tint))
                    }
                }
                left += CGFloat(glyph[0].count + 1) * pitch
            }
        }
        .frame(height: largest * 7)
        .accessibilityElement()
        .accessibilityLabel(text)
    }
}

/// The name, and the mark beside it.
struct Wordmark: View {
    var body: some View {
        HStack(spacing: 10) {
            PixelMark().frame(height: 38)
            PixelText(text: "UNBUBBLE", largest: 2.2).frame(width: 106)
            Spacer(minLength: 0)
        }
        .accessibilityElement()
        .accessibilityLabel("Unbubble")
    }
}

/// Small pictures in the same squares.
struct PixelGlyph: View {
    enum Shape {
        case page, table, photo, folder, arrow

        var rows: [String] {
            switch self {
            case .page: ["#######..", "#.....##.", "#.###..##", "#.......#", "#.#####.#", "#.......#", "#.#####.#", "#.......#", "#########"]
            case .table: ["#########", "#...#...#", "#...#...#", "#########", "#...#...#", "#...#...#", "#########", "#...#...#", "#########"]
            case .photo: ["#########", "#.......#", "#.##....#", "#.##....#", "#.....#.#", "#....####", "#.#.#####", "#.#######", "#########"]
            case .folder: ["####.....", "#...#####", "#.......#", "#.......#", "#.......#", "#.......#", "#.......#", "#.......#", "#########"]
            case .arrow: ["....#....", ".....#...", "......#..", ".......#.", "#########", ".......#.", "......#..", ".....#...", "....#...."]
            }
        }
    }

    let shape: Shape
    var color = Theme.signal

    var body: some View {
        Canvas { context, size in
            let pitch = size.width / 9
            for (row, line) in shape.rows.enumerated() {
                for (column, cell) in line.enumerated() where cell == "#" {
                    context.fill(Path(CGRect(x: CGFloat(column) * pitch, y: CGFloat(row) * pitch, width: pitch * 0.8, height: pitch * 0.8)), with: .color(color))
                }
            }
        }
        .aspectRatio(1, contentMode: .fit)
        .accessibilityHidden(true)
    }
}

/// Two people's shares as one row of squares, split the way the logo is.
struct SplitBar: View {
    let mine: Int
    let theirs: Int

    var body: some View {
        Canvas { context, size in
            let cells = 40
            let pitch = size.width / CGFloat(cells)
            let total = mine + theirs
            let split = total > 0 ? Int((Double(mine) / Double(total) * Double(cells)).rounded()) : cells / 2
            for cell in 0..<cells where cell != split {
                let square = CGRect(x: CGFloat(cell) * pitch, y: 0, width: pitch * 0.7, height: size.height)
                context.fill(Path(square), with: .color(cell < split ? Theme.ink : Theme.dim))
            }
        }
        .frame(height: 8)
        .accessibilityHidden(true)
    }
}

/// A conversation's whole history as a block of squares: a row for each year, a
/// square for each fortnight, brighter the more was said. It fills in as the
/// read goes back, and the signal square is where the read has got to.
struct HistoryGrid: View {
    private static let perYear = 26
    private static let labelCells = 4
    private static let mostYears = 20

    private let counts: [Int: [Int]]
    private let years: [Int]
    private let busiest: Int
    private let place: (year: Int, cell: Int)?

    init(days: [String: Int], reached: Date? = nil) {
        var counts: [Int: [Int]] = [:]
        for (day, count) in days {
            let parts = day.split(separator: "-").compactMap { Int($0) }
            guard parts.count == 3 else { continue }
            counts[parts[0], default: Array(repeating: 0, count: Self.perYear)][Self.cell(month: parts[1], day: parts[2])] += count
        }
        let place = reached.map { date -> (year: Int, cell: Int) in
            let parts = Calendar.current.dateComponents([.year, .month, .day], from: date)
            return (parts.year ?? 0, Self.cell(month: parts.month ?? 1, day: parts.day ?? 1))
        }
        let known = Array(counts.keys) + (place.map { [$0.year] } ?? [])
        let newest = known.max() ?? Calendar.current.component(.year, from: Date())
        let oldest = max(known.min() ?? newest, newest - Self.mostYears + 1)
        self.counts = counts
        self.years = Array(stride(from: newest, through: oldest, by: -1))
        self.busiest = counts.values.joined().max() ?? 0
        self.place = place
    }

    private static func cell(month: Int, day: Int) -> Int {
        let starts = [0, 31, 59, 90, 120, 151, 181, 212, 243, 273, 304, 334]
        return min(perYear - 1, (starts[min(max(month - 1, 0), 11)] + max(day - 1, 0)) / 14)
    }

    var body: some View {
        Canvas { context, size in
            let pitch = size.width / CGFloat(Self.perYear + Self.labelCells)
            let square = pitch * 0.74
            for (row, year) in years.enumerated() {
                let top = CGFloat(row) * pitch
                let label = Text(verbatim: String(year)).font(.system(size: pitch * 0.9, weight: .medium, design: .monospaced)).foregroundStyle(Theme.dim)
                context.draw(label, at: CGPoint(x: 0, y: top + square / 2), anchor: .leading)
                let cells = counts[year]
                for cell in 0..<Self.perYear {
                    let box = CGRect(x: CGFloat(Self.labelCells + cell) * pitch, y: top, width: square, height: square)
                    let count = cells?[cell] ?? 0
                    let share = busiest > 0 ? (Double(count) / Double(busiest)).squareRoot() : 0
                    var color = count > 0 ? Theme.ink.opacity(0.22 + 0.78 * share) : Color.white.opacity(0.06)
                    if let place, place.year == year, place.cell == cell { color = Theme.signal }
                    context.fill(Path(box), with: .color(color))
                }
            }
        }
        .aspectRatio(CGFloat(Self.perYear + Self.labelCells) / CGFloat(max(years.count, 1)), contentMode: .fit)
        .accessibilityElement()
        .accessibilityLabel("History from \(years.last.map(String.init) ?? "") to \(years.first.map(String.init) ?? "")")
    }
}
