// Modified from higurashi-ai/Haru (Apache-2.0) for haru-kit.
// Screens in Haru's style: white pages, soft grey rounded cards, capsule
// Buttons and one red-orange accent, the coral of the model's lane. The orbs and waveform lanes (Waveform.swift)
// Draw at screen rate inside TimelineView, so they never re-render the rest
// Of the window.

import SwiftUI

// MARK: - Theme

extension Color {
    init(hex: UInt32, opacity: Double = 1) {
        self.init(.sRGB, red: Double((hex >> 16) & 0xff) / 255, green: Double((hex >> 8) & 0xff) / 255,
                  blue: Double(hex & 0xff) / 255, opacity: opacity)
    }
}

enum Theme {
    static let ink = Color(hex: 0x1d1d1f)      // Text
    static let muted = Color(hex: 0x6e6e73)    // Secondary text
    static let line = Color(hex: 0xd2d2d7)     // Field borders, silence dots
    static let paper = Color.white             // Page
    static let card = Color(hex: 0xf5f5f7)     // Grouped sections, lanes
    static let fill = Color(hex: 0xe8e8ed)     // Secondary buttons
    static let accent = Color(hex: 0xe04a2f)   // The model lane's coral, dark enough for white text on it
    static let success = accent
    static let warning = Color(hex: 0xb64400)
}

/// Capsule buttons: red-orange and filled for the main action (`accent`) or a
/// Chosen option (`selected`), grey otherwise.
struct HaruButtonStyle: ButtonStyle {
    var selected = false
    var accent = false
    var large = false

    func makeBody(configuration: Configuration) -> some View {
        CapsuleLabel(configuration: configuration, filled: selected || accent, large: large)
    }

    private struct CapsuleLabel: View {
        let configuration: ButtonStyleConfiguration
        let filled: Bool
        let large: Bool
        @Environment(\.isEnabled) private var enabled

        var body: some View {
            configuration.label
                .font(.system(size: large ? 15 : 13, weight: large ? .semibold : .medium))
                .padding(.vertical, large ? 11 : 6)
                .padding(.horizontal, large ? 22 : 14)
                .foregroundStyle(filled ? Color.white : Theme.ink)
                .background(Capsule().fill(filled ? Theme.accent : Theme.fill))
                .opacity(enabled ? (configuration.isPressed ? 0.75 : 1) : 0.4)
                .contentShape(Capsule())
        }
    }
}

/// A grouped section: soft grey, rounded, like Settings.
struct Card<Content: View>: View {
    @ViewBuilder let content: Content

    var body: some View {
        VStack(alignment: .leading, spacing: 16) { content }
            .padding(22)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(RoundedRectangle(cornerRadius: 18, style: .continuous).fill(Theme.card))
    }
}

struct FieldBox<Content: View>: View {
    let label: String
    var help = ""
    @ViewBuilder let content: Content

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(label).font(.system(size: 12, weight: .medium)).foregroundStyle(Theme.muted)
            content
            if !help.isEmpty {
                Text(help).font(.system(size: 11)).foregroundStyle(Theme.muted)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
    }
}

/// White, rounded, with a blue ring while focused.
private struct FieldChrome: ViewModifier {
    var focused = false

    func body(content: Content) -> some View {
        content
            .background(RoundedRectangle(cornerRadius: 10, style: .continuous).fill(Theme.paper))
            .overlay(RoundedRectangle(cornerRadius: 10, style: .continuous)
                .stroke(focused ? Theme.accent : Theme.line, lineWidth: focused ? 2 : 1))
    }
}

struct HaruTextField: View {
    let label: String
    var help = ""
    @Binding var text: String
    @FocusState private var focused: Bool

    var body: some View {
        FieldBox(label: label, help: help) {
            TextField("", text: $text)
                .textFieldStyle(.plain)
                .font(.system(size: 14))
                .focused($focused)
                .padding(.horizontal, 12)
                .padding(.vertical, 9)
                .modifier(FieldChrome(focused: focused))
        }
    }
}

