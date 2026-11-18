// The registry client against a served registry (run.sh: TEST_REGISTRY, default
// Http://localhost:8970/): the address rule, the index, the pack download with
// Its size and SHA-256 check, the import without a question (voices stored as
// WAV), Update by version, and the first start (every entry added to an empty
// Library). TEST_REGISTRY_UPDATE (optional): the same registry with a newer
// Version of a character, for a real update.

import Foundation

@main
struct RegistryTests {
    static let fm = FileManager.default

    static func fails<T>(_ what: String, _ body: () async throws -> T, _ expected: (Error) -> Bool) async {
        do {
            _ = try await body()
            check(false, "\(what): no error")
        } catch {
            check(expected(error), "\(what): \(error)")
        }
    }

    /// Header of a stored voice: PCM, mono, 24 kHz, 16-bit, with its data.
    static func storedWav(_ url: URL) -> Bool {
        guard let data = try? Data(contentsOf: url), data.count > 44 else { return false }
        func u16(_ at: Int) -> Int { Int(data[at]) | Int(data[at + 1]) << 8 }
        func u32(_ at: Int) -> Int { u16(at) | u16(at + 2) << 16 }
        return String(decoding: data[0..<4], as: UTF8.self) == "RIFF" && u16(20) == 1 && u16(22) == 1
            && u32(24) == 24_000 && u16(34) == 16 && u32(40) == data.count - 44
    }

