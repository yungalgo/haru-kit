// Swiftc -parse-as-library App/DeviceChoice.swift Tests/Check.swift Tests/DeviceChoiceTests.swift

@main
struct DeviceChoiceTests {
    static func main() {
        let mics = ["MacBook Pro Microphone", "AirPods Pro", "USB Audio Device"]
        check(automaticInput(mics, systemDefault: "AirPods Pro") == "USB Audio Device", "wired or USB microphone first")
        check(automaticInput(["MacBook Pro Microphone", "AirPods Pro"], systemDefault: "AirPods Pro")
              == "MacBook Pro Microphone", "then the built-in microphone")
        check(automaticInput(["AirPods Pro", "BlackHole 2ch"], systemDefault: "AirPods Pro") == "AirPods Pro",
              "then the system default")
        check(automaticInput([], systemDefault: nil) == "", "no microphone")
        check(automaticOutput(["MacBook Pro Speakers", "External Headphones"], systemDefault: "External Headphones")
              == "External Headphones", "output follows the system default")
        check(automaticOutput(["MacBook Pro Speakers"], systemDefault: "Gone") == "MacBook Pro Speakers",
              "a missing default gives the first output")

        // Automatic follows each change.
        var r = resolveDevice(choice: "", available: ["A", "B"], automatic: "B")
        check(r.choice == "" && r.device == "B", "automatic uses the automatic device")
        // A hand choice stays while it exists, even when the automatic device changes.
        r = resolveDevice(choice: "A", available: ["A", "B", "C"], automatic: "C")
        check(r.choice == "A" && r.device == "A", "hand choice stays")
        // It goes back to automatic when the device disappears.
        r = resolveDevice(choice: "A", available: ["B", "C"], automatic: "C")
        check(r.choice == "" && r.device == "C", "hand choice gone: back to automatic")
        // When the device comes back, the choice is still automatic.
        r = resolveDevice(choice: r.choice, available: ["A", "B", "C"], automatic: "C")
        check(r.choice == "" && r.device == "C", "stays automatic after the device returns")
        finish("DeviceChoiceTests")
    }
}
