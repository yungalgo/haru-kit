// Modified from higurashi-ai/Haru (Apache-2.0) for haru-kit.
// State of the app: server, character card, voice reference, settings and the
// Running conversation.

import AppKit
import AVFoundation
import CoreAudio
import SwiftUI

/// The latest user and model levels (0...1 on a -60..0 dBFS scale), read by
/// The meters at screen rate; not observable, so reading does not re-render
/// The views.
final class Levels {
    var source: () -> (Float, Float) = { (0, 0) }

    static func normalized(_ rms: Float) -> Float {
        let db = 20 * log10(max(rms, 1e-6))
        return max(0, min(1, (db + 60) / 60))
    }

    func current() -> (user: Float, model: Float) {
        let (u, m) = source()
        return (Self.normalized(u), Self.normalized(m))
    }
}

final class AppModel: ObservableObject {
    enum Phase { case launching, ready, starting, talking }

    // Server
    @Published var phase: Phase = .launching
    @Published var status = t("launch.connecting")
    @Published var serverLog: [String] = []
    @Published var errorMessage: String?
    /// The server could not start or be reached: the launch screen offers to try again.
    @Published var serverFailed = false
    /// The user refused the microphone: no conversation and no recording until it is allowed.
    @Published var microphoneDenied = false
    /// The model download's progress from the server log, while it downloads.
    @Published var downloadPercent: Int?
    @Published var precision = ""
    /// The model the server loaded (a repo id or a snapshot path), to reload it in another precision.
    private var modelPath = ""
    /// Restarting the server in another precision (its log keeps the reload status).
    private var reloading = false
    @Published var stepMs = 0.0

    // Character: the library, the selected character, and the editor's draft
    // Of its version in the current language (AppModel+Library.swift).
    @Published var languages: [(code: String, label: String)] = []
    /// The one language (the globe menu): the app's text and the language that the model speaks,
    /// With the card, the prompt headings, the default voice and the library filter.
    @Published var language = UILanguage.current {
        didSet {
            UILanguage.current = language
            schedulePrepare()
        }
    }
    let library = LibraryStore()
    @Published var characters: [LibraryCharacter] = []
    @Published var libraryVoices: [LibraryVoice] = []
    @Published var selectedID = "" { didSet { Stored.set("character", selectedID) } }
    @Published var draft = Draft() {
        didSet {
            schedulePrepare()
            Stored.saveDraft(draft, character: selectedID, language: language, writing: writingVersion)
        }
    }
    /// The saved version that the draft started from (nil: a version not saved yet).
    @Published var baseline: Draft?
    /// Only characters with a version in the current language.
    @Published var onlyLanguage = false
    /// Writing a new language version of the selected character (not saved yet).
    @Published var writingVersion = false
    /// The card templates of the server, by language.
    @Published private(set) var templates: [String: [String: Any]] = [:]
    /// The longest system prompt the web server takes; the same limit here.
    static let promptMaxChars = 4000

    var card: CardFields { draft.card }

    /// The card as the model reads it (upstream `render_card`).
    var renderedCard: String {
        templates[language].map { renderCard(card.strings, $0) } ?? ""
    }

    // Voice (AppModel+Voices.swift)
    @Published var isRecording = false { didSet { schedulePrepare() } }
    /// Only voices in the current language.
    @Published var voicesOnlyLanguage = false
    /// The voice that is playing in the list.
    @Published var playingID: String?
    let playback = Playback()
    @Published var recordingSeconds = 0.0
    /// Records a new voice from the microphone of Settings (VoiceRecorder.swift).
    var voiceRecorder: VoiceRecorder?
    /// A voice being added, before it is saved.
    @Published var newVoice: NewVoice?

    // Shared characters (AppModel+Registry.swift)
    /// The registry of this launch (nil: no Browse button).
    let registry = launchRegistry()
    /// The Browse sheet is open.
    @Published var browsing = false
    @Published var registryList: RegistryList = .loading
    /// The entry being downloaded and added.
    @Published var registryAdding: String?
    /// Why the last Add or Update failed (shown in the sheet).
    @Published var registryError: String?
    /// The first start adds the registry's characters to the library (AppModel+Registry.swift).
    @Published var seeding = false

