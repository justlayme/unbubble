import SwiftUI

struct RootView: View {
    @Environment(ExportEngine.self) private var engine

    var body: some View {
        ZStack {
            Theme.ground.ignoresSafeArea()
            ScrollView {
                VStack(alignment: .leading, spacing: 22) {
                    switch engine.phase {
                    case .idle: HomeView()
                    case .opening, .reading, .paused: ReadingView()
                    case .finished: FinishedView()
                    }
                    if let line = engine.errorLine {
                        Text(line).font(.footnote).foregroundStyle(.orange)
                    }
                }
                .padding(.horizontal, 22)
                .padding(.vertical, 14)
            }
            .scrollBounceBehavior(.basedOnSize)
        }
        .foregroundStyle(Theme.ink)
    }
}

/// Apple added the Messages actions in iOS 26, but on iOS 26 Shortcuts stops with
/// "There was a problem running" as soon as a conversation is picked (an iPhone 11
/// on iOS 26.6.3, 2026-11-02). The export works on iOS 27.
let minimumExportIOS = 27
var canExportOnThisIOS: Bool { ProcessInfo.processInfo.operatingSystemVersion.majorVersion >= minimumExportIOS }

private struct HomeView: View {
    @Environment(ExportEngine.self) private var engine

    var body: some View {
        PixelMark(alive: true).frame(height: 208).frame(maxWidth: .infinity).padding(.top, 6)
        Text("Get your messages out of their bubbles.")
            .font(.system(size: 38, weight: .semibold)).tracking(-0.7)
            .fixedSize(horizontal: false, vertical: true)
        Text("Export a whole conversation as a printable PDF or a spreadsheet, with every date and time. No computer, and it all stays on your iPhone.")
            .foregroundStyle(Theme.soft).fixedSize(horizontal: false, vertical: true)
        if canExportOnThisIOS { steps } else { needsUpdate }
    }

    // Starting would only end in Shortcuts' error, so say what to do instead.
    private var needsUpdate: some View {
        VStack(alignment: .leading, spacing: 10) {
            Seam()
            Text("NEEDS iOS \(minimumExportIOS) OR LATER").font(Theme.label).foregroundStyle(.orange)
            Text("This iPhone is on iOS \(ProcessInfo.processInfo.operatingSystemVersion.majorVersion), where Apple's Messages actions in Shortcuts stop with an error as soon as you pick a conversation.")
                .font(.subheadline).fixedSize(horizontal: false, vertical: true)
            Text("Update for free in Settings → General → Software Update, then open Unbubble again. If this iPhone can't update to iOS \(minimumExportIOS), plug it into a Windows PC and use the free Unbubble app for Windows instead.")
                .font(.footnote).foregroundStyle(Theme.soft).fixedSize(horizontal: false, vertical: true)
            Seam()
        }
    }

    @ViewBuilder private var steps: some View {
        VStack(spacing: 0) {
            Seam()
            step("01", "Add the Shortcut", "Apple only lets Shortcuts read Messages. The first time, tap Add Shortcut and come back.", done: engine.shortcutAdded)
            Seam()
            step("02", "Pick a conversation", "Tap Always Allow whenever iPhone asks.", done: false)
            Seam()
            step("03", "Keep this screen open", "Unlocked and plugged in. Long histories can take a while.", done: false)
            Seam()
        }
        PrimaryButton(title: "Start export") { Task { await engine.start() } }
        if engine.shortcutAdded {
            QuietButton(title: "Add the Shortcut again") { Task { await engine.addShortcut() } }
        }
    }

    private func step(_ number: String, _ title: String, _ detail: String, done: Bool) -> some View {
        HStack(alignment: .firstTextBaseline, spacing: 14) {
            Text(done ? "OK" : number).font(Theme.label).foregroundStyle(done ? Theme.signal : Theme.dim).frame(width: 22, alignment: .leading)
            VStack(alignment: .leading, spacing: 3) {
                Text(title).font(.subheadline.weight(.semibold))
                Text(detail).font(.footnote).foregroundStyle(Theme.soft).fixedSize(horizontal: false, vertical: true)
            }
            Spacer(minLength: 0)
        }
        .padding(.vertical, 12)
    }
}

private struct ReadingView: View {
    @Environment(ExportEngine.self) private var engine