struct HaruTextEditor: View {
    let label: String
    var help = ""
    @Binding var text: String
    var height: CGFloat = 80
    @FocusState private var focused: Bool

    var body: some View {
        FieldBox(label: label, help: help) {
            TextEditor(text: $text)
                .font(.system(size: 14))
                .scrollContentBackground(.hidden)
                .focused($focused)
                .padding(8)
                .frame(minHeight: height)
                .modifier(FieldChrome(focused: focused))
        }
    }
}

struct HaruSelect: View {
    let options: [(String, String)]
    @Binding var selection: String

    var body: some View {
        // The whole field opens the menu: a plain button-style menu draws this
        // Label as is, where a borderless one only answers clicks on its text.
        Menu {
            ForEach(options, id: \.0) { tag, label in Button(label) { selection = tag } }
        } label: {
            HStack(spacing: 8) {
                Text(options.first { $0.0 == selection }?.1 ?? selection).font(.system(size: 14))
                    .foregroundStyle(Theme.ink).lineLimit(1)
                Spacer(minLength: 0)
                Image(systemName: "chevron.up.chevron.down").font(.system(size: 10, weight: .semibold))
                    .foregroundStyle(Theme.muted)
            }
            .padding(.horizontal, 12)
            .frame(maxWidth: .infinity, minHeight: 38)
            .contentShape(Rectangle())
        }
        .menuStyle(.button)
        .buttonStyle(.plain)
        .menuIndicator(.hidden)
        .modifier(FieldChrome())
    }
}

struct SectionTitle: View {
    let title: String
    let note: String

    var body: some View {
        VStack(alignment: .leading, spacing: 3) {
            Text(title).font(.system(size: 20, weight: .semibold))
            if !note.isEmpty {
                Text(note).font(.system(size: 13)).foregroundStyle(Theme.muted)
            }
        }
    }
}

/// The model's orb at rest, as a logo.
struct IdleOrb: View {
    @State private var state = BlobState()

    var body: some View {
        TimelineView(.animation) { timeline in
            Canvas { ctx, size in
                BlobPainter.draw(ctx, size: size, level: 0, time: timeline.date.timeIntervalSinceReferenceDate,
                                 state: state, colors: Palette.model)
            }
        }
    }
}

struct Masthead: View {
    var compact = false

    var body: some View {
        if compact {
            HStack(spacing: 6) {
                IdleOrb().frame(width: 30, height: 30)
                Text("Haru").font(.system(size: 22, weight: .semibold)).tracking(-0.3)
            }
        } else {
            VStack(spacing: 6) {
                HStack(spacing: 8) {
                    // The orb fills about the middle half of its canvas: lay it
                    // Out at that width so the pair is centred as it looks.
                    IdleOrb().frame(width: 84, height: 84).padding(.horizontal, -20)
                    Text("Haru").font(.system(size: 52, weight: .semibold)).tracking(-1)
                }
                Text("Full-Duplex Speech-to-Speech AI").font(.system(size: 20, weight: .medium))
                    .foregroundStyle(Theme.muted)
            }
        }
    }
}

/// The globe menu: the one language of the app's text and of the model (the card, the voice, the library).
struct LanguageMenu: View {
    @EnvironmentObject var model: AppModel

    var body: some View {
        Menu {
            Picker(t("app.language"), selection: Binding(get: { model.language }, set: { model.selectLanguage($0) })) {
                ForEach(UILanguage.supported, id: \.self) { code in Text(UILanguage.names[code] ?? code).tag(code) }
            }
            .pickerStyle(.inline)
            .labelsHidden()
        } label: {
            Image(systemName: "globe").font(.system(size: 15)).foregroundStyle(Theme.muted)
                .frame(width: 30, height: 30).contentShape(Rectangle())
        }
        .menuStyle(.button)
        .buttonStyle(.plain)
        .menuIndicator(.hidden)
        .fixedSize()
        .disabled(model.phase == .starting)
        .help(t("app.languageHelp"))
        .accessibilityLabel(t("app.language"))
    }
}

