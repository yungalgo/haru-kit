// Renders the app's screens in given states to PNG files, without the model
// Server, to check the layout (Tests/screens.sh builds and runs it).
//   Screens <assets.json> <output dir>
// Assets.json: the native server's ASSETS (haru_mlx.native.bundled_assets()).
// The user's library is not changed: own items are added in memory only.

import AppKit
import SwiftUI

@main
struct Screens {
    static func main() {
        let app = NSApplication.shared
        app.setActivationPolicy(.prohibited)
        let args = CommandLine.arguments
        let assets = readJSONObject(URL(fileURLWithPath: args[1])) ?? [:]
        let out = URL(fileURLWithPath: args[2])
        try? FileManager.default.createDirectory(at: out, withIntermediateDirectories: true)
        // The renders change the kept language; put the user's back at the end.
        _ = UILanguage.current  // Moves an old app-language key first
        let kept = UserDefaults.standard.object(forKey: "language")

        /// A model that is ready, in English (each render starts from the same language).
        func fresh() -> AppModel {
            UILanguage.current = "en"
            let model = AppModel()
            model.apply(assets)
            return model
        }

        func renderView(_ name: String, _ root: some View, width: CGFloat = 920, height: CGFloat, wait: Double = 0.3) {
            let view = root.frame(width: width, height: height)
            let host = NSHostingView(rootView: view)
            host.frame = NSRect(x: 0, y: 0, width: width, height: height)
            let window = NSWindow(contentRect: host.frame, styleMask: [.borderless], backing: .buffered, defer: false)
            window.contentView = host
            host.layoutSubtreeIfNeeded()
            RunLoop.main.run(until: Date().addingTimeInterval(wait))
            guard let rep = host.bitmapImageRepForCachingDisplay(in: host.bounds) else { return }
            host.cacheDisplay(in: host.bounds, to: rep)
            try? rep.representation(using: .png, properties: [:])?.write(to: out.appendingPathComponent("\(name).png"))
            print("wrote \(name).png")
        }

        func render(_ name: String, _ model: AppModel, height: CGFloat = 3400) {
            renderView(name, ContentView().environmentObject(model), height: height)
        }

        /// The Browse sheet (a sheet does not render offscreen: its view alone, at its size).
        func renderSheet(_ name: String, _ model: AppModel) {
            renderView(name, RegistrySheet().environmentObject(model), width: 640, height: 560, wait: 1.5)
        }

        // First run: Haru in the current language, its voice, Ready to start.
        let first = fresh()
        render("1-setup", first)

        // An own character with only an English version, shown in French: F3a.
        let own = fresh()
        var mine = LibraryCharacter(id: "u-000000000001", versions: [
            "en": CharacterVersion(title: "Coach", summary: "A running coach.", voice: "en-parker",
                                   card: CardFields(["persona": "A calm running coach.", "name": "Sam"])),
        ])
        own.characters.append(mine)
        own.selectCharacter(mine.id)
        own.draft.card.situation = "Before a race."  // Unsaved
        render("2-own-unsaved", own)
        own.revertDraft()
        own.selectLanguage("fr")
        render("3-missing-version", own)
        own.writeVersion()
        render("4-writing-version", own)

        // Two versions: "delete the version" shows.
        let two = fresh()
        mine.versions["ja"] = CharacterVersion(title: "コーチ", voice: "ja-aiko", card: CardFields(["persona": "落ち着いたコーチ。"]))
        two.characters.append(mine)
        two.selectCharacter(mine.id)
        two.useVoice("missing-voice")
        render("5-voice-missing", two)

        // Recording a voice, then the form with the trimmed preview.
        let voice = fresh()
        voice.isRecording = true
        voice.recordingSeconds = 4.2
        render("6-recording", voice)
        voice.isRecording = false
        if let first = voice.libraryVoices.first { voice.prepareNewVoice(first.audioURL, text: "") }
        render("7-new-voice", voice)

        // Microphone refused.
        let denied = fresh()
        denied.microphoneDenied = true
        render("8-microphone-denied", denied, height: 1400)

        // Launch: the model download, then a failure with Try again.
        let launch = AppModel()
        launch.downloadPercent = 37
        launch.status = t("launch.downloading", ["percent": 37])
        render("9-downloading", launch, height: 900)
        launch.status = t("launch.downloadFailed")
        launch.serverFailed = true
        render("10-download-failed", launch, height: 900)

        // The live view: muted, 2 min 5 s in, some model text.
        let live = fresh()
        live.phase = .talking
        live.talkStarted = Date().addingTimeInterval(-125)
        live.muted = true
        live.waves.text("Hello! I'm Haru. What did you do this weekend? I went running by the river.", heardAt: now() - 5)
        render("11-live", live, height: 820)

        // An own character with author, version and date, whose English text uses a Japanese voice: R1, R3.
        let mismatch = fresh()
        var mika = LibraryCharacter(id: "u-000000000002", versions: [
            "en": CharacterVersion(title: "Mika", summary: "A friend.", voice: "ja-aiko",
                                   card: CardFields(["persona": "A cheerful friend.", "name": "Mika"])),
        ])
        mika.author = "Ana"
        mika.version = "1.0"
        mika.updated = "2026-11-15"
        mismatch.characters.append(mika)
        mismatch.selectCharacter(mika.id)
        render("12-voice-language-mismatch", mismatch)

        // An upload with more than 180 s of speech: the model uses the last 180 s.
        voice.newVoice?.speechSeconds = 200
        render("13-long-voice", voice)

        // Browse characters: loading, unreachable with Try again, empty, and a list with
        // Add (with a picture), In your library, Update, one being added, and a failed add.
        let browse = fresh()
        browse.registryList = .loading
        renderSheet("14-registry-loading", browse)
        browse.registryList = .failed(t("registry.unreachable"))
        renderSheet("15-registry-unreachable", browse)
        browse.registryList = .loaded([])
        renderSheet("16-registry-empty", browse)
        let index = URL(string: "http://localhost:8970/index.json")!
        let picture = browse.characters.first.flatMap(browse.imageURL)
        func entry(_ id: String, _ version: String, _ titles: [String: String], image: URL? = nil) -> RegistryEntry {
            var item = RegistryEntry(json: ["id": id, "author": "haru-kit", "version": version, "updated": "2026-11-15",
                                            "languages": Array(titles.keys).sorted(), "titles": titles,
                                            "summaries": titles.mapValues { "\($0) likes to talk about coffee, music and long walks by the river." },
                                            "image": "", "haru": "packs/\(id).haru", "size": 1,
                                            "sha256": String(repeating: "0", count: 64)], index: index)!
            item.image = image
            return item
        }
        for (id, version) in [("haru-kit-kai", "1.0"), ("haru-kit-ren", "0.9")] {
            var own = LibraryCharacter(id: id, versions: ["en": CharacterVersion(title: id)])
            own.version = version
            browse.characters.append(own)
        }
        browse.registryList = .loaded([
            entry("haru-kit-mika", "1.0", ["en": "Mika", "ja": "ミカ"], image: picture),
            entry("haru-kit-kai", "1.0", ["en": "Kai", "ko": "카이", "de": "Kai"]),
            entry("haru-kit-ren", "1.1", ["ja": "レン"]),
            entry("haru-kit-sora", "1.0", ["en": "Sora", "fr": "Sora"]),
        ])
        browse.registryAdding = "haru-kit-sora"
        renderSheet("17-registry-list", browse)
        browse.registryAdding = nil
        browse.registryError = t("file.notHaru")
        renderSheet("18-registry-add-failed", browse)

        // A new character: no picture yet, so Save is off and the hint shows. Not saved: in memory only.
        let new = fresh()
        new.newCharacter()
        render("19-new-character", new)

        // The first start while the registry's characters are added.
        let seeding = fresh()
        seeding.seeding = true
        render("20-library-adding", seeding, height: 1400)

        // The globe menu set to Japanese: the app's text, the tile titles, the filter and the selected
        // Character's version (コーチ) are all in Japanese.
        let one = fresh()
        one.characters.append(mine)
        one.selectCharacter(mine.id)
        one.selectLanguage("ja")
        render("21-one-language", one, height: 1400)
        UserDefaults.standard.set(kept, forKey: "language")
        exit(0)
    }
}
