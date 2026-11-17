// The voice library on the setup screen: listen to a voice, choose it for the
// Draft's version, and adding and deleting voices.

import AppKit
import AVFoundation

/// Plays one audio file at a time and says when it ends.
final class Playback: NSObject, AVAudioPlayerDelegate {
    private var player: AVAudioPlayer?
    private var onEnd: (() -> Void)?

    func play(_ url: URL, onEnd: @escaping () -> Void) -> Bool {
        stop()
        guard let player = try? AVAudioPlayer(contentsOf: url) else { return false }
        player.delegate = self
        self.player = player
        self.onEnd = onEnd
        return player.play()
    }

    func stop() {
        player?.stop()
        player = nil
        let end = onEnd
        onEnd = nil
        end?()
    }

    func audioPlayerDidFinishPlaying(_ player: AVAudioPlayer, successfully flag: Bool) {
        guard player === self.player else { return }
        stop()
    }
}

extension AppModel {
    /// The voices, filtered to the current language when the switch is on.
    var shownVoices: [LibraryVoice] {
        voicesOnlyLanguage ? libraryVoices.filter { $0.language == language } : libraryVoices
    }

    func genderLabel(_ gender: String) -> String {
        switch gender {
        case "female": return t("option.female")
        case "male": return t("option.male")
        default: return t("option.unspecified")
        }
    }

    /// Plays the voice, or stops it when it is playing.
    func togglePlay(_ voice: LibraryVoice) {
        if playingID == voice.id {
            playback.stop()
            return
        }
        if playback.play(voice.audioURL, onEnd: { [weak self] in self?.playingID = nil }) {
            playingID = voice.id
        } else {
            errorMessage = unreadableAudioMessage(voice.audioURL)
        }
    }

    /// The voice for the draft's version (saved with it).
    func useVoice(_ id: String) { draft.voice = id }
}

/// A voice being added: a recording or an uploaded file, before it is saved.
struct NewVoice {
    /// The spoken words (the read-aloud text of a recording; empty for a file).
    var text: String
    var name: String
    var language: String
    var gender = ""
    /// The whole audio; the speech left after the silence trim; and what the
    /// Model gets: at most the last `voiceMaxSeconds` of the speech.
    var seconds: Double
    var speechSeconds: Double
    var trimmedSeconds: Double
    /// The trimmed audio as a 24 kHz mono 16-bit WAV: the preview, and the file that is saved.
    var preview: URL
}

extension AppModel {
    /// The voice reference: at least 3 s of speech; the model uses at most the
    /// Last 180 s (upstream `prepare_reference`), and a recording stops by itself there.
    static let voiceMinSeconds = 3.0
    static let voiceMaxSeconds = 180.0
    static let previewID = "new-voice"

    /// Records the read-aloud text with the microphone of Settings (the hand
    /// Choice, or the automatic one, as for a conversation); it stops by itself
    /// At 180 s. Never during a conversation.
    func startVoiceRecording() {
        guard !isRecording, phase != .starting, phase != .talking else { return }
        guard !microphoneDenied else {
            errorMessage = t("error.microphoneDenied") + " " + t("error.microphoneFixMac")
            return
        }
        playback.stop()
        refreshDevices()
        guard !inputDevice.isEmpty, let device = findDevice(inputDevice, input: true) else {
            errorMessage = t("error.recording", ["error": t("error.noDevice")])
            return
        }
        do {
            let recorder = try VoiceRecorder(device: device, maxSeconds: Self.voiceMaxSeconds)
            try recorder.start()
            voiceRecorder = recorder
            newVoice = nil
            isRecording = true
            recordingSeconds = 0
            fileLog("[app] voice recording: input \(device.name)")
            Timer.scheduledTimer(withTimeInterval: 0.1, repeats: true) { [weak self] timer in
                guard let self, self.isRecording, let recorder = self.voiceRecorder else { timer.invalidate(); return }
                self.recordingSeconds = recorder.seconds
                if recorder.seconds >= Self.voiceMaxSeconds { self.stopVoiceRecording() }
            }
        } catch {
            errorMessage = t("error.recording", ["error": "\(error)"])
        }
    }

