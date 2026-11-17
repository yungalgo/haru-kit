// The voice section of the setup screen: the voice library and adding a voice.

import SwiftUI

struct VoiceSection: View {
    @EnvironmentObject var model: AppModel

    var body: some View {
        Card {
            SectionTitle(title: t("voice.title"), note: t("voice.note", ["min": 3, "max": 3]))
            if model.draftVoice == nil {
                Label(t("voices.missing"), systemImage: "exclamationmark.triangle.fill")
                    .font(.system(size: 12)).foregroundStyle(Theme.warning)
            }
            if let mismatch = model.voiceLanguageMismatch {
                Label(mismatch, systemImage: "exclamationmark.triangle.fill")
                    .font(.system(size: 12)).foregroundStyle(Theme.warning)
                    .fixedSize(horizontal: false, vertical: true)
            }
            HStack {
                Toggle(t("voices.onlyLanguage", ["language": model.languageLabel(model.language)]),
                       isOn: $model.voicesOnlyLanguage)
                    .toggleStyle(.switch).controlSize(.mini).font(.system(size: 12))
                Spacer()
            }
            VStack(spacing: 0) {
                ForEach(Array(model.shownVoices.enumerated()), id: \.element.id) { index, voice in
                    if index > 0 { Divider() }
                    VoiceRow(voice: voice)
                }
            }
            .background(RoundedRectangle(cornerRadius: 12, style: .continuous).fill(Theme.paper))
            AddVoice()
        }
    }
}

struct VoiceRow: View {
    @EnvironmentObject var model: AppModel
    let voice: LibraryVoice

    var body: some View {
        let playing = model.playingID == voice.id
        let inUse = model.draft.voice == voice.id
        HStack(spacing: 10) {
            Button { model.togglePlay(voice) } label: {
                Image(systemName: playing ? "stop.fill" : "play.fill").frame(width: 14)
            }
            .buttonStyle(HaruButtonStyle())
            .help(playing ? t("voice.stop") : t("voices.play"))
            VStack(alignment: .leading, spacing: 2) {
                Text(voice.name).font(.system(size: 13, weight: .medium)).lineLimit(1)
                HStack(spacing: 6) {
                    Text(voice.language).font(.system(size: 11, design: .monospaced))
                    Text(model.genderLabel(voice.gender))
                    Text(t("voice.seconds", ["seconds": String(format: "%.1f", voice.seconds)])).monospacedDigit()
                }
                .font(.system(size: 11)).foregroundStyle(Theme.muted)
            }
            Spacer()
            Button { model.exportVoice(voice) } label: { Image(systemName: "square.and.arrow.up") }
                .buttonStyle(HaruButtonStyle())
                .help(t("file.exportVoice"))
            Button { model.deleteVoice(voice) } label: { Image(systemName: "trash") }
                .buttonStyle(HaruButtonStyle())
                .help(t("voices.delete"))
            if inUse {
                if let mismatch = model.voiceLanguageMismatch {
                    Image(systemName: "exclamationmark.triangle.fill")
                        .font(.system(size: 12)).foregroundStyle(Theme.warning)
                        .help(mismatch)
                }
                Label(t("voices.inUse"), systemImage: "checkmark.circle.fill")
                    .font(.system(size: 12, weight: .medium)).foregroundStyle(Theme.accent)
            } else {
                Button(t("voices.use")) { model.useVoice(voice.id) }
                    .buttonStyle(HaruButtonStyle())
                    .disabled(model.missingVersion)
            }
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 8)
    }
}

/// Record a voice (with a text to read aloud) or upload a file; then name it,
/// Listen to what the model gets, and save it.
struct AddVoice: View {
    @EnvironmentObject var model: AppModel

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            if model.isRecording {
                recording
            } else if model.newVoice != nil {
                form
            } else {
                HStack(spacing: 8) {
                    Text(t("voices.add")).font(.system(size: 13, weight: .semibold))
                    Button { model.startVoiceRecording() } label: { Label(t("voices.record"), systemImage: "mic.fill") }
                        .buttonStyle(HaruButtonStyle())
                    Button { model.uploadVoice() } label: { Label(t("voices.upload"), systemImage: "square.and.arrow.up") }
                        .buttonStyle(HaruButtonStyle())
                    Spacer()
                }
                lengthHint
            }
        }
    }

    private var lengthHint: some View {
        Text(t("voices.lengthHint")).font(.system(size: 12)).foregroundStyle(Theme.muted)
    }

    private var recording: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text(t("voices.readThis")).font(.system(size: 13, weight: .semibold))
            Text(t("voices.readText"))
                .font(.system(size: 15)).lineSpacing(4)
                .fixedSize(horizontal: false, vertical: true)
                .padding(14)
                .frame(maxWidth: .infinity, alignment: .leading)
                .background(RoundedRectangle(cornerRadius: 12, style: .continuous).fill(Theme.paper))
            HStack(spacing: 10) {
                Circle().fill(Color(hex: 0xff3b30)).frame(width: 9, height: 9)
                Text(t("voices.recording", ["seconds": Int(model.recordingSeconds),
                                            "min": Int(AppModel.voiceMinSeconds), "max": Int(AppModel.voiceMaxSeconds)]))
                    .font(.system(size: 12)).monospacedDigit().foregroundStyle(Theme.muted)
                Spacer()
                Button(t("voice.stopRecording")) { model.stopVoiceRecording() }
                    .buttonStyle(HaruButtonStyle(selected: true))
                    .disabled(model.recordingSeconds < AppModel.voiceMinSeconds)
            }
            lengthHint
        }
    }

    @ViewBuilder private var form: some View {
        if let voice = model.newVoice {
            let binding = Binding(get: { model.newVoice ?? voice }, set: { model.newVoice = $0 })
            HStack(alignment: .top, spacing: 14) {
                HaruTextField(label: t("voices.name"), text: binding.name)
                FieldBox(label: t("voices.language")) {
                    HaruSelect(options: model.languages.map { ($0.code, $0.label) }, selection: binding.language)
                }
                FieldBox(label: t("voices.gender")) {
                    HaruSelect(options: [("", t("option.unspecified")), ("female", t("option.female")), ("male", t("option.male"))],
                               selection: binding.gender)
                }
            }
            HStack(spacing: 10) {
                Button { model.togglePreview() } label: {
                    Label(t("voices.preview"), systemImage: model.playingID == AppModel.previewID ? "stop.fill" : "play.fill")
                }
                .buttonStyle(HaruButtonStyle())
                Text(t("voices.trimmed", ["seconds": String(format: "%.1f", voice.speechSeconds)])
                     + (voice.speechSeconds > AppModel.voiceMaxSeconds
                        ? " " + t("voices.lastUsed", ["max": Int(AppModel.voiceMaxSeconds)]) : ""))
                    .font(.system(size: 12)).foregroundStyle(Theme.muted)
                    .fixedSize(horizontal: false, vertical: true)
                Spacer()
            }
            if let tooShort = model.newVoiceTooShort {
                Label(tooShort, systemImage: "exclamationmark.triangle.fill")
                    .font(.system(size: 12)).foregroundStyle(Theme.warning)
                    .fixedSize(horizontal: false, vertical: true)
            }
            HStack(spacing: 8) {
                Button(t("voices.save")) { model.saveNewVoice() }
                    .buttonStyle(HaruButtonStyle(accent: true))
                    .disabled(model.newVoiceTooShort != nil)
                Button(t("voices.cancel")) { model.cancelNewVoice() }
                    .buttonStyle(HaruButtonStyle())
                Spacer()
            }
        }
    }
}
