import SwiftUI

@main
struct UnbubbleApp: App {
    @State private var engine = ExportEngine()
    @Environment(\.scenePhase) private var scenePhase

    var body: some Scene {
        WindowGroup {
            RootView()
                .environment(engine)
                .preferredColorScheme(.dark)
        }
        .onChange(of: scenePhase) { _, phase in
            engine.sceneChanged(to: phase)
        }
    }
}
