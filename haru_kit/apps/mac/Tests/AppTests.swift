// Built with all of App/ except HaruApp.swift (see run.sh).

import Foundation

@main
struct AppTests {
    static func main() throws {
        try savedAudio()
        downloadLines()
        voiceLimits()
        try recorderResample()
        finish("AppTests")
    }

    /// A voice clip has at least 3 s of speech; a recording stops by itself at 180 s (upstream keeps the last 180 s).
    static func voiceLimits() {
        check(AppModel.voiceMinSeconds == 3, "minimum 3 s")
        check(AppModel.voiceMaxSeconds == 180, "maximum 180 s")
    }

    /// A microphone at 44.1 kHz gives a 48 kHz clip of the same length.
    static func recorderResample() throws {
        let x = (0..<44_100).map { Float(0.5 * sin(2 * Double.pi * 440 * Double($0) / 44_100)) }
        let y = try x.withUnsafeBufferPointer { try resample($0, from: 44_100, to: VoiceRecorder.rate) }
        check(abs(y.count - 48_000) <= 2, "1 s at 44.1 kHz is 48000 samples at 48 kHz: \(y.count)")
        check(abs((y.map { $0 * $0 }.reduce(0, +) / Float(y.count)).squareRoot() - 0.354) < 0.01, "same level")
    }

    /// The saved WAV of a conversation: two channels, you on the left and the model on the right.
    static func savedAudio() throws {
        let user: [Int16] = [100, 200, 300, 400]
        let model: [Int16] = [-1, -2, -3]
        let url = FileManager.default.temporaryDirectory.appendingPathComponent("recording-\(UUID().uuidString).wav")
        defer { try? FileManager.default.removeItem(at: url) }
        try Recording(user: user, model: model, texts: []).writeAudio(to: url)
        let data = try Data(contentsOf: url)
        func u16(_ at: Int) -> Int { Int(data[at]) | Int(data[at + 1]) << 8 }
        func u32(_ at: Int) -> Int { u16(at) | u16(at + 2) << 16 }
        check(String(decoding: data[0..<4], as: UTF8.self) == "RIFF" && String(decoding: data[8..<16], as: UTF8.self) == "WAVEfmt ",
              "a WAV file")
        check(u16(20) == 1 && u16(22) == 2, "PCM, two channels")
        check(u32(24) == 24_000 && u16(34) == 16, "24 kHz, 16-bit")
        let samples = (0..<(u32(40) / 2)).map { Int16(bitPattern: UInt16(u16(44 + 2 * $0))) }
        check(stride(from: 0, to: samples.count, by: 2).map { samples[$0] } == user, "left channel: you")
        check(stride(from: 1, to: samples.count, by: 2).map { samples[$0] } == model + [0], "right channel: the model")
    }

    /// The model download's progress, read from the server's log lines.
    static func downloadLines() {
        check(downloadProgress("model.safetensors:  37%|███▋      | 3.90G/10.5G [01:10<02:00, 55.0MB/s]") == 37, "file bar")
        check(downloadProgress("model.safetensors: 100%|██████████| 10.5G/10.5G [03:10<00:00, 55.0MB/s]") == 100, "done")
        check(downloadProgress("Fetching 5 files:  20%|██        | 1/5 [00:01<00:04,  1.00it/s]") == nil, "file count bar ignored")
        check(downloadProgress("10:29:08 haru_mlx.native: loading higurashi-ai/Haru-4B") == nil, "other lines")
    }
}
