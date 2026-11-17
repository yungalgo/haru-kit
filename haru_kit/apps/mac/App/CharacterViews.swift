// The character section of the setup screen: the card editor and the text that
// The model reads.

import SwiftUI
import AppKit

/// The 7 upstream card fields, each with its label and help text.
struct CardFieldsEditor: View {
    @Binding var card: CardFields

    var body: some View {
        // Labels from your side; the model's prompt keeps the
        // Template's own headers (its name as "your name", yours as "the other's").
        HStack(alignment: .top, spacing: 14) {
            HaruTextField(label: t("field.modelName"), help: t("help.name"), text: $card.name)
            HaruTextField(label: t("field.yourName"), help: t("help.other_name"), text: $card.otherName)
        }
        HStack(alignment: .top, spacing: 14) {
            FieldBox(label: t("field.gender"), help: t("help.gender")) {
                HaruSelect(options: [("", t("option.unspecified")), ("female", t("option.female")), ("male", t("option.male"))],
                           selection: $card.gender)
            }
            FieldBox(label: t("field.backchannel"), help: t("help.backchannel")) {
                HaruSelect(options: [("", t("option.unspecified")), ("low", t("backchannel.low")),
                                     ("mid", t("backchannel.mid")), ("high", t("backchannel.high"))],
                           selection: $card.backchannel)
            }
        }
        HaruTextEditor(label: t("field.persona"), help: t("help.persona"), text: $card.persona, height: 110)
        HaruTextEditor(label: t("field.relationship"), help: t("help.relationship"), text: $card.relationship, height: 64)
        HaruTextEditor(label: t("field.situation"), help: t("help.situation"), text: $card.situation, height: 64)
    }
}

/// The rendered card, read-only and always visible, with its length.
struct RenderedCard: View {
    let text: String
    let maxChars: Int

    var body: some View {
        let count = text.unicodeScalars.count
        VStack(alignment: .leading, spacing: 6) {
            HStack {
                Text(t("prompt.rendered")).font(.system(size: 12, weight: .medium)).foregroundStyle(Theme.muted)
                Spacer()
                Text(t("prompt.length", ["count": count, "max": maxChars]))
                    .font(.system(size: 11)).monospacedDigit()
                    .foregroundStyle(count > maxChars ? Theme.warning : Theme.muted)
            }
            Text(text)
                .font(.system(size: 12, design: .monospaced))
                .foregroundStyle(Theme.ink)
                .textSelection(.enabled)
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(12)
                .background(RoundedRectangle(cornerRadius: 10, style: .continuous).fill(Theme.paper))
            if count > maxChars {
                Label(t("error.prompt_too_long", ["max_chars": maxChars]), systemImage: "exclamationmark.triangle.fill")
                    .font(.system(size: 12)).foregroundStyle(Theme.warning)
            }
        }
    }
}

/// One tile for each character, filtered to the current language when the
/// Switch is on.
struct LibraryGrid: View {
    @EnvironmentObject var model: AppModel

    var body: some View {
        let language = model.languageLabel(model.language)
        let shown = model.shownCharacters
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                Text(t("library.title")).font(.system(size: 15, weight: .semibold))
                Spacer()
                Toggle(t("library.onlyLanguage", ["language": language]), isOn: $model.onlyLanguage)
                    .toggleStyle(.switch).controlSize(.mini).font(.system(size: 12))
                if model.registry != nil {
                    Button { model.browseRegistry() } label: { Label(t("registry.browse"), systemImage: "globe") }
                        .buttonStyle(HaruButtonStyle())
                }
            }
            if model.seeding {
                HStack(spacing: 8) {
                    ProgressView().controlSize(.small)
                    Text(t("library.adding")).font(.system(size: 12)).foregroundStyle(Theme.muted)
                }
            } else if shown.isEmpty {
                Text(t("library.empty", ["language": language])).font(.system(size: 12)).foregroundStyle(Theme.muted)
            }
            LazyVGrid(columns: [GridItem(.adaptive(minimum: 200), spacing: 10)], spacing: 10) {
                ForEach(shown) { character in
                    Button { model.chooseCharacter(character.id) } label: {
                        LibraryTile(character: character, language: model.language, order: model.languageOrder,
                                    image: model.imageURL(character), selected: character.id == model.selectedID)
                    }
                    .buttonStyle(.plain)
                    .overlay(alignment: .topTrailing) {
                        Button { model.deleteCharacter(character.id) } label: {
                            Image(systemName: "trash").font(.system(size: 11)).foregroundStyle(Theme.muted)
                                .frame(width: 26, height: 26).contentShape(Rectangle())
                        }
                        .buttonStyle(.plain)
                        .help(t("character.delete"))
                        .padding(4)
                    }
                    .contextMenu {
                        Button(t("character.delete"), role: .destructive) { model.deleteCharacter(character.id) }
                    }
                }
                Button { model.newCharacter() } label: {
                    Label(t("library.new"), systemImage: "plus")
                        .font(.system(size: 13, weight: .medium))
                        .frame(maxWidth: .infinity, minHeight: 74)
                        .background(RoundedRectangle(cornerRadius: 12, style: .continuous)
                            .strokeBorder(Theme.line, style: StrokeStyle(lineWidth: 1, dash: [5, 4])))
                        .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
            }
        }
    }
}

