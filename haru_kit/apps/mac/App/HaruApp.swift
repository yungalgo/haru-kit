// Modified from higurashi-ai/Haru (Apache-2.0) for haru-kit.
// MacOS app for Haru: native audio I/O, the model in
// A Python server process that the app starts when needed.

import SwiftUI

@main
struct HaruApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) private var delegate
    @StateObject private var model = AppModel()

    var body: some Scene {
        WindowGroup("Haru") {
            ContentView()
                .environmentObject(model)
                .frame(minWidth: 920, minHeight: 760)
                .preferredColorScheme(.light)
                .onAppear {
                    delegate.model = model
                    model.launch()
                    model.seedLibrary()
                }
        }
    }
}

/// Asks before quitting with unsaved changes to the character, or an unsaved last conversation.
final class AppDelegate: NSObject, NSApplicationDelegate {
    weak var model: AppModel?

    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        guard let model else { return .terminateNow }
        return model.confirmDiscard() && model.confirmLoseConversation() ? .terminateNow : .terminateCancel
    }
}
