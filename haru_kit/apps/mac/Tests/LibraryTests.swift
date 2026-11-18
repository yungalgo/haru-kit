// Swiftc -parse-as-library App/Library.swift App/HaruFile.swift App/Zip.swift App/Card.swift App/AudioFiles.swift \
//     App/Trim.swift App/Strings.swift App/Strings.gen.swift Tests/Check.swift Tests/Fixture.swift Tests/LibraryTests.swift

import Foundation

@main
struct LibraryTests {
    static let fm = FileManager.default

    /// A library root: the fixture library (Fixture.swift), or empty.
    static func makeRoot(fixture: Bool = true) throws -> URL {
        let root = fm.temporaryDirectory.appendingPathComponent("library-test-\(UUID().uuidString)")
        try fm.createDirectory(at: root, withIntermediateDirectories: true)
        if fixture { try makeFixtureLibrary(root) }
        return root
    }

    /// Writes the entries of a .haru file and extracts it as the app does.
    static func roundTrip(_ entries: [(name: String, data: Data)]) throws -> URL {
        let file = fm.temporaryDirectory.appendingPathComponent("test-\(UUID().uuidString).haru")
        try storeZip(entries).write(to: file)
        defer { try? fm.removeItem(at: file) }
        return try unzip(file)
    }

    /// A voice in the stored form (24 kHz mono 16-bit WAV after the trim), made from the fixture clip.
    static func storedWav(_ url: URL) throws -> URL {
        let clip = url.deletingLastPathComponent().appendingPathComponent("clip-\(UUID().uuidString).wav")
        try writeFixtureClip(clip)
        defer { try? fm.removeItem(at: clip) }
        try storeVoiceAudio(clip, to: url)
        return url
    }