    static func main() async throws {
        // The address: HARU_KIT_REGISTRY, else the registryURL setting, else the default; empty: none.
        check(registryBase(override: nil, stored: nil)?.absoluteString == defaultRegistry, "default")
        check(registryBase(override: nil, stored: "http://a.test/r")?.absoluteString == "http://a.test/r/", "setting, with a slash")
        check(registryBase(override: "http://b.test/", stored: "http://a.test/")?.absoluteString == "http://b.test/", "variable wins")
        check(registryBase(override: nil, stored: "") == nil, "empty setting: no Browse")
        check(registryBase(override: "", stored: "http://a.test/") == nil, "empty variable: no Browse")

        // The index format.
        let index = URL(string: "http://r.test/x/index.json")!
        let sampleUpperHash = "AB" + String(repeating: "0", count: 62)
        let sampleLowerHash = "ab" + String(repeating: "0", count: 62)
        let sample = #"{"format": 1, "entries": [{"id": "a", "haru": "packs/a.haru", "size": 3, "sha256": ""#
            + sampleUpperHash + #"", "image": "", "languages": ["en", "ja"], "titles": {"en": "A", "ja": "エー"}}, "#
            + #"{"id": "broken"}, {"id": "bad-scheme", "haru": "file:///etc/passwd", "size": 3, "sha256": ""#
            + sampleLowerHash + #""}]}"#
        let parsed = try parseRegistry(Data(sample.utf8), index: index)
        check(parsed.map(\.id) == ["a"], "an entry without valid pack metadata is left out")
        check(parsed.first?.haru.absoluteString == "http://r.test/x/packs/a.haru" && parsed.first?.image == nil
              && parsed.first?.sha256 == sampleLowerHash, "relative to index.json; empty image: none")
        check(parsed.first.map { [$0.title("ja"), $0.title("fr"), $0.summary("fr")] } == ["エー", "A", ""],
              "the UI language, else the first language")
        await fails("newer format", { try parseRegistry(Data(#"{"format": 2, "entries": []}"#.utf8), index: index) }) {
            ($0 as? HaruFileError) == .newerFormat
        }
        await fails("not an index", { try parseRegistry(Data("<html>".utf8), index: index) }) { $0 is RegistryError }
        await fails("no server", { try await fetchRegistry(URL(string: "http://127.0.0.1:9/")!) }) {
            $0.localizedDescription == t("registry.unreachable")
        }

        // The served registry.
        let base = URL(string: ProcessInfo.processInfo.environment["TEST_REGISTRY"] ?? "http://localhost:8970/")!
        let entries = try await fetchRegistry(base)
        check(!entries.isEmpty, "the registry lists characters")
        guard let entry = entries.first else { return finish("RegistryTests") }
        check(entry.haru.absoluteString.hasPrefix(base.absoluteString) && !entry.version.isEmpty && !entry.languages.isEmpty,
              "entry \(entry.id): pack \(entry.haru), version \(entry.version), languages \(entry.languages)")
        let pack = try await downloadPack(entry)
        check(pack.count == entry.size && sha256Hex(pack) == entry.sha256, "size and SHA-256 of the pack")
        var bad = entry
        bad.sha256 = String(repeating: "0", count: 64)
        await fails("wrong SHA-256", { try await downloadPack(bad) }) { $0.localizedDescription == t("file.notHaru") }
        bad = entry
        bad.size += 1
        await fails("wrong size", { try await downloadPack(bad) }) { $0.localizedDescription == t("file.notHaru") }

        let root = fm.temporaryDirectory.appendingPathComponent("registry-test-\(UUID().uuidString)")
        try fm.createDirectory(at: root, withIntermediateDirectories: true)
        defer { try? fm.removeItem(at: root) }
        let store = LibraryStore(root: root)
        check(entryState(entry, characters: store.characters) == .add, "not in the library: Add")
        var other = entry
        other.id = "not-in-the-pack"
        await fails("a pack without the entry's character", { try store.addRegistryPack(pack, entry: other) }) {
            ($0 as? HaruFileError) == .notHaru
        }

        // Add: the character with its id, its voices stored as WAV, no question.
        let added = try store.addRegistryPack(pack, entry: entry)
        let character = store.character(entry.id)
        check(added.characters == [entry.id] && character != nil, "added as \(entry.id)")
        check(character.map { !$0.image.isEmpty && store.imageURL($0).map { fm.fileExists(atPath: $0.path) } == true } == true,
              "with its picture")
        check(character?.version == entry.version && Set(character.map { Array($0.versions.keys) } ?? []) == Set(entry.languages),
              "its version and languages")
        let voices = Set(character?.versions.values.map(\.voice) ?? [])
        check(!voices.isEmpty && voices.allSatisfy { store.voice($0) != nil }, "its voices are in the library: \(voices)")
        for id in voices {
            guard let voice = store.voice(id) else { continue }
            check(voice.file == "\(id).wav" && storedWav(voice.audioURL) && voice.seconds >= 3, "\(id): stored WAV, \(voice.seconds) s")
        }
        check(entryState(entry, characters: store.characters) == .added, "same version: In your library")

        // Update: another version replaces the character and its voices (same ids, no copies).
        let own = { store.characters.count }
        let ownVoices = { store.voices.count }
        let counts = (own(), ownVoices())
        guard var older = store.character(entry.id) else {
            check(false, "\(entry.id) in the library")
            return finish("RegistryTests")
        }
        older.version = "0.9"
        try store.save(older)
        check(entryState(entry, characters: store.characters) == .update, "another version: Update")
        _ = try store.addRegistryPack(pack, entry: entry)
        check(store.character(entry.id)?.version == entry.version && (own(), ownVoices()) == counts, "updated in place")
        check(entryState(entry, characters: store.characters) == .added, "after Update: In your library")
        let files = try fm.contentsOfDirectory(atPath: root.appendingPathComponent("voices").path)
        check(files.allSatisfy { $0.hasSuffix(".wav") || $0.hasSuffix(".voice.json") }, "only WAV voices: \(files.sorted())")

        // First start: every entry of the list into an empty library, as the app does.
        let seedRoot = fm.temporaryDirectory.appendingPathComponent("registry-seed-\(UUID().uuidString)")
        defer { try? fm.removeItem(at: seedRoot) }
        let seeded = LibraryStore(root: seedRoot)
        for item in entries where entryState(item, characters: seeded.characters) == .add {
            _ = try seeded.addRegistryPack(try await downloadPack(item), entry: item)
        }
        check(entries.allSatisfy { entryState($0, characters: seeded.characters) == .added },
              "first start: all \(entries.count) characters in the library")
        check(entries.contains { $0.id == "haru" } == (seeded.character("haru") != nil), "haru is there when the list has it")

        // A real newer version from a second registry.
        if let update = ProcessInfo.processInfo.environment["TEST_REGISTRY_UPDATE"].flatMap(URL.init(string:)) {
            let newer = try await fetchRegistry(update).first { $0.id == entry.id }
            if let newer {
                check(newer.version != entry.version, "the second registry has another version")
                check(entryState(newer, characters: store.characters) == .update, "newer version: Update")
                _ = try store.addRegistryPack(try await downloadPack(newer), entry: newer)
                check(store.character(entry.id)?.version == newer.version && (own(), ownVoices()) == counts,
                      "updated to \(newer.version) in place")
                check(entryState(newer, characters: store.characters) == .added, "newer: In your library")
                check(entryState(entry, characters: store.characters) == .update, "the old list's version: Update")
            } else {
                check(false, "\(entry.id) in the second registry")
            }
        }
        finish("RegistryTests")
    }
}
