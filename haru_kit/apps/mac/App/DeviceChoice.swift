// Which audio devices to use. The user chooses a device by hand, or leaves the
// Choice on "Automatic" (an empty choice): then the app follows the system and
// Its own preference after each device change. A device chosen by hand stays
// While it exists, and the choice goes back to automatic when it disappears.

import Foundation

/// The microphone that "Automatic" picks: a wired or USB microphone (the built-in
/// One adds ~30 ms of processing, and a Bluetooth one switches the headphones to
/// Call mode), then the built-in microphone, then the system default.
func automaticInput(_ names: [String], systemDefault: String?) -> String {
    let skip = ["MacBook", "AirPods", "iPhone", "BlackHole", "Teams", "Zoom", "Bluetooth"]
    return names.first { name in !skip.contains { name.contains($0) } }
        ?? names.first { $0.contains("MacBook") && $0.contains("Microphone") }
        ?? systemDefault.flatMap { names.contains($0) ? $0 : nil }
        ?? names.first ?? ""
}

/// The output that "Automatic" picks: the system default.
func automaticOutput(_ names: [String], systemDefault: String?) -> String {
    systemDefault.flatMap { names.contains($0) ? $0 : nil } ?? names.first ?? ""
}

/// The choice after a device change (empty: automatic) and the device in effect.
func resolveDevice(choice: String, available: [String], automatic: String) -> (choice: String, device: String) {
    if !choice.isEmpty, available.contains(choice) { return (choice, choice) }
    return ("", automatic)
}
