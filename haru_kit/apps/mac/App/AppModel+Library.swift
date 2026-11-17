// The character library on the setup screen: choosing a character and the
// Version of the current language, which the editor holds as a draft, and the
// Characters of the library (save, duplicate, delete). Work in the draft is never
// Lost without a question.

import AppKit

/// The editor's copy of a character's version in the current language.
struct Draft: Equatable {
    var title = "", summary = "", voice = ""
    /// The picture: a name in the library's images/, or a file the user chose (an absolute path).
    var image = ""
    /// The character's author (one for all its versions; the model does not see it).
    var author = ""
    var card = CardFields()

    init() {}

    init(_ version: CharacterVersion, image: String, author: String) {
        title = version.title
        summary = version.summary
        voice = version.voice
        card = version.card
        self.image = image
        self.author = author
    }

    var version: CharacterVersion { CharacterVersion(title: title, summary: summary, voice: voice, card: card) }
}

extension AppModel {
    /// The language codes in the server's order.
    var languageOrder: [String] { languages.isEmpty ? UILanguage.supported : languages.map(\.code) }

    func languageLabel(_ code: String) -> String { languages.first { $0.code == code }?.label ?? code }

    var selectedCharacter: LibraryCharacter? { characters.first { $0.id == selectedID } }

    /// The tiles, filtered to the current language when the switch is on.
    var shownCharacters: [LibraryCharacter] {
        onlyLanguage ? characters.filter { $0.versions[language] != nil } : characters
    }

    func imageURL(_ character: LibraryCharacter) -> URL? { library.imageURL(character) }

    /// The draft's picture: a file the user chose, or the selected character's.
    var draftImageURL: URL? {
        if draft.image.hasPrefix("/") { return URL(fileURLWithPath: draft.image) }
        guard !draft.image.isEmpty, var character = selectedCharacter else { return nil }
        character.image = draft.image
        return library.imageURL(character)
    }

    /// The draft differs from the saved version.
    var unsaved: Bool { selectedCharacter != nil && draft != baseline }

    func reloadLibrary() {
        library.reload()
        characters = library.characters
        libraryVoices = library.voices
    }

    /// The first run's character: Haru (else the first one).
    var defaultCharacterID: String {
        (characters.first { $0.id == "haru" } ?? characters.first)?.id ?? ""
    }

    /// At launch: the character of last time with its unsaved draft, if any; the
    /// First time, Haru in the current language with its voice, so
    /// That Start works at once.
    func restoreCharacter() {
        let saved = Stored.draft()
        let stored = Stored.string("character")
        selectCharacter(characters.contains { $0.id == stored } ? stored : defaultCharacterID)
        guard var saved, saved.character == selectedID, saved.language == language else { return }
        if saved.author == nil { saved.draft.author = draft.author }
        guard saved.draft != draft else { return }
        if selectedCharacter?.versions[language] == nil {
            guard saved.writing else { return }
            writingVersion = true
            baseline = nil
        }
        draft = saved.draft
    }

    func selectCharacter(_ id: String) {
        selectedID = id
        loadVersion()
    }

    /// The selected character's version in the current language, into the editor.
    /// When it has none, the draft keeps the version shown before.
    func loadVersion() {
        writingVersion = false
        guard let character = selectedCharacter else { return }
        if let version = character.versions[language] {
            draft = Draft(version, image: character.image, author: character.author)
        }
        baseline = draft
    }

    /// The selected character has no version in the current language, and none is being written.
    var missingVersion: Bool {
        guard let character = selectedCharacter else { return false }
        return character.versions[language] == nil && !writingVersion
    }

    /// Starts a version in the current language from the fields of the version
    /// Shown before; Start waits until it is saved.
    func writeVersion() {
        writingVersion = true
        baseline = nil
    }

    /// Shows only the characters that have a version in the current language.
    func chooseAnotherCharacter() { onlyLanguage = true }

    /// The voice of the draft, if the library has it.
    var draftVoice: LibraryVoice? { libraryVoices.first { $0.id == draft.voice } }

    /// The draft's voice speaks another language than the version's text (a warning; Start is not blocked).
    var voiceLanguageMismatch: String? {
        guard !missingVersion, let voice = draftVoice, !voice.language.isEmpty, voice.language != language else { return nil }
        return t("voices.languageMismatch", ["voiceLanguage": languageLabel(voice.language),
                                             "textLanguage": languageLabel(language)])
    }

    // MARK: Questions

    /// A modal question; true when the user chose `confirm`.
    func ask(_ message: String, confirm: String, cancel: String, destructive: Bool = false) -> Bool {
        let alert = NSAlert()
        alert.messageText = message
        alert.addButton(withTitle: confirm)
        alert.addButton(withTitle: cancel)
        if destructive { alert.buttons[0].hasDestructiveAction = true }
        return alert.runModal() == .alertFirstButtonReturn
    }