// MARK: - Root

struct ContentView: View {
    @EnvironmentObject var model: AppModel

    var body: some View {
        ZStack {
            Theme.paper.ignoresSafeArea()
            switch model.phase {
            case .launching: LaunchView()
            case .ready, .starting: SetupView()
            case .talking: ConversationView()
            }
        }
        .foregroundStyle(Theme.ink)
        .tint(Theme.accent)
        .alert(t("error.title"), isPresented: Binding(
            get: { model.errorMessage != nil }, set: { if !$0 { model.errorMessage = nil } }
        )) {
            Button("OK") { model.errorMessage = nil }
        } message: {
            Text(model.errorMessage ?? "")
        }
    }
}

struct LaunchView: View {
    @EnvironmentObject var model: AppModel

    var body: some View {
        VStack(spacing: 24) {
            Masthead().frame(maxWidth: .infinity).overlay(alignment: .topTrailing) { LanguageMenu() }
            HStack(spacing: 10) {
                if model.serverFailed {
                    Image(systemName: "exclamationmark.triangle.fill").foregroundStyle(Theme.warning)
                    Text(model.status).foregroundStyle(Theme.warning)
                    Button(t("action.retry")) { model.retryLaunch() }
                        .buttonStyle(HaruButtonStyle(accent: true))
                } else {
                    if let percent = model.downloadPercent, percent < 100 {
                        ProgressView(value: Double(percent) / 100).frame(width: 160)
                    } else {
                        ProgressView().controlSize(.small)
                    }
                    Text(model.status).foregroundStyle(Theme.muted)
                }
            }
            ScrollViewReader { proxy in
                ScrollView {
                    VStack(alignment: .leading, spacing: 2) {
                        ForEach(Array(model.serverLog.enumerated()), id: \.offset) { i, line in
                            Text(line).font(.system(size: 11, design: .monospaced))
                                .foregroundStyle(Theme.muted).id(i)
                        }
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .textSelection(.enabled)
                    .padding(14)
                }
                .onChange(of: model.serverLog.count) { n in proxy.scrollTo(n - 1, anchor: .bottom) }
            }
            .background(RoundedRectangle(cornerRadius: 18, style: .continuous).fill(Theme.card))
        }
        .padding(32)
        .frame(maxWidth: 760)
    }
}

// MARK: - Setup

struct SetupView: View {
    @EnvironmentObject var model: AppModel

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 20) {
                Masthead().frame(maxWidth: .infinity).padding(.top, 12).padding(.bottom, 8)
                    .overlay(alignment: .topTrailing) { LanguageMenu() }
                startRow.padding(.bottom, 8)
                if model.microphoneDenied { MicrophoneDenied() }
                if (model.lastReport?.frames ?? 0) > 0 || model.lastRecording != nil { lastConversation }
                cardSection
                VoiceSection()
                settingsSection
                Text(t("footer.limit", ["minutes": AppModel.timeLimitMinutes]))
                    .font(.system(size: 12)).foregroundStyle(Theme.muted).frame(maxWidth: .infinity)
            }
            .padding(.horizontal, 32)
            .padding(.vertical, 28)
            .frame(maxWidth: 720)
            .frame(maxWidth: .infinity)
        }
        .sheet(isPresented: $model.browsing) { RegistrySheet().environmentObject(model) }
    }

    private var lastConversation: some View {
        Card {
            if let r = model.lastReport, r.frames > 0 {
                HStack(alignment: .bottom, spacing: 28) {
                    Stat(label: t("stats.lastLatency"), value: String(format: "%.0f ms", r.p50 * 1000), big: true)
                    Stat(label: "p95", value: String(format: "%.0f ms", r.p95 * 1000))
                    Spacer()
                }
            }
            if model.lastRecording != nil { saveRow }
        }
    }

    /// The last conversation's audio, text and video.
    private var saveRow: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack(spacing: 10) {
                Text(t("save.last")).font(.system(size: 13)).foregroundStyle(Theme.muted)
                Button { model.saveAudio() } label: { Label(t("save.audio"), systemImage: "waveform") }
                    .buttonStyle(HaruButtonStyle())
                Button { model.saveText() } label: { Label(t("save.text"), systemImage: "doc.text") }
                    .buttonStyle(HaruButtonStyle())
                Button { model.saveVideo() } label: { Label(t("save.video"), systemImage: "film") }
                    .buttonStyle(HaruButtonStyle())
                    .disabled(model.exportProgress != nil)
                if let p = model.exportProgress {
                    ProgressView(value: p).frame(width: 120)
                    Text(t("save.exporting", ["percent": Int(p * 100)]))
                        .font(.system(size: 12)).monospacedDigit().foregroundStyle(Theme.muted)
                }
                Spacer()
            }
            Text(t("save.audioHelp")).font(.system(size: 12)).foregroundStyle(Theme.muted)
        }
    }

    private var cardSection: some View {
        Card {
            SectionTitle(title: t("character.title"), note: t("character.note"))
            LibraryGrid()
            if model.missingVersion {
                MissingVersion()
            } else {
                if model.writingVersion {
                    Label(t("character.newVersionNote", ["language": model.languageLabel(model.language)]),
                          systemImage: "square.and.pencil")
                        .font(.system(size: 12)).foregroundStyle(Theme.warning)
                }
                CharacterMeta()
                Label(t("character.matchLanguageNote"), systemImage: "info.circle")
                    .font(.system(size: 12)).foregroundStyle(Theme.muted)
                CardFieldsEditor(card: $model.draft.card)
                RenderedCard(text: model.renderedCard, maxChars: AppModel.promptMaxChars)
            }
            CharacterActions()
        }
    }

    /// The settings in the order of both front ends: devices, device advice, CFG
    /// Scale, margin, precision.
    private var settingsSection: some View {
        Card {
            SectionTitle(title: t("settings.title"), note: "")
            HStack(alignment: .top, spacing: 14) {
                FieldBox(label: t("settings.microphone")) {
                    HaruSelect(options: deviceOptions(model.inputDevices, model.inputChoice, model.inputDevice),
                               selection: Binding(get: { model.inputChoice }, set: { model.chooseInput($0) }))
                }
                FieldBox(label: t("settings.output")) {
                    HaruSelect(options: deviceOptions(model.outputDevices, model.outputChoice, model.outputDevice),
                               selection: Binding(get: { model.outputChoice }, set: { model.chooseOutput($0) }))
                }
            }
            deviceAdvice
            // Each control sits between what its two ends do.
            VStack(alignment: .leading, spacing: 4) {
                HStack(spacing: 10) {
                    settingLabel("CFG scale")
                    hint(t("settings.cfgLow"))
                    Slider(value: $model.cfgScale, in: 1.0...2.0, step: 0.05).frame(width: 200)
                    hint(t("settings.cfgHigh"))
                    Text(String(format: "%.2f", model.cfgScale)).monospacedDigit().padding(.leading, 6)
                }
                settingHelp(t("settings.cfgHelp"))
            }
            VStack(alignment: .leading, spacing: 4) {
                HStack(spacing: 10) {
                    settingLabel(t("settings.margin"))
                    hint(t("settings.faster"))
                    Button { model.marginMs = max(5, model.marginMs - 5) } label: { Image(systemName: "minus") }
                        .buttonStyle(HaruButtonStyle())
                    Text("\(Int(model.marginMs)) ms").monospacedDigit().frame(width: 52)
                    Button { model.marginMs = min(40, model.marginMs + 5) } label: { Image(systemName: "plus") }
                        .buttonStyle(HaruButtonStyle())
                    hint(t("settings.steadier"))
                }
                settingHelp(t("settings.marginHelp"))
            }
            VStack(alignment: .leading, spacing: 4) {
                HStack(spacing: 10) {
                    settingLabel(t("settings.precision"))
                    hint(t("settings.accurate"))
                    ForEach([("bf16", "BF16"), ("int8", "int8")], id: \.0) { value, label in
                        Button(label) { model.setPrecision(value) }
                            .buttonStyle(HaruButtonStyle(selected: (model.precision.isEmpty ? "int8" : model.precision) == value))
                    }
                    hint(t("settings.faster"))
                }
                .disabled(model.isRecording || model.exportProgress != nil || !model.ownsServer)
                settingHelp(model.ownsServer ? t("settings.precisionHelp") : t("settings.precisionForeign"))
                if model.precisionTooSlow {
                    HStack(spacing: 10) {
                        Label(t("settings.precisionSlow", ["ms": Int(model.stepMs)]), systemImage: "exclamationmark.triangle.fill")
                            .font(.system(size: 12)).foregroundStyle(Theme.warning)
                        if model.ownsServer {
                            Button(t("settings.useInt8")) { model.setPrecision("int8") }
                                .buttonStyle(.plain).font(.system(size: 12)).foregroundStyle(Theme.accent)
                        }
                    }
                }
            }
        }
    }

    /// "Automatic" with the device it uses now, then each device.
    private func deviceOptions(_ names: [String], _ choice: String, _ device: String) -> [(String, String)] {
        let automatic = choice.isEmpty && !device.isEmpty ? "\(t("settings.automatic")) (\(device))" : t("settings.automatic")
        return [("", automatic)] + names.map { ($0, $0) }
    }

    private func settingHelp(_ text: String) -> some View {
        Text(text).font(.system(size: 12)).foregroundStyle(Theme.muted)
            .fixedSize(horizontal: false, vertical: true)
            .padding(.leading, 110)
    }

    private func settingLabel(_ text: String) -> some View {
        Text(text).font(.system(size: 13, weight: .medium)).frame(width: 100, alignment: .leading)
    }

    private func hint(_ text: String) -> some View {
        Text(text).font(.system(size: 12)).foregroundStyle(Theme.muted)
    }

    private var deviceAdvice: some View {
        HStack(alignment: .top, spacing: 10) {
            Image(systemName: "headphones").font(.system(size: 15)).foregroundStyle(Theme.muted)
            VStack(alignment: .leading, spacing: 6) {
                Text(t("devices.title")).font(.system(size: 13, weight: .semibold))
                Text(t("devices.body"))
                    .font(.system(size: 12)).foregroundStyle(Theme.muted)
                    .fixedSize(horizontal: false, vertical: true)
                if let warning = model.deviceWarning {
                    Label(warning, systemImage: "exclamationmark.triangle.fill")
                        .font(.system(size: 12)).foregroundStyle(Theme.warning)
                }
            }
        }
        .padding(14)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(RoundedRectangle(cornerRadius: 12, style: .continuous).fill(Theme.paper))
    }

    private var startRow: some View {
        HStack(spacing: 14) {
            Spacer()
            prepareStatus
            Button(model.phase == .starting ? t("state.connecting") : t("action.start")) { model.startConversation() }
                .buttonStyle(HaruButtonStyle(accent: true, large: true))
                .keyboardShortcut(.defaultAction)
                .disabled(model.phase != .ready || model.isRecording || model.exportProgress != nil || model.blocker != nil)
        }
    }

    /// Whether the server has prefilled the current card and voice.
    @ViewBuilder private var prepareStatus: some View {
        let small = Font.system(size: 12)
        if let blocker = model.blocker, model.phase == .ready {
            Text(blocker).font(small).foregroundStyle(Theme.muted)
        } else if model.phase == .starting || model.prepare == .preparing {
            ProgressView().controlSize(.small)
            Text(t("state.preparing")).font(small).foregroundStyle(Theme.muted)
        } else if model.prepare == .ready {
            Label(t("state.ready"), systemImage: "checkmark.circle.fill").font(small).foregroundStyle(Theme.success)
        } else if case .failed(let message) = model.prepare {
            Label(message, systemImage: "exclamationmark.triangle.fill").font(small)
                .foregroundStyle(Theme.warning).lineLimit(2)
        }
    }
}

