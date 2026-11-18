// A library for the tests, made in a folder: the character "haru" with a version
// In each of the 9 languages, its 9 voices (generated tones, as WAV) and its picture.
// Built with App/Library.swift and App/AudioFiles.swift.

import Foundation

let fixtureVoices = ["de": "de-sebastian", "en": "en-parker", "es": "es-fernanda", "fr": "fr-ines", "ja": "ja-aiko",
                     "ko": "ko-taehyun", "pt": "pt-eloa", "ru": "ru-tatiana", "zh": "zh-jing"]

/// A 1x1 PNG.
let fixturePNG = Data(base64Encoded:
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==")!

/// 1 s of silence, 5 s of a tone that swells like speech, 1 s of silence, at 24 kHz.
func writeFixtureClip(_ url: URL) throws {
    let rate = 24_000
    let tone = (0..<5 * rate).map { i -> Float in
        let t = Float(i) / Float(rate)
        return 0.3 * sin(2 * .pi * 220 * t) * (0.6 + 0.4 * sin(2 * .pi * 3 * t))
    }
    let silence = [Float](repeating: 0, count: rate)
    try writeWav(silence + tone + silence, rate: rate, to: url)
}

/// Writes the fixture library into `root`.
func makeFixtureLibrary(_ root: URL) throws {
    let store = LibraryStore(root: root)
    let clip = root.appendingPathComponent("clip.wav")
    try writeFixtureClip(clip)
    defer { try? FileManager.default.removeItem(at: clip) }
    var versions: [String: CharacterVersion] = [:]
    for (language, id) in fixtureVoices {
        try store.save(LibraryVoice(id: id, name: id, language: language, gender: "female", seconds: 7,
                                    file: "\(id).wav"), audio: clip)
        versions[language] = CharacterVersion(title: "Haru", summary: "A friend.", voice: id,
                                              card: CardFields(["persona": "A friend.", "name": "Haru"]))
    }
    let picture = root.appendingPathComponent("haru-source.png")
    try fixturePNG.write(to: picture)
    defer { try? FileManager.default.removeItem(at: picture) }
    var haru = LibraryCharacter(id: "haru", versions: versions)
    haru.author = "haru-kit"
    haru.version = "1.0"
    haru.image = try store.savePicture(picture, for: "haru")
    try store.save(haru)
}