    var body: some View {
        Wordmark()
        VStack(alignment: .leading, spacing: 8) {
            SmallLabel(text: title, color: engine.phase == .paused ? .orange : Theme.signal)
            if count > 0 {
                PixelText(text: count.formatted(), live: engine.phase == .reading)
                Text(unit).font(.subheadline).foregroundStyle(Theme.soft)
            } else {
                PixelMark(alive: true).frame(height: 180).frame(maxWidth: .infinity).padding(.vertical, 6)
            }
        }
        if let pass {
            ProgressView(value: Double(pass.index), total: Double(max(pass.windows.count, 1))).tint(Theme.signal)
            HStack {
                figure("Stretches left", max(pass.windows.count - pass.index, 0).formatted())
                figure("Saved so far", pass.bytes.formatted(.byteCount(style: .file)))
            }
        } else if count > 0 {
            HistoryGrid(days: engine.progress.days, reached: engine.reachedBack)
            VStack(alignment: .leading, spacing: 10) {
                HStack {
                    figure("You", engine.mine.formatted())
                    figure(engine.progress.chat.isEmpty ? "Them" : engine.progress.chat, engine.theirs.formatted())
                }
                SplitBar(mine: engine.mine, theirs: engine.theirs)
            }
        }
        Text(detail).font(.footnote).foregroundStyle(Theme.soft).fixedSize(horizontal: false, vertical: true)
        if engine.phase == .paused {
            PrimaryButton(title: "Resume in Shortcuts") { Task { await engine.resume() } }
        }
        if pass != nil {
            QuietButton(title: "Stop and keep these") { engine.stopFiles() }
        } else {
            if engine.hasExport {
                QuietButton(title: "Finish with these \(engine.messageCount.formatted())") { engine.finishNow() }
            }
            if engine.phase == .opening {
                QuietButton(title: "Add the Shortcut again") { Task { await engine.addShortcut() } }
            }
            Button("Cancel and delete") { engine.clear() }.font(.footnote).foregroundStyle(Theme.dim)
        }
    }

    private var pass: FilePass? { engine.isFetchingFiles ? engine.progress.files : nil }
    private var count: Int { pass?.saved ?? engine.messageCount }

    private var title: String {
        switch engine.phase {
        case .opening: "Opening Shortcuts"
        case .paused: "Paused"
        default:
            if pass != nil { "Saving photos and files" }
            else if engine.progress.chat.isEmpty { "Reading your messages" }
            else { "Reading \(engine.progress.chat)" }
        }
    }

    private var unit: String {
        if pass != nil { return count == 1 ? "file saved" : "files saved" }
        guard let back = engine.reachedBack else { return "messages found" }
        return "messages found, back to \(back.formatted(.dateTime.month(.wide).year()))"
    }

    private var detail: String {
        switch engine.phase {
        case .opening: pass == nil ? "Tap Always Allow whenever iPhone asks, then pick the conversation." : "Tap Always Allow whenever iPhone asks."
        case .paused: "Nothing is lost. Everything so far is saved on this iPhone. Resume to carry on from there, or finish with what you have."
        default: "Keep this screen open, unlocked and plugged in. Shortcuts may flash up now and then, then brings you back. Your messages stay on this iPhone."
        }
    }

    private func figure(_ label: String, _ value: String) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            SmallLabel(text: label)
            Text(value).font(.system(.title3, design: .monospaced).weight(.medium))
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}

private struct FinishedView: View {
    @Environment(ExportEngine.self) private var engine
    @State private var file: ExportFile?
    @State private var working: Double?
    @State private var failure: String?
    @Environment(\.openURL) private var openURL