    // Settings
    @Published var cfgScale = Stored.double("cfgScale", 1.5) {
        didSet {
            schedulePrepare()
            Stored.set("cfgScale", cfgScale)
        }
    }
    @Published var marginMs = Stored.double("marginMs", 15) { didSet { Stored.set("marginMs", marginMs) } }
    @Published var inputDevices: [String] = []
    @Published var outputDevices: [String] = []
    /// The devices chosen by hand; empty: automatic.
    @Published private(set) var inputChoice = Stored.string("inputDevice")
    @Published private(set) var outputChoice = Stored.string("outputDevice")
    /// The devices in effect: the hand choice, or the automatic one.
    @Published private(set) var inputDevice = ""
    @Published private(set) var outputDevice = ""
    private var deviceRefresh: DispatchWorkItem?

    // Prefill: the server prefills the current card and voice ahead of START.
    enum Prepare: Equatable { case idle, preparing, ready, failed(String) }
    @Published var prepare: Prepare = .idle
    private var prepareWork: DispatchWorkItem?
    private var preparedKey: String?  // The request of the last PREPARE
    private var prepareSeq = 0  // PREPAREs sent on this connection

    // Conversation
    /// The server's limit for one conversation (upstream max_frames: 60 minutes).
    static let timeLimitMinutes = 60
    @Published var transcript = ""
    /// The microphone is muted: the server gets silence, so the model keeps its clock.
    @Published var muted = false { didSet { audio?.muted = muted } }
    /// When the audio of the running conversation started.
    @Published var talkStarted: Date?
    @Published var latencyMs: Double?
    @Published var underruns = 0
    @Published var caughtUpMs = 0.0
    @Published var lastReport: LatencyReport?
    @Published var audioInfo = ""
    /// The last conversation as heard, for saving its audio and video, and the model's text.
    @Published var lastRecording: Recording?
    @Published var lastText = ""
    /// The last conversation is not saved yet (no audio, video or text), and the
    /// User has not said "Don't save".
    @Published var lastUnsaved = false
    @Published var exportProgress: Double?

    private let launcher = ServerLauncher()
    private var conn: Connection?
    private var refusals = 0  // Connections the server closed before sending its assets
    private var audio: DuplexAudio?
    private var capture: ConversationRecorder?
    /// Levels for the meters, read by the views at screen rate.
    let levels = Levels()
    /// Both voices and the model's text on the host clock, for the waveform lanes.
    let waves = WaveHistory()
    private var timer: Timer?
    /// Devices and margin for the conversation START asked for.
    private var plan: (input: Device, output: Device, margin: Double)?
    let socket = defaultSocket

    init() {
        NotificationCenter.default.addObserver(
            forName: NSApplication.willTerminateNotification, object: nil, queue: .main
        ) { [weak self] _ in
            self?.audio?.stop()
            self?.conn?.close()
            self?.launcher.stop()
        }
        // The permission can change in System Settings while the app runs.
        NotificationCenter.default.addObserver(
            forName: NSApplication.didBecomeActiveNotification, object: nil, queue: .main
        ) { [weak self] _ in self?.checkMicrophone() }
        refreshDevices()
        watchDevices()
        reloadLibrary()
        restoreCharacter()
    }

    // MARK: Server

    func launch() {
        checkMicrophone()
        let info = Bundle.main.infoDictionary ?? [:]
        let python = info["HaruPython"] as? String ?? "/usr/bin/python3"
        launcher.connect(
            socket: socket, python: python,
            arguments: ["--language", language, "--quantize", Stored.string("precision") == "bf16" ? "none" : "8"],
            onLog: logLine, onConnected: { [weak self] conn in self?.receiveAssets(conn) },
            onFailure: launchFailed)
    }