struct Stat: View {
    let label: String
    let value: String
    var big = false

    var body: some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(label).font(.system(size: 11, weight: .medium)).foregroundStyle(Theme.muted)
            Text(value).font(.system(size: big ? 26 : 16, weight: big ? .semibold : .regular))
                .foregroundStyle(big ? Theme.accent : Theme.ink)
                .monospacedDigit()
        }
    }
}

// MARK: - Conversation

struct ConversationView: View {
    @EnvironmentObject var model: AppModel

    var body: some View {
        VStack(spacing: 20) {
            HStack(spacing: 12) {
                Button(t("action.end")) { model.stopConversation() }
                    .buttonStyle(HaruButtonStyle())
                    .keyboardShortcut(.cancelAction)
                Button { model.muted.toggle() } label: {
                    Label(model.muted ? t("live.unmute") : t("live.mute"),
                          systemImage: model.muted ? "mic.slash.fill" : "mic.fill")
                }
                .buttonStyle(HaruButtonStyle(selected: model.muted))
                Circle().fill(model.muted ? Theme.warning : Theme.success).frame(width: 8, height: 8)
                Text(model.muted ? t("live.muted") : t("status.live")).font(.system(size: 13))
                    .foregroundStyle(model.muted ? Theme.warning : Theme.muted)
                if let started = model.talkStarted {
                    TimelineView(.periodic(from: .now, by: 1)) { context in
                        let left = max(0, AppModel.timeLimitMinutes * 60 - Int(context.date.timeIntervalSince(started)))
                        Text(t("live.timeLeft", ["time": String(format: "%02d:%02d", left / 60, left % 60)]))
                            .font(.system(size: 13)).monospacedDigit().foregroundStyle(Theme.muted)
                    }
                }
                Spacer()
                Masthead(compact: true)
            }
            stats
            WaveLanes(levels: model.levels, waves: model.waves,
                      userName: model.draft.card.otherName.isEmpty ? t("speaker.you") : model.draft.card.otherName,
                      modelName: model.draft.card.name.isEmpty ? t("speaker.model") : model.draft.card.name)
            Subtitles(waves: model.waves)
                .padding(.leading, 126)
                .frame(maxHeight: .infinity)
        }
        .padding(24)
    }

