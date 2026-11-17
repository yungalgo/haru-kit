// Records a voice clip from one input device: the microphone chosen in
// Settings, opened by its AudioDeviceID with a HAL unit as a conversation opens
// It (not the system default). The result is a 48 kHz mono 16-bit WAV.

import AudioToolbox
import CoreAudio
import Foundation

final class VoiceRecorder {
    static let rate = 48_000.0
    let device: Device
    private var unit: AudioUnit?
    private let buffers: UnsafeMutableAudioBufferListPointer
    private let capacity = 8192  // Frames per callback at most
    /// The first channel at the device rate, up to `maxFrames`.
    private let samples: UnsafeMutablePointer<Float>
    private let maxFrames: Int
    private var lock = os_unfair_lock()
    private var count = 0

    /// Opens `device`; it records at most `maxSeconds`.
    init(device: Device, maxSeconds: Double) throws {
        guard device.inputs > 0, device.rate > 0 else { throw AudioError(description: "\(device.name): no input") }
        self.device = device
        maxFrames = Int(device.rate * maxSeconds)
        samples = .allocate(capacity: maxFrames)
        buffers = AudioBufferList.allocate(maximumBuffers: device.inputs)
        for i in 0..<device.inputs {
            buffers[i] = AudioBuffer(mNumberChannels: 1, mDataByteSize: UInt32(capacity * 4),
                                     mData: UnsafeMutableRawPointer.allocate(byteCount: capacity * 4, alignment: 16))
        }
    }

    deinit {
        close()
        samples.deallocate()
        for b in buffers { b.mData?.deallocate() }
        free(buffers.unsafeMutablePointer)
    }

    func start() throws {
        let u = try DuplexAudio.makeUnit(device: device.id, input: true)
        unit = u
        do {
            var format = DuplexAudio.floatFormat(rate: device.rate, channels: device.inputs)
            try ok(AudioUnitSetProperty(u, kAudioUnitProperty_StreamFormat, kAudioUnitScope_Output, 1, &format,
                                        UInt32(MemoryLayout<AudioStreamBasicDescription>.size)), "input format")
            var callback = AURenderCallbackStruct(inputProc: { refCon, flags, ts, bus, frames, _ in
                Unmanaged<VoiceRecorder>.fromOpaque(refCon).takeUnretainedValue().handleInput(flags, ts, bus, frames)
            }, inputProcRefCon: Unmanaged.passUnretained(self).toOpaque())
            try ok(AudioUnitSetProperty(u, kAudioOutputUnitProperty_SetInputCallback, kAudioUnitScope_Global, 0,
                                        &callback, UInt32(MemoryLayout<AURenderCallbackStruct>.size)), "input callback")
            try ok(AudioUnitInitialize(u), "initialize input")
            try ok(AudioOutputUnitStart(u), "start input")
        } catch {
            close()
            throw AudioError(description: "\(device.name): \(error)")
        }
    }

    /// The seconds recorded so far.
    var seconds: Double {
        os_unfair_lock_lock(&lock)
        defer { os_unfair_lock_unlock(&lock) }
        return Double(count) / device.rate
    }

    /// Stops and writes the clip as a 48 kHz mono 16-bit WAV.
    func finish(to url: URL) throws {
        close()
        os_unfair_lock_lock(&lock)
        let n = count
        os_unfair_lock_unlock(&lock)
        let audio = try resample(UnsafeBufferPointer(start: samples, count: n), from: device.rate, to: Self.rate)
        try writeWav(audio, rate: Int(Self.rate), to: url)
    }

    /// Stops the device; nothing is written.
    func close() {
        guard let u = unit else { return }
        AudioOutputUnitStop(u)
        AudioComponentInstanceDispose(u)
        unit = nil
    }

    private func handleInput(_ flags: UnsafeMutablePointer<AudioUnitRenderActionFlags>,
                             _ ts: UnsafePointer<AudioTimeStamp>, _ bus: UInt32, _ frames: UInt32) -> OSStatus {
        guard Int(frames) <= capacity, let u = unit else { return noErr }
        for i in 0..<buffers.count { buffers[i].mDataByteSize = frames * 4 }
        let status = AudioUnitRender(u, flags, ts, bus, frames, buffers.unsafeMutablePointer)
        if status != noErr { return status }
        os_unfair_lock_lock(&lock)
        let n = min(Int(frames), maxFrames - count)
        if n > 0 {
            (samples + count).update(from: buffers[0].mData!.assumingMemoryBound(to: Float.self), count: n)
            count += n
        }
        os_unfair_lock_unlock(&lock)
        return noErr
    }
}