    static func main() throws {
        let rootA = try makeRoot(), rootB = try makeRoot(fixture: false)
        defer { try? fm.removeItem(at: rootA); try? fm.removeItem(at: rootB) }

        // The library.
        let a = LibraryStore(root: rootA)
        let haru = a.character("haru")
        check(haru?.versions.count == 9 && haru?.author == "haru-kit" && haru?.image == "haru.png", "haru with 9 versions")
        check(a.voices.count == 9, "9 voices")
        check(a.voice("en-parker").map { fm.fileExists(atPath: $0.audioURL.path) } == true, "voice audio found")
        let id = a.newID()
        check(id.range(of: "^u-[0-9a-f]{12}$", options: .regularExpression) != nil, "new id: \(id)")

        // A voice (a stored WAV) and a character that uses it and a voice of haru.
        // An import stores it again: a WAV in the stored form keeps its bytes.
        let wav = try storedWav(rootA.appendingPathComponent("my.wav"))
        let voiceID = a.newID()
        try a.save(LibraryVoice(id: voiceID, name: "Mine", language: "en", gender: "female", seconds: 3.2,
                                text: "words", file: "\(voiceID).wav"), audio: wav)
        let charID = a.newID()
        var mine = LibraryCharacter(id: charID, versions: [
            "en": CharacterVersion(title: "Coach", summary: "A coach.", voice: voiceID,
                                   card: CardFields(["persona": "A calm coach.", "name": "Sam", "backchannel": "mid"])),
            "ja": CharacterVersion(title: "コーチ", voice: "ja-aiko", card: CardFields(["persona": "落ち着いたコーチ。"])),
        ])
        mine.image = try a.savePicture(a.imageURL(haru!)!, for: charID)
        try a.save(mine)
        check(a.character(charID)?.image == "\(charID).png", "own picture copied")
        let saved = readJSONObject(rootA.appendingPathComponent("characters/\(charID).character.json"))
        check(saved?["format"] as? Int == 1 && saved?["image"] as? String == "\(charID).png", "character file format")
        check(((saved?["versions"] as? [String: Any])?["en"] as? [String: Any])?["card"].map { ($0 as? [String: Any])?.count } == 7,
              "a card has exactly 7 fields")
        let savedVoice = readJSONObject(rootA.appendingPathComponent("voices/\(voiceID).voice.json"))
        check(savedVoice?["file"] as? String == "\(voiceID).wav", "voice file names its audio")

        // Export the character: index, character, picture, and both voices.
        let entries = try a.exportCharacter(charID)
        let names = Set(entries.map(\.name))
        check(names == ["haru.json", "characters/\(charID).character.json", "images/\(charID).png",
                        "voices/\(voiceID).voice.json", "voices/\(voiceID).wav",
                        "voices/ja-aiko.voice.json", "voices/ja-aiko.wav"], "exported entries: \(names)")
        let index = (try? JSONSerialization.jsonObject(with: entries[0].data)) as? [String: Any]
        check(index?["kind"] as? String == "haru-kit" && index?["format"] as? Int == 1
              && index?["characters"] as? [String] == [charID], "haru.json index")

        // Import into an empty library: every voice of the file is imported (also
        // Ja-aiko, as a stored WAV); the items keep their ids.
        let b = LibraryStore(root: rootB)
        var asked: [String] = []
        var folder = try roundTrip(entries)
        let first = try b.importHaru(folder) { asked.append($0); return true }
        check(asked.isEmpty, "no question without a conflict")
        check(first.characters == [charID], "character keeps its id")
        let imported = b.character(charID)
        check(imported?.versions["ja"]?.voice == "ja-aiko", "voice of haru keeps its id")
        check(b.voice("ja-aiko").map { $0.file == "ja-aiko.wav" && fm.fileExists(atPath: $0.audioURL.path) } == true,
              "voice of haru imported")
        check(imported?.versions["en"]?.voice == voiceID, "own voice keeps its id")
        check(imported?.versions["en"]?.card == mine.versions["en"]?.card, "same card")
        let template: [String: Any] = ["lead": "Lead", "headers": ["persona": "P", "name": "N"], "backchannel": ["mid": "M"]]
        check(renderCard(imported!.versions["en"]!.card.strings, template) == renderCard(mine.versions["en"]!.card.strings, template),
              "same rendered card")
        check((try? Data(contentsOf: b.voice(voiceID)!.audioURL)) == (try? Data(contentsOf: wav)), "same voice audio")
        check(first.voices.sorted() == [voiceID, "ja-aiko"].sorted(), "both voices imported: \(first.voices)")
        check(imported?.image == "\(charID).png" && b.imageURL(imported!).map { fm.fileExists(atPath: $0.path) } == true,
              "picture imported")
        try? fm.removeItem(at: folder)

        // The same file again: asked for each item that is there; "keep both" gives new ids.
        folder = try roundTrip(entries)
        asked = []
        let second = try b.importHaru(folder) { asked.append($0); return true }
        check(asked.sorted() == ["Coach", "Mine", "ja-aiko"], "asked about every item: \(asked)")
        let copy = second.characters.first ?? ""
        check(copy != charID && copy.hasPrefix("u-"), "kept both: new character id")
        let copyVoice = b.character(copy)?.versions["en"]?.voice ?? ""
        check(copyVoice != voiceID && b.voice(copyVoice) != nil, "kept both: new voice id, reference rewritten")
        try? fm.removeItem(at: folder)

        // And "replace": the ids stay, nothing is added.
        let count = (b.characters.count, b.voices.count)
        folder = try roundTrip(entries)
        let third = try b.importHaru(folder) { _ in false }
        check(third.characters == [charID] && third.voices.contains(voiceID), "replaced in place")
        check(b.characters.count == count.0 && b.voices.count == count.1, "replace adds nothing")
        try? fm.removeItem(at: folder)

        // Haru: imported with its id and all its voices; again with "replace", nothing is added.
        folder = try roundTrip(try a.exportCharacter("haru"))
        let added = try b.importHaru(folder) { _ in false }
        check(added.characters == ["haru"] && added.voices.count == 9, "haru imported with its 9 voices")
        try? fm.removeItem(at: folder)
        let before = (b.characters.count, b.voices.count)
        folder = try roundTrip(try a.exportCharacter("haru"))
        _ = try b.importHaru(folder) { _ in false }
        check(b.characters.count == before.0 && b.voices.count == before.1, "haru replaced in place")
        try? fm.removeItem(at: folder)

        // One voice.
        folder = try roundTrip(try a.exportVoice(voiceID))
        check(fm.fileExists(atPath: folder.appendingPathComponent("voices/\(voiceID).wav").path), "voice export has its audio")
        try? fm.removeItem(at: folder)

        // A .haru made by hand with the contract's layout, compressed with DEFLATE (zip).
        let hand = fm.temporaryDirectory.appendingPathComponent("hand-\(UUID().uuidString)")
        try fm.createDirectory(at: hand.appendingPathComponent("characters"), withIntermediateDirectories: true)
        try fm.createDirectory(at: hand.appendingPathComponent("voices"), withIntermediateDirectories: true)
        try Data(#"{"format": 1, "kind": "haru-kit", "characters": ["web-1"], "voices": ["u-aaaaaaaaaaaa"]}"#.utf8)
            .write(to: hand.appendingPathComponent("haru.json"))
        try Data(#"{"format": 1, "id": "web-1", "versions": {"fr": {"title": "Ami", "summary": "", "voice": "u-aaaaaaaaaaaa", "card": {"persona": "Un ami.", "name": "Léa", "gender": "female", "backchannel": "", "other_name": "", "relationship": "", "situation": ""}}}}"#.utf8)
            .write(to: hand.appendingPathComponent("characters/web-1.character.json"))
        try Data(#"{"format": 1, "id": "u-aaaaaaaaaaaa", "name": "Web voice", "language": "fr", "gender": "female", "seconds": 4, "text": "", "file": "u-aaaaaaaaaaaa.wav"}"#.utf8)
            .write(to: hand.appendingPathComponent("voices/u-aaaaaaaaaaaa.voice.json"))
        try fm.copyItem(at: wav, to: hand.appendingPathComponent("voices/u-aaaaaaaaaaaa.wav"))
        let zipped = fm.temporaryDirectory.appendingPathComponent("hand-\(UUID().uuidString).haru")
        let zip = Process()
        zip.executableURL = URL(fileURLWithPath: "/usr/bin/zip")
        zip.currentDirectoryURL = hand
        zip.arguments = ["-q", "-r", "-9", zipped.path, "."]
        try zip.run()
        zip.waitUntilExit()
        folder = try unzip(zipped)
        let web = try b.importHaru(folder) { _ in true }
        check(web.characters == ["web-1"] && b.character("web-1")?.versions["fr"]?.card.name == "Léa", "hand-made .haru imported")
        check(b.voice("u-aaaaaaaaaaaa")?.name == "Web voice", "hand-made voice imported")
        try? fm.removeItem(at: folder)
        try? fm.removeItem(at: hand)
        try? fm.removeItem(at: zipped)

        // Refused files.
        let newer = fm.temporaryDirectory.appendingPathComponent("newer-\(UUID().uuidString)")
        try fm.createDirectory(at: newer, withIntermediateDirectories: true)
        try Data(#"{"format": 2, "kind": "haru-kit", "characters": [], "voices": []}"#.utf8).write(to: newer.appendingPathComponent("haru.json"))
        do { _ = try b.importHaru(newer) { _ in true }; check(false, "newer format refused") } catch HaruFileError.newerFormat {} catch {
            check(false, "newer format: \(error)")
        }
        try Data(#"{"format": 1}"#.utf8).write(to: newer.appendingPathComponent("haru.json"))
        do { _ = try b.importHaru(newer) { _ in true }; check(false, "not haru refused") } catch HaruFileError.notHaru {} catch {
            check(false, "not haru: \(error)")
        }
        try? fm.removeItem(at: newer)

        // Delete: every character goes with its picture, every voice goes with its audio.
        try b.deleteCharacter(charID)
        check(b.character(charID) == nil && !fm.fileExists(atPath: rootB.appendingPathComponent("images/\(charID).png").path),
              "deleted with its picture")
        try b.deleteCharacter("haru")
        check(b.character("haru") == nil && !fm.fileExists(atPath: rootB.appendingPathComponent("images/haru.png").path),
              "haru deleted")
        let parker = b.voice("en-parker")
        try b.deleteVoice("en-parker")
        check(b.voice("en-parker") == nil && parker.map { !fm.fileExists(atPath: $0.audioURL.path) } == true,
              "voice of haru deleted")
        try b.deleteVoice(voiceID)
        check(b.voice(voiceID) == nil, "voice deleted")
        try optionalFields()
        finish("LibraryTests")
    }

    /// Author, version and updated are kept through save, duplicate, export and import;
    /// A save by the user sets updated; files without them stay valid.
    static func optionalFields() throws {
        let rootA = try makeRoot(), rootB = try makeRoot()
        defer { try? fm.removeItem(at: rootA); try? fm.removeItem(at: rootB) }
        let a = LibraryStore(root: rootA), b = LibraryStore(root: rootB)

        check(a.character("haru")?.author == "haru-kit", "haru reads")
        let old = LibraryCharacter(json: ["id": "u-000000000001", "versions": ["en": ["title": "Old"]]])
        check(old?.author == "" && old?.version == "" && old?.updated == "", "a file without the fields is valid")
        check(old.map { $0.json["author"] == nil && $0.json["version"] == nil && $0.json["updated"] == nil } == true,
              "absent fields are not written")

        let wav = try storedWav(rootA.appendingPathComponent("v.wav"))
        let voiceID = a.newID()
        var voice = LibraryVoice(id: voiceID, name: "V", language: "en", gender: "", seconds: 3, file: "\(voiceID).wav")
        voice.author = "Ana"
        voice.updated = "2025-01-02"
        try a.save(voice, audio: wav)
        check(a.voice(voiceID)?.author == "Ana" && a.voice(voiceID)?.updated == "2025-01-02", "voice fields saved")

        let id = a.newID()
        var character = LibraryCharacter(id: id, versions: ["en": CharacterVersion(title: "T", voice: voiceID)])
        character.author = "Ana"
        character.version = "1.2"
        character.updated = "2025-01-02"
        try a.save(character)
        let saved = readJSONObject(rootA.appendingPathComponent("characters/\(id).character.json"))
        check(saved?["author"] as? String == "Ana" && saved?["version"] as? String == "1.2"
              && saved?["updated"] as? String == "2025-01-02", "character fields written")

        // A save by the user: updated is today; author and version stay.
        try a.saveEdit(a.character(id)!, today: "2026-11-15")
        let edited = a.character(id)
        check(edited?.updated == "2026-11-15" && edited?.author == "Ana" && edited?.version == "1.2",
              "save sets updated, keeps author and version")
        check(todayISO().range(of: #"^\d{4}-\d{2}-\d{2}$"#, options: .regularExpression) != nil, "today as YYYY-MM-DD")

        // Path traversal IDs and filenames are rejected on parse.
        check(LibraryCharacter(json: ["id": "../evil", "versions": ["en": ["title": "X"]]]) == nil, "traversal character id rejected")
        check(LibraryCharacter(json: ["id": "ok", "image": "../evil.png", "versions": ["en": ["title": "X"]]]) == nil, "traversal image rejected")
        check(LibraryVoice(json: ["id": "../evil", "name": "V"]) == nil, "traversal voice id rejected")
        check(LibraryVoice(json: ["id": "ok", "name": "V", "file": "../secret.wav"]) == nil, "traversal voice file rejected")

        // Duplicate: updated today, author kept, version dropped.
        let copyID = a.newID()
        try a.save(edited!.copied(id: copyID, today: "2026-11-16"))
        let copy = a.character(copyID)
        check(copy?.author == "Ana" && copy?.version == "" && copy?.updated == "2026-11-16",
              "duplicate: author kept, version dropped, updated today")

        // Export and import: unchanged.
        let folder = try roundTrip(try a.exportCharacter(id))
        defer { try? fm.removeItem(at: folder) }
        _ = try b.importHaru(folder) { _ in true }
        let imported = b.character(id)
        check(imported?.author == "Ana" && imported?.version == "1.2" && imported?.updated == "2026-11-15",
              "character fields survive export and import")
        check(b.voice(voiceID)?.author == "Ana" && b.voice(voiceID)?.updated == "2025-01-02",
              "voice fields survive export and import")
    }
}