    func stopVoiceRecording() {
        guard isRecording, let recorder = voiceRecorder else { return }
        voiceRecorder = nil
        isRecording = false
        let url = FileManager.default.temporaryDirectory.appendingPathComponent("haru-voice-\(UUID().uuidString).wav")
        do {
            try recorder.finish(to: url)
        } catch {
            errorMessage = t("error.recording", ["error": "\(error)"])
            return
        }
        prepareNewVoice(url, text: t("voices.readText"))
    }

    func uploadVoice() {
        let panel = NSOpenPanel()
        panel.allowedContentTypes = voiceAudioTypes
        panel.allowsMultipleSelection = false
        if panel.runModal() == .OK, let url = panel.url { prepareNewVoice(url, text: "") }
    }

    /// Reads the audio and trims it as the server will: the silence removed, then
    /// At most the last `voiceMaxSeconds`. This is the preview and the stored voice.
    func prepareNewVoice(_ url: URL, text: String) {
        do {
            let samples = try loadMono(url)
            let speech = trimSilence(samples, maxSeconds: Double(samples.count) / 24_000)
            let trimmed = Array(speech.suffix(Int(Self.voiceMaxSeconds * 24_000)))
            let preview = FileManager.default.temporaryDirectory
                .appendingPathComponent("haru-preview-\(UUID().uuidString).wav")
            try writeWav(trimmed, rate: 24_000, to: preview)
            newVoice = NewVoice(text: text, name: t("voices.unnamed"), language: language,
                                seconds: Double(samples.count) / 24_000, speechSeconds: Double(speech.count) / 24_000,
                                trimmedSeconds: Double(trimmed.count) / 24_000, preview: preview)
        } catch {
            errorMessage = unreadableAudioMessage(url)
        }
    }

    /// Plays the trimmed audio of the new voice, or stops it.
    func togglePreview() {
        guard let newVoice else { return }
        if playingID == Self.previewID {
            playback.stop()
        } else if playback.play(newVoice.preview, onEnd: { [weak self] in self?.playingID = nil }) {
            playingID = Self.previewID
        }
    }

    /// The new voice is too short after the trim.
    var newVoiceTooShort: String? {
        guard let newVoice, newVoice.trimmedSeconds < Self.voiceMinSeconds else { return nil }
        return t("error.audio_too_short", ["seconds": String(format: "%.1f", newVoice.trimmedSeconds),
                                           "min_seconds": Int(Self.voiceMinSeconds)])
    }

    /// Saves the new voice to the user's library and uses it for the draft. Every
    /// Type is stored in one format: the trimmed audio as a 24 kHz mono 16-bit WAV.
    func saveNewVoice() {
        guard let newVoice else { return }
        if let tooShort = newVoiceTooShort {
            errorMessage = tooShort
            return
        }
        let id = library.newID()
        let name = newVoice.name.trimmingCharacters(in: .whitespaces)
        var voice = LibraryVoice(id: id, name: name.isEmpty ? t("voices.unnamed") : name, language: newVoice.language,
                                 gender: newVoice.gender, seconds: (newVoice.trimmedSeconds * 10).rounded() / 10,
                                 text: newVoice.text, file: "\(id).wav")
        voice.updated = todayISO()
        do {
            try library.save(voice, audio: newVoice.preview)
        } catch {
            errorMessage = t("error.library", ["error": error.localizedDescription])
            return
        }
        cancelNewVoice()
        reloadLibrary()
        if !missingVersion { useVoice(id) }
    }

    func cancelNewVoice() {
        if playingID == Self.previewID { playback.stop() }
        newVoice = nil
    }

    func deleteVoice(_ voice: LibraryVoice) {
        guard ask(t("voices.deleteConfirm", ["name": voice.name]), confirm: t("voices.delete"),
                  cancel: t("voices.cancel"), destructive: true) else { return }
        if playingID == voice.id { playback.stop() }
        do { try library.deleteVoice(voice.id) } catch {
            errorMessage = t("error.library", ["error": error.localizedDescription])
        }
        reloadLibrary()
    }
}
