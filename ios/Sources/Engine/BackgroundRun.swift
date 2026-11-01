import BackgroundTasks
import Foundation

/// Keeps this app running while the Shortcut reads, so it can still answer when
/// Shortcuts or anything else is on screen. iOS shows the run's progress itself
/// and lets the reader stop it from there.
@MainActor
final class BackgroundRun {
    private static let prefix = "me.justlay.unbubble.export."

    private var task: BGContinuedProcessingTask?
    private var isStarting = false

    /// Called when the reader stops the run from iOS's own progress view.
    var onExpired: (() -> Void)?

    /// Only works while the app is on screen, which is when a run starts.
    func start(title: String) {
        guard task == nil, !isStarting else { return }
        isStarting = true
        let identifier = Self.prefix + UUID().uuidString
        let scheduler = BGTaskScheduler.shared
        let registered = scheduler.register(forTaskWithIdentifier: identifier, using: .main) { [weak self] task in
            MainActor.assumeIsolated {
                guard let task = task as? BGContinuedProcessingTask else {
                    task.setTaskCompleted(success: false)
                    return
                }
                self?.adopt(task)
            }
        }
        guard registered else { isStarting = false; return }
        let request = BGContinuedProcessingTaskRequest(identifier: identifier, title: title, subtitle: "Starting")
        request.strategy = .fail
        do { try scheduler.submit(request) } catch { isStarting = false }
    }

    private func adopt(_ task: BGContinuedProcessingTask) {
        self.task = task
        isStarting = false
        task.progress.totalUnitCount = 1000
        task.expirationHandler = { [weak self] in
            MainActor.assumeIsolated {
                self?.task = nil
                self?.onExpired?()
            }
        }
    }

    /// `fraction` is how far through the phone's history the run has got.
    func report(fraction: Double, title: String, subtitle: String) {
        guard let task else { return }
        task.progress.completedUnitCount = Int64(min(max(fraction, 0), 0.999) * 1000)
        task.updateTitle(title, subtitle: subtitle)
    }

    func finish() {
        task?.progress.completedUnitCount = 1000
        task?.setTaskCompleted(success: true)
        task = nil
    }
}
