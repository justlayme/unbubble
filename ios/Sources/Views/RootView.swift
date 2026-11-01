import SwiftUI

struct RootView: View {
    @Environment(ExportEngine.self) private var engine

    var body: some View {
        ZStack {
            Theme.ground.ignoresSafeArea()
            ScrollView {
                VStack(alignment: .leading, spacing: 18) {
                    switch engine.phase {
                    case .idle: HomeView()
                    case .opening, .reading, .paused: ReadingView()
                    case .finished: FinishedView()
                    }
                    if let line = engine.errorLine {
                        Text(line).font(.footnote).foregroundStyle(.orange)
                    }
                }
                .padding(20)
            }
        }
        .foregroundStyle(Theme.ink)
    }
}

private struct Brand: View {
    var body: some View {
        Text("UNBUBBLE").font(Theme.mono.weight(.semibold)).tracking(3).foregroundStyle(Theme.quiet)
    }
}

private struct HomeView: View {
    @Environment(ExportEngine.self) private var engine

    var body: some View {
        Brand()
        Text("Get your messages out of their bubbles.")
            .font(.system(size: 34, weight: .semibold))
            .fixedSize(horizontal: false, vertical: true)
        Text("Export one conversation from this iPhone as a PDF or a spreadsheet, with every date and time. No computer. Your messages stay on your iPhone.")
            .foregroundStyle(Theme.quiet)
        step(1, "Add the Shortcut", "Apple only lets Shortcuts read Messages. The first time, Unbubble opens it for you: tap Add Shortcut and come back. You only do this once.", done: engine.shortcutAdded)
        step(2, "Pick a conversation", "Stay on this screen, unlocked and plugged in. Tap Always Allow whenever iPhone asks.", done: false)
        PrimaryButton(title: "Start export") { Task { await engine.start() } }
        if engine.shortcutAdded {
            QuietButton(title: "Add the Shortcut again") { Task { await engine.addShortcut() } }
        }
    }

    private func step(_ number: Int, _ title: String, _ detail: String, done: Bool) -> some View {
        HStack(alignment: .top, spacing: 12) {
            Text(done ? "✓" : "\(number)").font(Theme.mono.weight(.bold)).foregroundStyle(Theme.signal).frame(width: 22)
            VStack(alignment: .leading, spacing: 4) {
                Text(title).font(.headline)
                Text(detail).font(.subheadline).foregroundStyle(Theme.quiet).fixedSize(horizontal: false, vertical: true)
            }
        }
        .padding(16)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Theme.card, in: RoundedRectangle(cornerRadius: 14))
    }
}

private struct ReadingView: View {
    @Environment(ExportEngine.self) private var engine

    var body: some View {
        Brand()
        Text(title).font(.title.weight(.semibold)).fixedSize(horizontal: false, vertical: true)
        UnbubbleMark(mine: engine.mine, theirs: engine.theirs)
        Text(count.formatted())
            .font(.system(size: 52, weight: .light)).monospacedDigit()
            .contentTransition(.numericText(value: Double(count)))
            .animation(.default, value: count)
        if let pass, !pass.windows.isEmpty {
            ProgressView(value: Double(pass.index), total: Double(pass.windows.count)).tint(Theme.signal)
        } else if let back = engine.reachedBack {
            Text("Reading back through \(back.formatted(.dateTime.month(.wide).year()))").font(.subheadline.weight(.semibold)).foregroundStyle(Theme.signal)
        }
        LazyVGrid(columns: [GridItem(.flexible(), alignment: .leading), GridItem(.flexible(), alignment: .leading)], alignment: .leading, spacing: 12) {
            if let pass {
                figure("Stretches left", max(pass.windows.count - pass.index, 0))
                VStack(alignment: .leading, spacing: 2) {
                    Text("Saved so far").font(.caption).foregroundStyle(Theme.quiet).lineLimit(1)
                    Text(pass.bytes.formatted(.byteCount(style: .file))).font(.title3).monospacedDigit()
                }
            } else {
                figure("You", engine.mine)
                figure(engine.progress.chat.isEmpty ? "Them" : engine.progress.chat, engine.theirs)
                figure("Days found", engine.activeDays)
                figure("Busiest day", engine.busiestDay)
            }
        }
        Text(detail).font(.subheadline).foregroundStyle(Theme.quiet).fixedSize(horizontal: false, vertical: true)
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
            Button("Cancel and delete") { engine.clear() }.font(.footnote).foregroundStyle(Theme.quiet)
        }
    }

    private var pass: FilePass? { engine.isFetchingFiles ? engine.progress.files : nil }
    private var count: Int { pass?.saved ?? engine.messageCount }

    private var title: String {
        switch engine.phase {
        case .opening: "Opening Shortcuts"
        case .paused: "The export paused"
        default:
            if pass != nil { "Saving photos and files" }
            else if engine.progress.chat.isEmpty { "Reading your messages" }
            else { "Reading \(engine.progress.chat)" }
        }
    }

    private var detail: String {
        switch engine.phase {
        case .opening: pass == nil ? "Tap Always Allow whenever iPhone asks, then pick the conversation." : "Tap Always Allow whenever iPhone asks."
        case .paused: "Nothing is lost. Everything so far is saved on this iPhone. Resume to carry on from there, or finish with what you have."
        default: "Keep this screen open, unlocked and plugged in. Shortcuts may flash up now and then, then brings you back. Long histories can take hours. Your messages stay on this iPhone."
        }
    }

    private func figure(_ label: String, _ value: Int) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(label).font(.caption).foregroundStyle(Theme.quiet).lineLimit(1)
            Text(value.formatted()).font(.title3).monospacedDigit()
        }
    }
}

