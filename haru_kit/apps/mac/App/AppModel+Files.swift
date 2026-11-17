// Export and import of .haru files (HaruFile.swift) from the setup screen.

import AppKit
import UniformTypeIdentifiers

extension AppModel {
    private var haruType: UTType { UTType(filenameExtension: "haru") ?? .zip }

    /// The saved character, with its picture and voices. With unsaved changes,
    /// The user saves them first (or cancels), so the file equals the library.
    func exportCharacter() {
        if unsaved {
            guard ask(t("file.saveFirst"), confirm: t("character.save"), cancel: t("voices.cancel")) else { return }
            saveDraft()
            guard !unsaved else { return }
        }
        guard let character = selectedCharacter else { return }
        let title = character.shown(language, order: languageOrder)?.title ?? character.id
        export(title) { try library.exportCharacter(character.id) }
    }

    func exportVoice(_ voice: LibraryVoice) {
        export(voice.name) { try library.exportVoice(voice.id) }
    }

    private func export(_ name: String, _ entries: () throws -> [(name: String, data: Data)]) {
        let panel = NSSavePanel()
        panel.nameFieldStringValue = name.replacingOccurrences(of: "/", with: "-") + ".haru"
        panel.allowedContentTypes = [haruType]
        guard panel.runModal() == .OK, let url = panel.url else { return }
        do {
            try storeZip(try entries()).write(to: url, options: .atomic)
        } catch {
            errorMessage = t("file.exportFailed", ["error": error.localizedDescription])
        }
    }

    /// Adds the characters and voices of a .haru file to the user's library and
    /// Selects the imported character.
    func importFile() {
        guard confirmDiscard() else { return }
        let panel = NSOpenPanel()
        panel.allowedContentTypes = [haruType]
        panel.allowsMultipleSelection = false
        guard panel.runModal() == .OK, let url = panel.url else { return }
        do {
            let folder = try unzip(url)
            defer { try? FileManager.default.removeItem(at: folder) }
            let result = try library.importHaru(folder) { name in
                !ask(t("file.replaceConfirm", ["name": name]), confirm: t("file.replace"), cancel: t("file.keepBoth"))
            }
            reloadLibrary()
            if let first = result.characters.first { selectCharacter(first) }
            inform(t("file.imported", ["items": result.names.joined(separator: ", ")]))
        } catch let error as HaruFileError {
            reloadLibrary()
            errorMessage = error.localizedDescription
        } catch {
            reloadLibrary()
            errorMessage = t("file.importFailed", ["error": error.localizedDescription])
        }
    }

    func inform(_ message: String) {
        let alert = NSAlert()
        alert.messageText = message
        alert.addButton(withTitle: "OK")
        alert.runModal()
    }
}