    private var stats: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(alignment: .bottom, spacing: 28) {
                Stat(label: t("stats.latency"), value: model.latencyMs.map { String(format: "%.0f ms", $0) } ?? "—",
                     big: true)
                Stat(label: t("stats.step"), value: "\(Int(model.stepMs)) ms")
                Stat(label: t("stats.devices"), value: model.audioInfo)
                Spacer()
            }
        }
        .padding(18)
        .background(RoundedRectangle(cornerRadius: 18, style: .continuous).fill(Theme.card))
    }
}

/// The user refused the microphone: what to do, and a way to System Settings.
struct MicrophoneDenied: View {
    @EnvironmentObject var model: AppModel

    var body: some View {
        HStack(alignment: .top, spacing: 12) {
            Image(systemName: "mic.slash.fill").font(.system(size: 18)).foregroundStyle(Theme.warning)
            VStack(alignment: .leading, spacing: 6) {
                Text(t("error.microphoneDenied")).font(.system(size: 14, weight: .semibold))
                Text(t("error.microphoneFixMac")).font(.system(size: 13)).foregroundStyle(Theme.muted)
                    .fixedSize(horizontal: false, vertical: true)
                Button(t("action.openSettings")) { model.openMicrophoneSettings() }
                    .buttonStyle(HaruButtonStyle(accent: true))
            }
            Spacer()
        }
        .padding(16)
        .background(RoundedRectangle(cornerRadius: 14, style: .continuous).fill(Theme.warning.opacity(0.08)))
    }
}
