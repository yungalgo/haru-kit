// .haru files: a ZIP of library items that moves characters and voices between
// The web app and the Mac app.
//
//   Haru.json                       {"format": 1, "kind": "haru-kit", "characters": [...], "voices": [...]}
//   Characters/<id>.character.json
//   Images/<name>                   (pictures of the characters, if any)
//   Voices/<id>.voice.json
//   Voices/<audio file>

import Foundation

enum HaruFileError: LocalizedError {
    case notHaru, newerFormat

    var errorDescription: String? {
        switch self {
        case .notHaru: return t("file.notHaru")
        case .newerFormat: return t("file.newerFormat")
        }
    }
}

/// The ids that imported items get. An id that is already in the library keeps
/// It (the import replaces the item) unless `keepBoth` says to keep both, then
/// The imported item gets a new id. New ids are added to `taken`.
func planImportIDs(_ ids: [String], existing: Set<String>, taken: inout Set<String>,
                   keepBoth: (String) -> Bool) -> [String: String] {
    var map: [String: String] = [:]
    for id in ids {
        if existing.contains(id) && keepBoth(id) {
            let new = newLibraryID(taken: taken)
            taken.insert(new)
            map[id] = new
        } else {
            map[id] = id
        }
    }
    return map
}

/// A character with its voice references changed to the ids in `voices` (others stay).
func rewriteVoices(_ character: LibraryCharacter, _ voices: [String: String]) -> LibraryCharacter {
    var character = character
    for (code, version) in character.versions {
        character.versions[code]?.voice = voices[version.voice] ?? version.voice
    }
    return character
}

struct ImportResult {
    var characters: [String] = []
    var voices: [String] = []
    /// The names of the imported items, for the message.
    var names: [String] = []
}

extension LibraryStore {
    /// A .haru file's entries: the character with all its versions, its picture,
    /// And every voice that its versions use.
    func exportCharacter(_ id: String) throws -> [(name: String, data: Data)] {
        guard let character = character(id) else { return [] }
        let voiceIDs = Array(Set(character.versions.values.map(\.voice))).sorted().filter { voice($0) != nil }
        var entries = [(name: "haru.json", data: try indexData(characters: [id], voices: voiceIDs)),
                       (name: "characters/\(id).character.json", data: try jsonData(character.json))]
        if let image = imageURL(character), let data = try? Data(contentsOf: image) {
            entries.append((name: "images/\(character.image)", data: data))
        }
        for voiceID in voiceIDs { entries += try voiceEntries(voiceID) }
        return entries
    }

    /// A .haru file's entries with one voice.
    func exportVoice(_ id: String) throws -> [(name: String, data: Data)] {
        [(name: "haru.json", data: try indexData(characters: [], voices: [id]))] + (try voiceEntries(id))
    }

    private func indexData(characters: [String], voices: [String]) throws -> Data {
        try jsonData(["format": libraryFormat, "kind": "haru-kit", "characters": characters, "voices": voices])
    }

    private func voiceEntries(_ id: String) throws -> [(name: String, data: Data)] {
        guard let voice = voice(id) else { return [] }
        return [(name: "voices/\(id).voice.json", data: try jsonData(voice.json)),
                (name: "voices/\(voice.file)", data: try Data(contentsOf: voice.audioURL))]
    }

    /// Adds the items of an extracted .haru folder to the user's library. Each
    /// Voice is stored as a 24 kHz mono 16-bit WAV after the silence trim (all
    /// Are converted before any is saved, so an unreadable clip changes nothing).
    /// `keepBoth(name)` is asked for each item that is already in the user's library.
    func importHaru(_ folder: URL, keepBoth: (String) -> Bool) throws -> ImportResult {
        guard let index = readJSONObject(folder.appendingPathComponent("haru.json")),
              index["kind"] as? String == "haru-kit" else { throw HaruFileError.notHaru }
        if ((index["format"] as? NSNumber)?.intValue ?? 1) > libraryFormat { throw HaruFileError.newerFormat }

        let voicesDir = folder.appendingPathComponent("voices")
        let charsDir = folder.appendingPathComponent("characters")
        let imagesDir = folder.appendingPathComponent("images")
        var voices: [(LibraryVoice, URL)] = []
        for id in (index["voices"] as? [String] ?? []) {
            guard isValidLibraryID(id),
                  let json = readJSONObject(voicesDir.appendingPathComponent("\(id).voice.json")),
                  let voice = LibraryVoice(json: json), voice.id == id else { throw HaruFileError.notHaru }
            let audio = voicesDir.appendingPathComponent(voice.file)
            guard isContained(audio, in: voicesDir), FileManager.default.fileExists(atPath: audio.path) else {
                throw HaruFileError.notHaru
            }
            voices.append((voice, audio))
        }
        var characters: [LibraryCharacter] = []
        for id in (index["characters"] as? [String] ?? []) {
            guard isValidLibraryID(id),
                  let json = readJSONObject(charsDir.appendingPathComponent("\(id).character.json")),
                  let character = LibraryCharacter(json: json), character.id == id else { throw HaruFileError.notHaru }
            characters.append(character)
        }
        guard !voices.isEmpty || !characters.isEmpty else { throw HaruFileError.notHaru }

        var taken = ids.union(voices.map(\.0.id)).union(characters.map(\.id))
        let voiceNames = Dictionary(voices.map { ($0.0.id, $0.0.name) }, uniquingKeysWith: { a, _ in a })
        let voiceMap = planImportIDs(voices.map(\.0.id), existing: Set(self.voices.map(\.id)), taken: &taken) {
            keepBoth(voiceNames[$0] ?? $0)
        }
        let characterNames = Dictionary(characters.map { ($0.id, $0.shown("", order: UILanguage.supported)?.title ?? $0.id) },
                                        uniquingKeysWith: { a, _ in a })
        let characterMap = planImportIDs(characters.map(\.id), existing: Set(self.characters.map(\.id)), taken: &taken) {
            keepBoth(characterNames[$0] ?? $0)
        }

        let work = FileManager.default.temporaryDirectory.appendingPathComponent("haru-voices-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: work, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: work) }
        let stored = try voices.enumerated().map { i, item in
            let wav = work.appendingPathComponent("\(i).wav")
            return (wav: wav, seconds: try storeVoiceAudio(item.1, to: wav))
        }

        var result = ImportResult()
        for ((imported, _), audio) in zip(voices, stored) {
            var voice = imported
            let old = self.voice(voiceMap[voice.id] ?? voice.id)
            voice.id = voiceMap[voice.id] ?? voice.id
            voice.file = "\(voice.id).wav"
            voice.seconds = (audio.seconds * 10).rounded() / 10
            try save(voice, audio: audio.wav)
            if let old, old.file != voice.file {
                try? FileManager.default.removeItem(at: old.audioURL)
            }
            result.voices.append(voice.id)
            result.names.append(voice.name)
        }
        for original in characters {
            var character = rewriteVoices(original, voiceMap)
            character.id = characterMap[original.id] ?? original.id
            let old = self.character(character.id)
            let picture = imagesDir.appendingPathComponent(original.image)
            character.image = !original.image.isEmpty && isContained(picture, in: imagesDir)
                && FileManager.default.fileExists(atPath: picture.path)
                ? try savePicture(picture, for: character.id) : ""
            try save(character)
            if let old, !old.image.isEmpty, old.image != character.image { removePicture(old.image) }
            result.characters.append(character.id)
            result.names.append(characterNames[original.id] ?? character.id)
        }
        return result
    }
}