struct LibraryTile: View {
    let character: LibraryCharacter
    let language: String
    let order: [String]
    let image: URL?
    let selected: Bool

    var body: some View {
        let version = character.shown(language, order: order)
        HStack(alignment: .top, spacing: 10) {
            Picture(url: image, title: version?.title ?? "").frame(width: 48, height: 48)
            VStack(alignment: .leading, spacing: 3) {
                Text(version?.title ?? "").font(.system(size: 13, weight: .semibold)).lineLimit(1)
                Text(version?.summary ?? "").font(.system(size: 11)).foregroundStyle(Theme.muted).lineLimit(2)
                HStack(spacing: 4) {
                    Text(character.languages(order).joined(separator: " "))
                        .font(.system(size: 10, design: .monospaced)).foregroundStyle(Theme.muted).lineLimit(1)
                }
                if let release = releaseLine(version: character.version, updated: character.updated) {
                    Text(release).font(.system(size: 10)).foregroundStyle(Theme.muted).lineLimit(1)
                }
            }
            Spacer(minLength: 0)
        }
        .padding(10)
        .frame(maxWidth: .infinity, minHeight: 74, alignment: .topLeading)
        .background(RoundedRectangle(cornerRadius: 12, style: .continuous).fill(Theme.paper))
        .overlay(RoundedRectangle(cornerRadius: 12, style: .continuous)
            .stroke(selected ? Theme.accent : Theme.line, lineWidth: selected ? 2 : 1))
        .contentShape(Rectangle())
    }
}

/// "Version 1.0 · Updated 2026-11-15" for a character that has them (nil for none).
func releaseLine(version: String, updated: String) -> String? {
    var parts: [String] = []
    if !version.isEmpty { parts.append(t("character.versionLabel", ["version": version])) }
    if !updated.isEmpty { parts.append(t("character.updatedLabel", ["updated": updated])) }
    return parts.isEmpty ? nil : parts.joined(separator: " · ")
}

/// A character's picture, or its first letter on grey when it has none.
struct Picture: View {
    let url: URL?
    let title: String

    var body: some View {
        if let url, let image = NSImage(contentsOf: url) {
            Image(nsImage: image).resizable().scaledToFill()
                .clipShape(RoundedRectangle(cornerRadius: 10, style: .continuous))
        } else {
            RoundedRectangle(cornerRadius: 10, style: .continuous).fill(Theme.fill)
                .overlay(Text(title.prefix(1)).font(.system(size: 18, weight: .semibold)).foregroundStyle(Theme.muted))
        }
    }
}

/// Above the card fields: the picture (a click chooses or replaces it), and
/// The name in the library, the short description and the author next to it.
struct CharacterMeta: View {
    @EnvironmentObject var model: AppModel