    /// Asks for the microphone the first time; notes when the user refused it.
    func checkMicrophone() {
        switch AVCaptureDevice.authorizationStatus(for: .audio) {
        case .notDetermined:
            AVCaptureDevice.requestAccess(for: .audio) { granted in
                DispatchQueue.main.async { self.microphoneDenied = !granted }
            }
        case .denied, .restricted:
            microphoneDenied = true
        default:
            microphoneDenied = false
        }
    }

    /// System Settings → Privacy & Security → Microphone.
    func openMicrophoneSettings() {
        if let url = URL(string: "x-apple.systempreferences:com.apple.preference.security?Privacy_Microphone") {
            NSWorkspace.shared.open(url)
        }
    }

    /// After a failure: connect again, or start the server again.
    func retryLaunch() {
        serverFailed = false
        errorMessage = nil
        refusals = 0
        downloadPercent = nil
        status = t("launch.connecting")
        launch()
    }

    /// This app started the model server, so it can reload it in another precision.
    var ownsServer: Bool { launcher.ownsServer }

    /// BF16 on a Mac that is too slow for it: the server's measured step leaves too little of the
    /// 133 ms frame, so the model can fall behind and stutter.
    var precisionTooSlow: Bool { precision == "bf16" && stepMs > slowStepMs }

    /// Reload the current model in int8 or bf16: the server is restarted, the
    /// Card, voice and settings stay.
    func setPrecision(_ precision: String) {
        guard precision != self.precision, phase == .ready, !isRecording, !modelPath.isEmpty, ownsServer else { return }
        let old = conn
        conn = nil
        old?.close()
        phase = .launching
        reloading = true
        status = t("launch.reloading", ["precision": precision == "bf16" ? "BF16" : "int8"])
        Stored.set("precision", precision)
        let info = Bundle.main.infoDictionary ?? [:]
        let python = info["HaruPython"] as? String ?? "/usr/bin/python3"
        launcher.restart(
            socket: socket, python: python,
            arguments: ["--model", modelPath, "--quantize", precision == "bf16" ? "none" : "8", "--language", language],
            onLog: logLine, onConnected: { [weak self] conn in self?.receiveAssets(conn) },
            onFailure: launchFailed)
    }

    private lazy var logLine: (String) -> Void = { [weak self] line in
        guard let self else { return }
        self.serverLog.append(line)
        fileLog(line)
        if self.serverLog.count > 300 { self.serverLog.removeFirst() }
        guard self.phase == .launching, !self.reloading else { return }
        if let percent = downloadProgress(line) {
            self.downloadPercent = percent
            self.status = t("launch.downloading", ["percent": percent])
        } else if (self.downloadPercent ?? 100) >= 100 {
            self.status = t("launch.loading")
        }
    }

    private lazy var launchFailed: (String) -> Void = { [weak self] message in
        self?.failed(message)
    }

    private func failed(_ message: String) {
        // A download that stopped half-way, or another failure.
        status = (downloadPercent ?? 100) < 100 ? t("launch.downloadFailed") : t("launch.failed")
        errorMessage = message
        serverFailed = true
    }

    /// The server sends its card templates, presets and voices on connect,
    /// Answers each PREPARE with READY, and START with HELLO (or ERROR), after
    /// Which the conversation runs on this connection.
    private func receiveAssets(_ conn: Connection) {
        self.conn = conn
        prepareSeq = 0
        preparedKey = nil
        prepare = .idle
        DispatchQueue.global().async {
            guard let (kind, payload) = conn.readMessage(), kind == Message.assets.rawValue else {
                // The server turns clients away while the last one is still closing,
                // Which a reconnect right after a conversation can hit: try again.
                DispatchQueue.main.async {
                    guard self.conn === conn else { return }
                    self.conn = nil
                    self.refusals += 1
                    if self.refusals <= 20 {
                        DispatchQueue.main.asyncAfter(deadline: .now() + 0.5) { self.reconnect() }
                    } else {
                        self.failed(t("error.busy"))
                    }
                }
                return
            }
            DispatchQueue.main.async { self.refusals = 0 }
            let assets = decodeJSON(payload)
            DispatchQueue.main.async {
                self.apply(assets)
                self.schedulePrepare()
            }
            while let (kind, payload) = conn.readMessage() {
                switch kind {
                case Message.ready.rawValue:
                    let reply = decodeJSON(payload)
                    DispatchQueue.main.async { self.prepared(reply) }
                case Message.error.rawValue:
                    let message = decodeJSON(payload)["message"] as? String ?? t("error.title")
                    DispatchQueue.main.async { self.endConversation(error: message) }
                    return
                case Message.hello.rawValue:
                    self.converse(conn, hello: decodeJSON(payload))
                    return
                default:
                    break
                }
            }
            DispatchQueue.main.async {
                if self.conn === conn { self.endConversation(error: t("error.disconnected")) }
            }
        }
    }