private struct FinishedView: View {
    @Environment(ExportEngine.self) private var engine
    @State private var file: ExportFile?
    @State private var working: Double?
    @State private var failure: String?
    @Environment(\.openURL) private var openURL

    var body: some View {
        Brand()
        if engine.hasExport {
            Text("\(engine.messageCount.formatted()) messages with \(engine.progress.chat)")
                .font(.title.weight(.semibold)).fixedSize(horizontal: false, vertical: true)
            if let oldest = engine.progress.oldest, let newest = engine.progress.newest {
                Text("\(Date(timeIntervalSince1970: oldest).formatted(date: .abbreviated, time: .omitted)) to \(Date(timeIntervalSince1970: newest).formatted(date: .abbreviated, time: .omitted))")
                    .foregroundStyle(Theme.quiet)
            }
            option("PDF", "A printable record with dates, times, senders, numbered messages and pages, and a fingerprint of its contents. Useful for legal matters; ask your attorney what your court requires.") { export(.pdf) }
            option("CSV", "A spreadsheet with a timestamp, sender and direction for every message.") { export(.csv) }
            if savedFiles > 0 {
                option("CSV with attachments", "A .zip of the spreadsheet and a folder of \(savedFiles.formatted()) photos and files, each named beside its message. The PDF shows the photos too.") { export(.bundle) }
                if pass?.stoppedEarly == true {
                    QuietButton(title: "Get the rest of the photos and files") { Task { await engine.fetchFiles() } }
                }
            } else {
                option("Add photos and files", "Goes back through this conversation and saves its photos, videos and files on this iPhone. Then the PDF shows the photos and the CSV comes with a folder of everything. It takes longer than the text did.") {
                    Task { await engine.fetchFiles() }
                }
            }
            if let skipped = pass?.skipped, skipped > 0 {
                Text("iPhone couldn't hand over the attachments for \(skipped.formatted()) stretches of this conversation, so a few may be missing.")
                    .font(.footnote).foregroundStyle(Theme.quiet).fixedSize(horizontal: false, vertical: true)
            }
            if engine.unknown > 0 {
                Text("Unbubble couldn't tell which of you sent \(engine.unknown.formatted()) messages. They're marked Unknown, under the name iPhone gave them.")
                    .font(.footnote).foregroundStyle(Theme.quiet).fixedSize(horizontal: false, vertical: true)
            }
            if let working {
                ProgressView(value: working).tint(Theme.signal)
            }
            if let failure { Text(failure).font(.footnote).foregroundStyle(.orange) }

            VStack(alignment: .leading, spacing: 8) {
                Text("What do these messages really say?").font(.headline)
                Text("The Grey Mirror reads a whole relationship's messages and shows you the patterns: who reached out, who pulled back, and when it changed.")
                    .font(.subheadline).foregroundStyle(Theme.quiet).fixedSize(horizontal: false, vertical: true)
                Button("See The Grey Mirror") { openURL(ExportEngine.greyMirrorLink) }
                    .font(.subheadline.weight(.semibold)).foregroundStyle(Theme.signal)
            }
            .padding(16)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(Theme.card, in: RoundedRectangle(cornerRadius: 14))

            Button("Delete this export and start over") { engine.clear() }.font(.footnote).foregroundStyle(Theme.quiet)
                .sheet(item: $file) { ShareSheet(url: $0.url) }
        } else {
            Text("No messages came through").font(.title.weight(.semibold))
            Text("Shortcuts didn't hand over anything from this conversation. Check that you tapped Always Allow, and that Messages on this iPhone shows the conversation, then try again.")
                .foregroundStyle(Theme.quiet).fixedSize(horizontal: false, vertical: true)
            PrimaryButton(title: "Start again") { engine.clear() }
        }
    }

    private var pass: FilePass? { engine.progress.files }
    private var savedFiles: Int { pass?.saved ?? 0 }

    private enum Format { case pdf, csv, bundle }

    private func option(_ title: String, _ detail: String, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            VStack(alignment: .leading, spacing: 6) {
                Text(title).font(.title3.weight(.semibold)).foregroundStyle(Theme.signal)
                Text(detail).font(.subheadline).foregroundStyle(Theme.quiet).multilineTextAlignment(.leading)
                    .fixedSize(horizontal: false, vertical: true)
            }
            .padding(16)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(Theme.card, in: RoundedRectangle(cornerRadius: 14))
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
