// Swiftc -parse-as-library App/Trim.swift Tests/Check.swift Tests/TrimTests.swift
// Expected values follow upstream trim_silence / prepare_reference (haru_mlx/audio.py).

import Foundation

@main
struct TrimTests {
    static func sine(_ seconds: Double, amplitude: Float, rate: Int = 24_000) -> [Float] {
        (0..<Int(seconds * Double(rate))).map { amplitude * sin(2 * .pi * 220 * Float($0) / Float(rate)) }
    }

    static func silence(_ seconds: Double, rate: Int = 24_000) -> [Float] {
        [Float](repeating: 0, count: Int(seconds * Double(rate)))
    }

    static func main() {
        // 1 s silence, 2 s speech, 1 s silence: windows 50..<150 voiced, 0.2 s (4800) kept on each side.
        let audio = silence(1) + sine(2, amplitude: 0.1) + silence(1)
        let trimmed = trimSilence(audio)
        check(trimmed.count == 150 * 480 + 4800 - (50 * 480 - 4800), "speech with 0.2 s margins: \(trimmed.count)")
        check(trimmed.first == audio[50 * 480 - 4800], "starts 0.2 s before the first voiced window")

        // Speech from the first sample: the start is clamped at 0.
        let early = sine(1, amplitude: 0.1) + silence(1)
        check(trimSilence(early).count == 50 * 480 + 4800, "start clamped at 0")

        // Speech to the end, with a partial window: the end is clamped to the audio.
        let late = silence(1) + sine(1, amplitude: 0.1) + [Float](repeating: 0.05, count: 100)
        check(trimSilence(late).count == late.count - (50 * 480 - 4800), "end clamped at the audio length")

        // Quieter than -45 dBFS everywhere: nothing left.
        check(trimSilence([Float](repeating: 0.003, count: 48_000)).isEmpty, "-50 dBFS is silence")
        check(trimSilence(silence(2)).isEmpty, "digital silence")
        // Just above -45 dBFS is speech.
        check(trimSilence([Float](repeating: 0.006, count: 48_000)).count == 48_000, "-44 dBFS is speech")

        // Shorter than one window: unchanged (upstream returns it as is).
        check(trimSilence([0, 0, 0]).count == 3, "shorter than a window")

        // At most the last 180 s.
        let long = sine(200, amplitude: 0.1)
        let kept = trimSilence(long)
        check(kept.count == 180 * 24_000, "at most 180 s")
        check(kept.last == long.last, "the last 180 s")
        finish("TrimTests")
    }
}