    private func reconnect() {
        DispatchQueue.global().async {
            var conn: Connection?
            // Up to 60 s, so a server restarted with another model can finish loading.
            for _ in 0..<600 where conn == nil {
                conn = Connection(path: self.socket)
                if conn == nil { usleep(100_000) }
            }
            DispatchQueue.main.async {
                if let conn { self.receiveAssets(conn) } else {
                    self.failed(t("error.reconnect"))
                }
            }
        }
    }

    func apply(_ assets: [String: Any]) {
        templates = assets["templates"] as? [String: [String: Any]] ?? [:]
        precision = assets["precision"] as? String ?? ""
        modelPath = assets["model"] as? String ?? modelPath
        stepMs = assets["step_ms"] as? Double ?? 0
        languages = (assets["languages"] as? [[String: Any]] ?? []).map {
            ($0["code"] as? String ?? "", $0["label"] as? String ?? "")
        }
        phase = .ready
        reloading = false
        status = ""
    }

    // MARK: Card

    /// Shows the selected character's version in that language; asks first
    /// When the draft has unsaved changes.
    func selectLanguage(_ code: String) {
        guard code != language, confirmDiscard() else { return }
        if let baseline { draft = baseline }
        language = code
        loadVersion()
    }

    // MARK: Voice reference

    /// The audio file of the draft's voice.
    var referenceURL: URL? { draftVoice?.audioURL }

    /// What is still missing before a conversation can start, as in the web client.
    var blocker: String? {
        if microphoneDenied { return t("error.microphoneDenied") }
        if missingVersion || writingVersion { return t("state.versionUnsaved", ["language": languageLabel(language)]) }
        if card.persona.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty { return t("state.personaMissing") }
        if renderedCard.unicodeScalars.count > Self.promptMaxChars {
            return t("error.prompt_too_long", ["max_chars": Self.promptMaxChars])
        }
        if referenceURL == nil { return t("state.voiceMissing") }
        return nil
    }

    // MARK: Devices

    func chooseInput(_ name: String) {
        inputChoice = name
        Stored.set("inputDevice", name)
        refreshDevices()
    }

    func chooseOutput(_ name: String) {
        outputChoice = name
        Stored.set("outputDevice", name)
        refreshDevices()
    }

    /// Reads the device list and applies the device rule (DeviceChoice.swift).
    /// The devices of a running conversation never change.
    func refreshDevices() {
        let all = audioDevices()
        inputDevices = all.filter { $0.inputs > 0 }.map(\.name)
        outputDevices = all.filter { $0.outputs > 0 }.map(\.name)
        // The recording's microphone was removed: keep what it recorded.
        if let recorder = voiceRecorder, !all.contains(where: { $0.id == recorder.device.id }) { stopVoiceRecording() }
        guard phase != .starting, phase != .talking else { return }
        let input = resolveDevice(
            choice: inputChoice, available: inputDevices,
            automatic: automaticInput(inputDevices, systemDefault: findDevice(nil, input: true)?.name))
        let output = resolveDevice(
            choice: outputChoice, available: outputDevices,
            automatic: automaticOutput(outputDevices, systemDefault: findDevice(nil, input: false)?.name))
        (inputChoice, inputDevice) = input
        (outputChoice, outputDevice) = output
    }

