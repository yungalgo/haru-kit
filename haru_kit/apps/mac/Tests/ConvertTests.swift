// Every own voice is stored as a 24 kHz mono 16-bit WAV after the silence
// Trim, whatever the type of its file. run.sh makes clips with ffmpeg (FLAC, MP3,
// M4A, AAC, Ogg Opus, Opus, Ogg Vorbis, AIFF, CAF, WAV, WebM) and passes each with
// The sample count of upstream `prepare_reference(decode_audio(...))`
// ("-": the platform cannot decode it) in CONVERT_CLIPS, one "<path> <count>" a line.
// Tolerance: ±480 samples (one 20 ms window of the trim; decoders differ in the
// Encoder delay and padding of lossy files).

import AVFoundation
import Foundation

@main
struct ConvertTests {
    static let fm = FileManager.default
    static let tolerance = 480

    /// The header of a stored voice: PCM, one channel, 24 kHz, 16-bit; and its sample count.
    static func wavSamples(_ url: URL) -> Int? {
        guard let data = try? Data(contentsOf: url), data.count >= 44 else { return nil }
        func u16(_ at: Int) -> Int { Int(data[at]) | Int(data[at + 1]) << 8 }
        func u32(_ at: Int) -> Int { u16(at) | u16(at + 2) << 16 }
        guard String(decoding: data[0..<4], as: UTF8.self) == "RIFF", String(decoding: data[8..<16], as: UTF8.self) == "WAVEfmt ",
              u16(20) == 1, u16(22) == 1, u32(24) == 24_000, u16(34) == 16,
              String(decoding: data[36..<40], as: UTF8.self) == "data", u32(40) == data.count - 44 else { return nil }
        return u32(40) / 2
    }

    static func main() throws {
        guard let lines = ProcessInfo.processInfo.environment["CONVERT_CLIPS"] else {
            print("ConvertTests: CONVERT_CLIPS is not set")
            exit(1)
        }
        let clips = lines.split(separator: "\n").map { $0.split(separator: " ").map(String.init) }
        let work = fm.temporaryDirectory.appendingPathComponent("convert-test-\(UUID().uuidString)")
        try fm.createDirectory(at: work, withIntermediateDirectories: true)
        defer { try? fm.removeItem(at: work) }
        var kinds: [String] = []

        for clip in clips {
            let source = URL(fileURLWithPath: clip[0])
            let name = source.lastPathComponent
            let target = work.appendingPathComponent("\(name).wav")
            guard let expected = Int(clip[1]) else {
                // Not decodable here: the clear message, nothing stored.
                do {
                    try storeVoiceAudio(source, to: target)
                    check(false, "\(name): unreadable")
                } catch let error as AudioFileError {
                    check(error.localizedDescription == t("error.unreadable_audio"), "\(name): error.unreadable_audio")
                }
                check(!fm.fileExists(atPath: target.path), "\(name): nothing stored")
                kinds.append("\(name) unreadable")
                continue
            }
            let seconds = try storeVoiceAudio(source, to: target)
            let count = wavSamples(target)
            check(count != nil, "\(name): stored as WAV, 24 kHz, mono, 16-bit")
            check(abs((count ?? 0) - expected) <= tolerance, "\(name): \(count ?? 0) samples, upstream \(expected)")
            check(abs(seconds - Double(count ?? 0) / 24_000) < 1e-9, "\(name): seconds = stored length")
            // The stored form is stable: storing it again gives the same bytes.
            let again = work.appendingPathComponent("\(name).again.wav")
            try storeVoiceAudio(target, to: again)
            check((try? Data(contentsOf: again)) == (try? Data(contentsOf: target)), "\(name): stored again, same bytes")
            kinds.append("\(name) \((count ?? 0) - expected)")
        }
        print("ConvertTests: samples minus upstream: " + kinds.joined(separator: ", "))

        // A .haru file with an Ogg Opus voice (the registry's format): the import stores it as <id>.wav,
        // The voice file says so with the stored length, and the export has the WAV.
        guard let ogg = clips.first(where: { $0[0].hasSuffix("/clip.ogg") && Int($0[1]) != nil }).map({ URL(fileURLWithPath: $0[0]) }) else {
            check(false, "an Ogg Opus clip")
            return finish("ConvertTests")
        }
        let rootA = work.appendingPathComponent("a"), rootB = work.appendingPathComponent("b")
        for root in [rootA, rootB] { try fm.createDirectory(at: root, withIntermediateDirectories: true) }
        let a = LibraryStore(root: rootA)
        let voiceID = a.newID(), charID = a.newID()
        try a.save(LibraryVoice(id: voiceID, name: "Ogg", language: "en", gender: "", seconds: 20,
                                file: "\(voiceID).ogg"), audio: ogg)
        try a.save(LibraryCharacter(id: charID, versions: ["en": CharacterVersion(title: "O", voice: voiceID)]))
        let file = work.appendingPathComponent("ogg.haru")
        try storeZip(try a.exportCharacter(charID)).write(to: file)
        let folder = try unzip(file)
        defer { try? fm.removeItem(at: folder) }
        let b = LibraryStore(root: rootB)
        _ = try b.importHaru(folder) { _ in true }
        guard let voice = b.voice(voiceID) else {
            check(false, "Ogg voice imported")
            return finish("ConvertTests")
        }
        check(voice.file == "\(voiceID).wav" && b.character(charID)?.versions["en"]?.voice == voiceID, "imported as <id>.wav")
        check(voice.json["file"] as? String == "\(voiceID).wav", "the voice file names the WAV")
        let stored = wavSamples(voice.audioURL) ?? 0
        check(voice.seconds == (Double(stored) / 2_400).rounded() / 10, "seconds = stored length: \(voice.seconds)")
        let direct = work.appendingPathComponent("direct.wav")
        try storeVoiceAudio(ogg, to: direct)
        check((try? Data(contentsOf: voice.audioURL)) == (try? Data(contentsOf: direct)), "the import stores what storeVoiceAudio writes")
        check(!fm.fileExists(atPath: rootB.appendingPathComponent("voices/\(voiceID).ogg").path), "no Ogg kept")
        check(try Set(b.exportCharacter(charID).map(\.name)).contains("voices/\(voiceID).wav"), "export has the stored WAV")

        // Stored WAV opens for playback; on macOS 26+, raw Ogg Opus also opens directly in AVAudioPlayer.
        check(((try? AVAudioPlayer(contentsOf: voice.audioURL))?.duration ?? 0) > 1, "AVAudioPlayer opens stored WAV")
        if ProcessInfo.processInfo.operatingSystemVersion.majorVersion >= 26 {
            check(((try? AVAudioPlayer(contentsOf: ogg))?.duration ?? 0) > 1, "AVAudioPlayer opens Ogg Opus")
        }
        check(unreadableAudioMessage(ogg) == t("error.oggUnsupported"), "Ogg message")

        // A clip with no speech is not stored.
        let silent = work.appendingPathComponent("silent.wav")
        try writeWav([Float](repeating: 0, count: 48_000), rate: 24_000, to: silent)
        do {
            try storeVoiceAudio(silent, to: work.appendingPathComponent("silent-out.wav"))
            check(false, "silence refused")
        } catch is AudioFileError {}
        finish("ConvertTests")
    }
}
