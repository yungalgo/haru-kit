// Records from a named input device with the app's VoiceRecorder (not the
// System default) and checks the WAV: 48 kHz, mono, 16-bit, the right length.
// On a loopback device (BlackHole) it plays a tone into the device's output
// (not the speakers) and checks that the clip has it.
//   ./Tests/record.sh ["BlackHole 2ch"]
// Needs microphone access for the terminal that runs it (macOS asks once);
// Without it, Core Audio delivers silence and only the tone check fails.

import AudioToolbox
import Foundation

@main
struct RecordTests {
    static func main() throws {
        let name = CommandLine.arguments.dropFirst().first ?? "BlackHole 2ch"
        guard let device = findDevice(name, input: true) else {
            print("RecordTests: skipped (no input device \"\(name)\")")
            return
        }
        print("RecordTests: system default input: \(findDevice(nil, input: true)?.name ?? "none")")
        // A loopback device (BlackHole) also has an output: a tone played into it
        // Shows that the clip comes from this device.
        let tone = device.outputs > 0 ? try Tone(device) : nil
        let level = try record(device, seconds: 2, maxSeconds: 180)
        tone?.stop()
        if tone != nil {
            check(level > 0.05, "the tone played into \(device.name) is in the clip (RMS \(level); 0 without microphone access)")
        }
        // The limit: it stops taking audio at maxSeconds.
        _ = try record(device, seconds: 1, maxSeconds: 0.5)
        finish("RecordTests")
    }

    /// Records, checks the WAV and returns its RMS level.
    static func record(_ device: Device, seconds: Double, maxSeconds: Double) throws -> Float {
        let recorder = try VoiceRecorder(device: device, maxSeconds: maxSeconds)
        try recorder.start()
        Thread.sleep(forTimeInterval: seconds)
        let got = recorder.seconds
        let url = FileManager.default.temporaryDirectory.appendingPathComponent("record-\(UUID().uuidString).wav")
        defer { try? FileManager.default.removeItem(at: url) }
        try recorder.finish(to: url)
        let data = try Data(contentsOf: url)
        func u16(_ at: Int) -> Int { Int(data[at]) | Int(data[at + 1]) << 8 }
        func u32(_ at: Int) -> Int { u16(at) | u16(at + 2) << 16 }
        check(String(decoding: data[0..<4], as: UTF8.self) == "RIFF" && String(decoding: data[8..<16], as: UTF8.self) == "WAVEfmt ",
              "a WAV file")
        check(u16(20) == 1 && u16(22) == 1, "PCM, mono")
        check(u32(24) == 48_000 && u16(34) == 16, "48 kHz, 16-bit")
        let length = Double(u32(40) / 2) / 48_000
        let expected = min(seconds, maxSeconds)
        check(abs(length - expected) < 0.15, "\(device.name): \(String(format: "%.3f", length)) s recorded, \(expected) s expected")
        check(abs(got - expected) < 0.15, "seconds while recording: \(got)")
        check(data.count == 44 + u32(40), "data size matches the file")
        let samples = (0..<(u32(40) / 2)).map { Float(Int16(bitPattern: UInt16(u16(44 + 2 * $0)))) / 32768 }
        let rms = (samples.reduce(0) { $0 + $1 * $1 } / Float(max(1, samples.count))).squareRoot()
        print(String(format: "RecordTests: %@ at %.0f Hz, %d channel(s): %.3f s, RMS %.3f",
                     device.name, device.rate, device.inputs, length, rms))
        return rms
    }
}

/// A 440 Hz tone (-10 dBFS) into an output device, with a HAL output unit.
final class Tone {
    private let unit: AudioUnit
    private var phase = 0.0
    private let step: Double

    init(_ device: Device) throws {
        step = 2 * .pi * 440 / 48_000
        unit = try DuplexAudio.makeUnit(device: device.id, input: false)
        var format = DuplexAudio.floatFormat(rate: 48_000, channels: 1)
        try ok(AudioUnitSetProperty(unit, kAudioUnitProperty_StreamFormat, kAudioUnitScope_Input, 0, &format,
                                    UInt32(MemoryLayout<AudioStreamBasicDescription>.size)), "tone format")
        var callback = AURenderCallbackStruct(inputProc: { refCon, _, _, _, frames, data in
            let tone = Unmanaged<Tone>.fromOpaque(refCon).takeUnretainedValue()
            let list = UnsafeMutableAudioBufferListPointer(data!)
            let out = list[0].mData!.assumingMemoryBound(to: Float.self)
            for i in 0..<Int(frames) {
                out[i] = Float(0.3 * sin(tone.phase))
                tone.phase += tone.step
            }
            return noErr
        }, inputProcRefCon: Unmanaged.passUnretained(self).toOpaque())
        try ok(AudioUnitSetProperty(unit, kAudioUnitProperty_SetRenderCallback, kAudioUnitScope_Input, 0, &callback,
                                    UInt32(MemoryLayout<AURenderCallbackStruct>.size)), "tone callback")
        try ok(AudioUnitInitialize(unit), "tone initialize")
        try ok(AudioOutputUnitStart(unit), "tone start")
    }

    func stop() {
        AudioOutputUnitStop(unit)
        AudioComponentInstanceDispose(unit)
    }
}