    /// Follows devices that are plugged in or removed, and changes of the system
    /// Defaults (several notices come at once: one refresh after them).
    private func watchDevices() {
        let selectors = [kAudioHardwarePropertyDevices, kAudioHardwarePropertyDefaultInputDevice,
                         kAudioHardwarePropertyDefaultOutputDevice]
        for selector in selectors {
            var address = AudioObjectPropertyAddress(
                mSelector: selector, mScope: kAudioObjectPropertyScopeGlobal,
                mElement: kAudioObjectPropertyElementMain)
            AudioObjectAddPropertyListenerBlock(AudioObjectID(kAudioObjectSystemObject), &address, .main) {
                [weak self] _, _ in
                guard let self else { return }
                self.deviceRefresh?.cancel()
                let work = DispatchWorkItem { [weak self] in self?.refreshDevices() }
                self.deviceRefresh = work
                DispatchQueue.main.asyncAfter(deadline: .now() + 0.3, execute: work)
            }
        }
    }

    // MARK: Saving

    /// True when no unsaved conversation is lost, or the user chose "Don't save".
    /// "Save…" stops the action: the save buttons stay on the setup screen.
    func confirmLoseConversation() -> Bool {
        guard lastUnsaved else { return true }
        if ask(t("save.notSaved"), confirm: t("save.choose"), cancel: t("save.dontSave")) { return false }
        lastUnsaved = false
        return true
    }

    func saveAudio() {
        guard let recording = lastRecording else { return }
        let panel = NSSavePanel()
        panel.nameFieldStringValue = "haru audio.wav"
        panel.allowedContentTypes = [.wav]
        guard panel.runModal() == .OK, let url = panel.url else { return }
        do {
            try recording.writeAudio(to: url)
            lastUnsaved = false
        } catch {
            errorMessage = t("error.saveAudio", ["error": error.localizedDescription])
        }
    }

    /// The model's text of the last conversation as a .txt file.
    func saveText() {
        let panel = NSSavePanel()
        panel.nameFieldStringValue = "haru text.txt"
        panel.allowedContentTypes = [.plainText]
        guard panel.runModal() == .OK, let url = panel.url else { return }
        do {
            try Data(lastText.utf8).write(to: url, options: .atomic)
            lastUnsaved = false
        } catch {
            errorMessage = t("error.saveText", ["error": error.localizedDescription])
        }
    }

    func saveVideo() {
        guard let recording = lastRecording, exportProgress == nil else { return }
        let panel = NSSavePanel()
        panel.nameFieldStringValue = "haru video.mp4"
        panel.allowedContentTypes = [.mpeg4Movie]
        guard panel.runModal() == .OK, let url = panel.url else { return }
        exportProgress = 0
        Task { @MainActor in
            do {
                try await recording.writeVideo(
                    to: url, userName: card.otherName.isEmpty ? t("speaker.you") : card.otherName,
                    modelName: card.name.isEmpty ? t("speaker.model") : card.name) { self.exportProgress = $0 }
                lastUnsaved = false
            } catch {
                errorMessage = t("error.saveVideo", ["error": error.localizedDescription])
            }
            exportProgress = nil
        }
    }

    /// A warning when the chosen devices add a lot of latency.
    var deviceWarning: String? {
        var parts: [String] = []
        if outputDevice.contains("Speakers") { parts.append(t("devices.speakers")) }
        if inputDevice.contains("MacBook") { parts.append(t("devices.microphone")) }
        if (inputDevice + outputDevice).contains("AirPods") { parts.append(t("devices.bluetooth")) }
        return parts.isEmpty ? nil : t("devices.warning", ["parts": parts.joined(separator: t("devices.separator"))])
    }

    // MARK: Prefill

    /// START's request: the card, the language, the guidance scale, and the voice.
    private var request: [String: Any] {
        var request: [String: Any] = ["card": card.json, "language": language, "guidance_scale": cfgScale]
        if let url = referenceURL { request["reference_path"] = url.path }
        return request
    }