    var body: some View {
        Wordmark()
        if engine.hasExport {
            VStack(alignment: .leading, spacing: 8) {
                SmallLabel(text: engine.progress.chat, color: Theme.signal)
                PixelText(text: engine.messageCount.formatted())
                if let oldest = engine.progress.oldest, let newest = engine.progress.newest {
                    Text("messages, \(Date(timeIntervalSince1970: oldest).formatted(date: .abbreviated, time: .omitted)) to \(Date(timeIntervalSince1970: newest).formatted(date: .abbreviated, time: .omitted))")
                        .font(.subheadline).foregroundStyle(Theme.soft)
                }
            }
            HistoryGrid(days: engine.progress.days)
            VStack(spacing: 0) {
                Seam()
                option(.page, "PDF", "A printable record: dates, times, senders, numbered messages and pages, and a fingerprint of its contents. Ask your attorney what your court requires.") { export(.pdf) }
                Seam()
                option(.table, "CSV", "A spreadsheet with a timestamp, sender and direction for every message.") { export(.csv) }
                Seam()
                if savedFiles > 0 {
                    option(.folder, "CSV with attachments", "A .zip of the spreadsheet and a folder of \(savedFiles.formatted()) photos and files. The PDF shows the photos too.") { export(.bundle) }
                    Seam()
                } else {
                    option(.photo, "Add photos and files", "Goes back through this conversation for its photos, videos and files. They stay on this iPhone until you export them.") {
                        Task { await engine.fetchFiles() }
                    }
                    Seam()
                }
            }
            if savedFiles > 0, pass?.stoppedEarly == true {
                QuietButton(title: "Get the rest of the files") { Task { await engine.fetchFiles() } }
            }
            if let skipped = pass?.skipped, skipped > 0 {
                note("iPhone couldn't hand over the attachments for \(skipped.formatted()) stretches of this conversation, so a few may be missing.")
            }
            if engine.unknown > 0 {
                note("Unbubble couldn't tell which of you sent \(engine.unknown.formatted()) messages. They're marked Unknown, under the name iPhone gave them.")
            }
            if let working {
                ProgressView(value: working).tint(Theme.signal)
            }
            if let failure { Text(failure).font(.footnote).foregroundStyle(.orange) }

            Button { openURL(ExportEngine.greyMirrorLink) } label: {
                VStack(alignment: .leading, spacing: 8) {
                    SmallLabel(text: "From the same people")
                    Text("What do these messages really say?").font(.headline).foregroundStyle(Theme.ink)
                    Text("The Grey Mirror reads a whole relationship's messages and shows you the patterns: who reached out, who pulled back, and when it changed.")
                        .font(.footnote).foregroundStyle(Theme.soft).multilineTextAlignment(.leading)
                        .fixedSize(horizontal: false, vertical: true)
                    SmallLabel(text: "See The Grey Mirror", color: Theme.signal)
                }
                .padding(16)
                .frame(maxWidth: .infinity, alignment: .leading)
                .overlay(Rectangle().stroke(Theme.line))
            }

            Button("Delete this export and start over") { engine.clear() }.font(.footnote).foregroundStyle(Theme.dim)
                .sheet(item: $file) { ShareSheet(url: $0.url) }
        } else {
            Text("No messages came through").font(.title.weight(.semibold))
            Text("Shortcuts didn't hand over anything from this conversation. Check that you tapped Always Allow, and that Messages on this iPhone shows the conversation, then try again.")
                .foregroundStyle(Theme.soft).fixedSize(horizontal: false, vertical: true)
            PrimaryButton(title: "Start again") { engine.clear() }
        }
    }

    private var pass: FilePass? { engine.progress.files }
    private var savedFiles: Int { pass?.saved ?? 0 }

    private enum Format { case pdf, csv, bundle }

    private func note(_ text: String) -> some View {
        Text(text).font(.footnote).foregroundStyle(Theme.soft).fixedSize(horizontal: false, vertical: true)
    }

    private func option(_ glyph: PixelGlyph.Shape, _ title: String, _ detail: String, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            HStack(alignment: .top, spacing: 16) {
                PixelGlyph(shape: glyph).frame(width: 27).padding(.top, 2)
                VStack(alignment: .leading, spacing: 4) {
                    Text(title).font(.headline).foregroundStyle(Theme.ink)
                    Text(detail).font(.footnote).foregroundStyle(Theme.soft).multilineTextAlignment(.leading)
                        .fixedSize(horizontal: false, vertical: true)
                }
                Spacer(minLength: 0)
            }
            .padding(.vertical, 14)
            .contentShape(Rectangle())
        }
        .disabled(working != nil)
    }

    private func export(_ format: Format) {
        working = 0
        failure = nil
        Task {
            do {
                let record = try engine.record()
                let url: URL = try await Task.detached(priority: .userInitiated) {
                    switch format {
                    case .csv: return try Exporter.csv(record)
                    case .bundle: return try Exporter.bundle(record)
                    case .pdf: return try Exporter.pdf(record) { value in
                        Task { @MainActor in working = value }
                    }
                    }
                }.value
                working = nil
                file = ExportFile(url: url)
            } catch {
                working = nil
                failure = "Couldn't make the file: \(error.localizedDescription)"
            }
        }
    }
}

private struct ExportFile: Identifiable {
    let url: URL
    var id: URL { url }
}

private struct ShareSheet: UIViewControllerRepresentable {
    let url: URL
    func makeUIViewController(context: Context) -> UIActivityViewController {
        UIActivityViewController(activityItems: [url], applicationActivities: nil)
    }
    func updateUIViewController(_ controller: UIActivityViewController, context: Context) {}
}
