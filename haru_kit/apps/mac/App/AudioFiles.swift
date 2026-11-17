// Reading audio files as mono float (for the voice preview), the stored form of
// An own voice, and writing 16-bit WAV.

import AVFoundation
import UniformTypeIdentifiers

/// An audio file that cannot be read (`file`: the message tells about Ogg on an old macOS).
struct AudioFileError: LocalizedError {
    var file: URL?

    var errorDescription: String? { file.map(unreadableAudioMessage) ?? t("error.unreadable_audio") }
}

/// The audio types that a voice can come from: WAV, FLAC, MP3, M4A/AAC, Ogg (Vorbis/Opus), Opus, AIFF, CAF.
let voiceAudioTypes: [UTType] = [.audio] + ["wav", "flac", "mp3", "m4a", "aac", "ogg", "opus", "aiff", "aif", "caf"]
    .compactMap { UTType(filenameExtension: $0) }

/// The message when a voice clip cannot be read or played: Ogg Opus needs a
/// Recent macOS or the haru-kit Python environment.
func unreadableAudioMessage(_ url: URL) -> String {
    ["ogg", "opus"].contains(url.pathExtension.lowercased()) ? t("error.oggUnsupported") : t("error.unreadable_audio")
}

/// Writes the stored form of an own voice: the audio as mono at 24 kHz, the
/// Silence trim, at most the last 180 s (upstream `prepare_reference`), as a
/// 16-bit PCM WAV. Returns its length in seconds. No speech: AudioFileError.
@discardableResult
func storeVoiceAudio(_ source: URL, to target: URL) throws -> Double {
    let samples: [Float]
    do { samples = try loadMono(source) } catch { throw AudioFileError(file: source) }
    let reference = trimSilence(samples)
    guard !reference.isEmpty else { throw AudioFileError(file: source) }
    try writeWav(reference, rate: 24_000, to: target)
    return Double(reference.count) / 24_000
}

private let maxAudioSeconds = 600.0  // Cap single-file decode memory at 10 min before the 3 min trim

private func haruPythonPath() -> String? {
    if let env = ProcessInfo.processInfo.environment["HARU_KIT_PYTHON"], !env.isEmpty { return env }
    if let plist = Bundle.main.object(forInfoDictionaryKey: "HaruPython") as? String, !plist.isEmpty { return plist }
    let fallback = FileManager.default.homeDirectoryForCurrentUser
        .appendingPathComponent("Library/Application Support/haru-kit/envs/mac/bin/python").path
    return FileManager.default.isExecutableFile(atPath: fallback) ? fallback : nil
}

/// Fallback for macOS 13–15 where AVAudioFile does not read Ogg Opus: decode via `soundfile` (`libsndfile`)
/// In haru-kit's Python environment into a temporary WAV, then read and resample with AVAudioFile.
private func decodeViaSoundfile(_ url: URL, to tempWav: URL) -> Bool {
    guard let python = haruPythonPath(), FileManager.default.isExecutableFile(atPath: python) else { return false }
    let script = """
    import sys, soundfile as sf
    data, sr = sf.read(sys.argv[1], dtype='float32', always_2d=True)
    if len(data) == 0 or len(data) > int(\(maxAudioSeconds) * sr):
        sys.exit(1)
    sf.write(sys.argv[2], data.mean(axis=1), sr, subtype='FLOAT')
    """
    let proc = Process()
    proc.executableURL = URL(fileURLWithPath: python)
    proc.arguments = ["-c", script, url.path, tempWav.path]
    proc.standardOutput = FileHandle.nullDevice
    proc.standardError = FileHandle.nullDevice
    guard (try? proc.run()) != nil else { return false }
    proc.waitUntilExit()
    return proc.terminationStatus == 0
}