    var body: some View {
        HStack(alignment: .top, spacing: 16) {
            Button { model.choosePicture() } label: {
                Group {
                    if let url = model.draftImageURL {
                        Picture(url: url, title: model.draft.title)
                    } else {
                        RoundedRectangle(cornerRadius: 14, style: .continuous)
                            .strokeBorder(Theme.line, style: StrokeStyle(lineWidth: 1, dash: [5, 4]))
                            .background(RoundedRectangle(cornerRadius: 14, style: .continuous).fill(Theme.paper))
                            .overlay(VStack(spacing: 4) {
                                Image(systemName: "photo.badge.plus").font(.system(size: 20))
                                Text(t("character.pictureHint")).font(.system(size: 10))
                                    .multilineTextAlignment(.center)
                            }
                            .foregroundStyle(Theme.muted).padding(6))
                    }
                }
                .frame(width: 96, height: 96)
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .help(t("character.pictureHint"))
            .accessibilityLabel(t("character.picture"))
            VStack(alignment: .leading, spacing: 12) {
                HaruTextField(label: t("character.libraryTitle"), help: t("character.libraryTitleHelp"),
                              text: $model.draft.title)
                HaruTextField(label: t("character.summary"), help: t("character.summaryHelp"),
                              text: $model.draft.summary)
                HaruTextField(label: t("character.author"), help: t("character.authorHelp"),
                              text: $model.draft.author)
            }
        }
    }
}

/// Save, undo, duplicate and delete; "unsaved" while the draft differs from the
/// Saved version. Save needs a picture.
struct CharacterActions: View {
    @EnvironmentObject var model: AppModel

    var body: some View {
        let character = model.selectedCharacter
        VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: 8) {
                Button(t("character.save")) { model.saveDraft() }
                    .buttonStyle(HaruButtonStyle(accent: true))
                    .disabled(!model.unsaved || !model.hasPicture)
                Button(t("character.revert")) { model.revertDraft() }
                    .buttonStyle(HaruButtonStyle())
                    .disabled(!model.unsaved || model.baseline == nil)
                if !model.hasPicture {
                    Label(t("character.pictureRequired"), systemImage: "photo")
                        .font(.system(size: 12)).foregroundStyle(Theme.warning)
                } else if model.unsaved {
                    Label(t("character.unsaved"), systemImage: "pencil.circle.fill")
                        .font(.system(size: 12)).foregroundStyle(Theme.warning)
                }
                Spacer()
            }
            .disabled(character == nil)
            HStack(spacing: 8) {
                Button(t("character.duplicate")) { model.duplicateCharacter() }
                    .buttonStyle(HaruButtonStyle())
                    .disabled(!model.hasPicture)
                Button(t("character.delete")) { model.deleteCharacter() }
                    .buttonStyle(HaruButtonStyle())
                if let character, character.versions.count > 1, character.versions[model.language] != nil {
                    Button(t("character.deleteVersion", ["language": model.languageLabel(model.language)])) {
                        model.deleteVersion()
                    }
                    .buttonStyle(HaruButtonStyle())
                }
                Spacer()
            }
            .disabled(character == nil)
            HStack(spacing: 8) {
                Button { model.exportCharacter() } label: {
                    Label(t("file.exportCharacter"), systemImage: "square.and.arrow.up")
                }
                .buttonStyle(HaruButtonStyle())
                .disabled(character == nil)
                Button { model.importFile() } label: { Label(t("file.import"), systemImage: "square.and.arrow.down") }
                    .buttonStyle(HaruButtonStyle())
                Spacer()
            }
        }
    }
}

/// The selected character has no version in the current language.
struct MissingVersion: View {
    @EnvironmentObject var model: AppModel

    var body: some View {
        let language = model.languageLabel(model.language)
        VStack(alignment: .leading, spacing: 10) {
            Label(t("character.missingVersion", ["language": language]), systemImage: "globe")
                .font(.system(size: 13))
                .fixedSize(horizontal: false, vertical: true)
            HStack(spacing: 8) {
                Button(t("character.writeVersion", ["language": language])) { model.writeVersion() }
                    .buttonStyle(HaruButtonStyle(accent: true))
                Button(t("character.chooseAnother")) { model.chooseAnotherCharacter() }
                    .buttonStyle(HaruButtonStyle())
            }
        }
        .padding(14)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(RoundedRectangle(cornerRadius: 12, style: .continuous).fill(Theme.paper))
    }
}