    /// True when the draft has no unsaved changes, or the user discards them.
    func confirmDiscard() -> Bool {
        guard unsaved else { return true }
        let name = draft.title.isEmpty ? t("library.untitled") : draft.title
        return ask(t("character.discardConfirm", ["name": name]),
                   confirm: t("character.discard"), cancel: t("character.keepEditing"), destructive: true)
    }

    // MARK: Characters

    /// A tile was clicked.
    func chooseCharacter(_ id: String) {
        guard id != selectedID, confirmDiscard() else { return }
        reloadLibrary()
        selectCharacter(id)
    }

    /// A new character: empty card, untitled, a version in the current language,
    /// No picture. It is not in the library until Save (which needs a picture).
    func newCharacter() {
        guard confirmDiscard() else { return }
        reloadLibrary()
        let voice = libraryVoices.first { $0.language == language }?.id ?? draft.voice
        let character = LibraryCharacter(
            id: library.newID(),
            versions: [language: CharacterVersion(title: t("library.untitled"), voice: voice)])
        characters.append(character)
        selectCharacter(character.id)
        baseline = nil
    }

    /// The draft has a picture: Save and Duplicate need one.
    var hasPicture: Bool { draftImageURL != nil }

    /// Saves the draft as the version of the current language.
    func saveDraft() {
        guard var character = selectedCharacter, !missingVersion, hasPicture else { return }
        character.versions[language] = draft.version
        character.author = draft.author.trimmingCharacters(in: .whitespacesAndNewlines)
        guard let picture = storePicture(for: character) else { return }
        character.image = picture
        store(character)
    }

    /// Back to the saved version.
    func revertDraft() {
        guard let baseline else { return }
        draft = baseline
    }

    /// A copy of the character with all its versions (and the draft's changes)
    /// In the library; the original keeps its saved state.
    func duplicateCharacter() {
        guard hasPicture, var character = selectedCharacter?.copied(id: library.newID(), today: todayISO()) else { return }
        character.author = draft.author.trimmingCharacters(in: .whitespacesAndNewlines)
        if character.versions[language] != nil || baseline == nil { character.versions[language] = draft.version }
        for (code, version) in character.versions {
            var copy = version
            copy.title = t("character.copyName", ["name": version.title])
            character.versions[code] = copy
        }
        guard let picture = storePicture(for: character) else { return }
        character.image = picture
        store(character)
    }

    /// Deletes a character after a question: `id`, else the selected one.
    func deleteCharacter(_ id: String? = nil) {
        guard let character = id.flatMap({ id in characters.first { $0.id == id } }) ?? selectedCharacter else { return }
        let wasSelected = character.id == selectedID
        let name = character.shown(language, order: languageOrder)?.title ?? ""
        guard ask(t("character.deleteConfirm", ["name": name]), confirm: t("character.delete"),
                  cancel: t("character.keepEditing"), destructive: true) else { return }
        do {
            try library.deleteCharacter(character.id)
        } catch {
            errorMessage = t("error.library", ["error": error.localizedDescription])
        }
        reloadLibrary()
        if wasSelected { selectCharacter(defaultCharacterID) }
    }

    /// Deletes the version of the current language (a character keeps at least one).
    func deleteVersion() {
        guard var character = selectedCharacter, character.versions.count > 1,
              character.versions[language] != nil else { return }
        let params = ["language": languageLabel(language), "name": baseline?.title ?? draft.title]
        guard ask(t("character.deleteVersionConfirm", params), confirm: t("character.delete"),
                  cancel: t("character.keepEditing"), destructive: true) else { return }
        character.versions[language] = nil
        store(character)
    }

    /// The picture of the draft in the library's images, named after `character`
    /// (nil when there is none or it could not be copied).
    private func storePicture(for character: LibraryCharacter) -> String? {
        guard let source = draftImageURL else { return nil }
        do {
            return try library.savePicture(source, for: character.id)
        } catch {
            errorMessage = t("error.library", ["error": error.localizedDescription])
            return nil
        }
    }

    /// Writes a character (its `updated` becomes today), removes a picture
    /// It no longer uses, and selects it.
    private func store(_ character: LibraryCharacter) {
        let old = library.character(character.id)
        do {
            try library.saveEdit(character)
        } catch {
            errorMessage = t("error.library", ["error": error.localizedDescription])
            return
        }
        if let old, !old.image.isEmpty, old.image != character.image { library.removePicture(old.image) }
        reloadLibrary()
        selectCharacter(character.id)
    }

    // MARK: Picture

    /// Chooses the draft's picture (a click on it), or replaces it.
    func choosePicture() {
        let panel = NSOpenPanel()
        panel.allowedContentTypes = [.png, .jpeg, .webP]
        panel.allowsMultipleSelection = false
        if panel.runModal() == .OK, let url = panel.url { draft.image = url.path }
    }
}