/// An audio file as mono float at `rate` Hz: the channels averaged, then
/// Resampled, as upstream `decode_audio` does.
func loadMono(_ url: URL, rate: Double = 24_000) throws -> [Float] {
    let file: AVAudioFile
    var tempWav: URL?
    do {
        file = try AVAudioFile(forReading: url)
    } catch {
        let tmp = FileManager.default.temporaryDirectory.appendingPathComponent("haru-sf-\(UUID().uuidString).wav")
        guard decodeViaSoundfile(url, to: tmp), let decoded = try? AVAudioFile(forReading: tmp) else {
            try? FileManager.default.removeItem(at: tmp)
            throw error
        }
        tempWav = tmp
        file = decoded
    }
    defer { if let tempWav { try? FileManager.default.removeItem(at: tempWav) } }
    let format = file.processingFormat
    let maxFrames = AVAudioFramePosition(maxAudioSeconds * format.sampleRate)
    guard file.length > 0, file.length <= maxFrames,
          let buffer = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: AVAudioFrameCount(file.length)) else {
        throw AudioFileError()
    }
    try file.read(into: buffer)
    guard let data = buffer.floatChannelData else { throw AudioFileError() }
    let channels = Int(format.channelCount), count = Int(buffer.frameLength)
    var mono = [Float](repeating: 0, count: count)
    for c in 0..<channels {
        for i in 0..<count { mono[i] += data[c][i] / Float(channels) }
    }
    if format.sampleRate == rate { return mono }
    return try mono.withUnsafeBufferPointer { try resample($0, from: format.sampleRate, to: rate) }
}

/// Mono float audio from one rate to another (AVAudioConverter, best quality).
func resample(_ x: UnsafeBufferPointer<Float>, from sourceRate: Double, to rate: Double) throws -> [Float] {
    if sourceRate == rate || x.isEmpty { return Array(x) }
    let count = x.count
    guard let source = AVAudioFormat(commonFormat: .pcmFormatFloat32, sampleRate: sourceRate, channels: 1, interleaved: false),
          let target = AVAudioFormat(commonFormat: .pcmFormatFloat32, sampleRate: rate, channels: 1, interleaved: false),
          let converter = AVAudioConverter(from: source, to: target),
          let input = AVAudioPCMBuffer(pcmFormat: source, frameCapacity: AVAudioFrameCount(count)),
          let output = AVAudioPCMBuffer(pcmFormat: target, frameCapacity: AVAudioFrameCount(Double(count) * rate / sourceRate) + 4096)
    else { throw AudioFileError() }
    converter.sampleRateConverterQuality = AVAudioQuality.max.rawValue
    input.frameLength = AVAudioFrameCount(count)
    input.floatChannelData![0].update(from: x.baseAddress!, count: count)
    var fed = false
    var error: NSError?
    converter.convert(to: output, error: &error) { _, status in
        if fed {
            status.pointee = .endOfStream
            return nil
        }
        fed = true
        status.pointee = .haveData
        return input
    }
    if let error { throw error }
    return Array(UnsafeBufferPointer(start: output.floatChannelData![0], count: Int(output.frameLength)))
}

/// Mono 16-bit PCM WAV. A sample is rounded on the scale that AVAudioFile reads
/// (1.0 = 32768), so a stored WAV that is read and written again keeps its bytes.
func writeWav(_ samples: [Float], rate: Int, to url: URL) throws {
    var data = Data()
    func u32(_ v: UInt32) { withUnsafeBytes(of: v.littleEndian) { data.append(contentsOf: $0) } }
    func u16(_ v: UInt16) { withUnsafeBytes(of: v.littleEndian) { data.append(contentsOf: $0) } }
    let bytes = UInt32(samples.count * 2)
    data.append(contentsOf: Array("RIFF".utf8)); u32(36 + bytes)
    data.append(contentsOf: Array("WAVEfmt ".utf8)); u32(16); u16(1); u16(1)
    u32(UInt32(rate)); u32(UInt32(rate * 2)); u16(2); u16(16)
    data.append(contentsOf: Array("data".utf8)); u32(bytes)
    data.reserveCapacity(data.count + samples.count * 2)
    for v in samples { u16(UInt16(bitPattern: Int16(clamping: Int((v * 32768).rounded())))) }
    try data.write(to: url)
}