    private func key(_ request: [String: Any]) -> String {
        let data = try? JSONSerialization.data(withJSONObject: request, options: .sortedKeys)
        return data.flatMap { String(data: $0, encoding: .utf8) } ?? ""
    }

    /// Has the server prefill the current settings 5 s after the last edit, so
    /// START answers at once (START prefills them itself if they changed since).
    /// "Ready" goes at once when the request changes; it comes back after the next PREPARE.
    func schedulePrepare() {
        if prepare != .idle, key(request) != preparedKey { prepare = .idle }
        prepareWork?.cancel()
        let work = DispatchWorkItem { [weak self] in self?.sendPrepare() }
        prepareWork = work
        DispatchQueue.main.asyncAfter(deadline: .now() + 5, execute: work)
    }

    private func sendPrepare() {
        guard let conn, phase == .ready, !isRecording, blocker == nil else { return }
        let request = request, key = key(request)
        // The same request again after an edit was undone: the server answers READY at once.
        guard key != preparedKey || prepare == .idle else { return }
        preparedKey = key
        prepareSeq += 1
        prepare = .preparing
        conn.sendJSON(.prepare, request)
    }

    private func prepared(_ reply: [String: Any]) {
        // A READY for a request that has changed since says nothing about the current one.
        guard reply["seq"] as? Int == prepareSeq, phase == .ready, key(request) == preparedKey else { return }
        prepare = reply["ok"] as? Bool == true ? .ready : .failed(reply["message"] as? String ?? t("error.title"))
    }

    // MARK: Conversation

    /// Sends START; the screen switches once the server answers with the
    /// Prefilled session (at once when PREPARE already prefilled it) and the
    /// Audio is running.
    func startConversation() {
        guard let conn, phase == .ready, blocker == nil, confirmLoseConversation() else { return }
        playback.stop()
        guard let input = findDevice(inputDevice, input: true),
              let output = findDevice(outputDevice, input: false) else {
            errorMessage = t("error.noDevice")
            return
        }
        prepareWork?.cancel()
        plan = (input, output, marginMs)
        phase = .starting
        transcript = ""
        latencyMs = nil
        conn.sendJSON(.start, request)
    }

    /// Runs the conversation on the reader thread, from HELLO until the
    /// Connection closes.
    private func converse(_ conn: Connection, hello: [String: Any]) {
        guard let (input, output, margin) = plan else { return }
        let audio = DuplexAudio(
            input: input, output: output, bufferMs: 2, marginMs: margin,
            frameSamples: hello["frame_samples"] as? Int ?? 6400,
            stepMs: hello["step_ms"] as? Double ?? 36, onAudio: { conn.sendAudio($0) })
        // The last conversation is always kept, so that it can be saved.
        let recorder = ConversationRecorder()
        let waves = self.waves
        waves.reset()
        audio.inputTap = { x, adc in
            recorder.input(x, adc: adc)
            waves.input(x, at: adc)
        }
        audio.outputTap = { x, dac in
            recorder.output(x, dac: dac)
            waves.output(x, at: dac)
        }
        do {
            try audio.start()
        } catch {
            DispatchQueue.main.async { self.endConversation(error: t("error.audio", ["error": error])) }
            return
        }
        let info = String(format: "buffer input %.1f ms / output %.1f ms, margin %.0f ms",
                          audio.inputBufferMs, audio.outputBufferMs, audio.marginMs)
        fileLog("[app] start: input \(input.name) / output \(output.name) | " + info
                + String(format: " | lead %.0f ms", audio.lead * 1000))
        // Each device's own latency plus its buffer, next to its name.
        let devices = String(format: "%@ (%.1f ms) → %@ (%.1f ms)",
                             input.name, audio.inLatency * 1000 + audio.inputBufferMs,
                             output.name, audio.outLatency * 1000 + audio.outputBufferMs)
        DispatchQueue.main.async {
            self.audio = audio
            self.capture = recorder
            self.audioInfo = devices
            self.muted = false
            self.talkStarted = Date()
            self.phase = .talking
            self.startTimer()
        }
        while let (kind, payload) = conn.readMessage() {
            if kind == Message.frame.rawValue {
                let f = decodeFrame(payload)
                audio.receive(index: f.index, pcm: f.pcm, step: f.stepSeconds)
            } else if kind == Message.text.rawValue, let text = String(bytes: payload, encoding: .utf8) {
                // Heard once its frame has waited out the margin and left the device.
                let heardAt = now() + audio.marginMs / 1000 + audio.outLatency
                recorder.text(text, heardAt: heardAt)
                waves.text(text, heardAt: heardAt)
                DispatchQueue.main.async { self.transcript += text }
            }
        }
        // The server closed the connection: it stopped (or crashed) during the call.
        DispatchQueue.main.async { if self.phase == .talking { self.stopConversation(error: t("error.disconnected")) } }
    }

    /// Ends the conversation: by the user, or with the reason it ended (`error`).
    func stopConversation(error: String? = nil) {
        guard phase == .talking, let audio else { return }
        let r = audio.report()  // Before stopping: once the input stops, no more frames come
        lastReport = r
        fileLog(String(format: "[app] end: %d frames, latency p50 %.0f / p95 %.0f / max %.0f ms, underruns %d, caught up %.0f ms",
                       r.frames, r.p50 * 1000, r.p95 * 1000, r.maximum * 1000, r.underruns, r.caughtUpMs)
                + " | " + r.parts.map { String(format: "%@ %.1f", $0.0, $0.1 * 1000) }.joined(separator: ", "))
        audio.stop()
        self.audio = nil
        lastRecording = capture?.finish()
        capture = nil
        lastUnsaved = lastRecording != nil
        lastText = transcript
        endConversation(error: error)
    }

    private func endConversation(error: String?) {
        timer?.invalidate()
        timer = nil
        conn?.close()
        conn = nil
        if let error { errorMessage = error; fileLog("[app] error: \(error)") }
        phase = .launching
        status = t("launch.next")
        refreshDevices()  // The changes during the conversation
        reconnect()
    }

    private func startTimer() {
        levels.source = { [weak self] in
            guard let audio = self?.audio else { return (0, 0) }
            return (audio.inputLevel, audio.outputLevel)
        }
        timer = Timer.scheduledTimer(withTimeInterval: 0.25, repeats: true) { [weak self] _ in
            guard let self, let audio = self.audio else { return }
            if let started = self.talkStarted,
               Date().timeIntervalSince(started) >= Double(Self.timeLimitMinutes * 60) {
                // The server's limit: the model sends no more frames after it.
                self.stopConversation()
                DispatchQueue.main.async { self.inform(t("status.timeLimit", ["minutes": Self.timeLimitMinutes])) }
                return
            }
            self.latencyMs = audio.recentLatency().map { $0 * 1000 }
            os_unfair_lock_lock(&audio.player.lock)
            self.underruns = audio.player.underruns
            self.caughtUpMs = Double(audio.player.compressed) / audio.rate * 1000
            os_unfair_lock_unlock(&audio.player.lock)
        }
    }
}

/// Appends a line to ~/Library/Logs/Haru/app.log (server output and conversation stats).
func fileLog(_ line: String) {
    logQueue.async {
        let dir = FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent("Library/Logs/Haru")
        try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        let url = dir.appendingPathComponent("app.log")
        let stamp = logFormatter.string(from: Date())
        let data = Data("\(stamp) \(line)\n".utf8)
        if let h = try? FileHandle(forWritingTo: url) {
            h.seekToEndOfFile()
            h.write(data)
            try? h.close()
        } else {
            try? data.write(to: url)
        }
    }
}

private let logQueue = DispatchQueue(label: "fileLog")
private let logFormatter: DateFormatter = {
    let f = DateFormatter()
    f.dateFormat = "yyyy-MM-dd HH:mm:ss"
    return f
}()

/// A model step above this (of the 133 ms frame) is too slow: the app suggests int8.
let slowStepMs = 110.0
