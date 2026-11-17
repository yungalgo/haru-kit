// The character and voice library, in the format that the web app shares:
// <id>.character.json, <id>.voice.json next to its audio file, and pictures in
// Images/. Every item is the user's: it can be changed and deleted.

import Foundation

/// The 7 upstream card fields. Never add fields to a card.
struct CardFields: Equatable {
    var persona = "", name = "", gender = "", backchannel = ""
    var otherName = "", relationship = "", situation = ""

    init() {}

    init(_ d: [String: Any]) {
        persona = d["persona"] as? String ?? ""
        name = d["name"] as? String ?? ""
        gender = d["gender"] as? String ?? ""
        backchannel = d["backchannel"] as? String ?? ""
        otherName = d["other_name"] as? String ?? ""
        relationship = d["relationship"] as? String ?? ""
        situation = d["situation"] as? String ?? ""
    }

    var json: [String: Any] { strings }

    var strings: [String: String] {
        ["persona": persona, "name": name, "gender": gender, "backchannel": backchannel,
         "other_name": otherName, "relationship": relationship, "situation": situation]
    }
}

/// One language version of a character: what the library shows, its voice and its card.
struct CharacterVersion: Equatable {
    var title = "", summary = "", voice = ""
    var card = CardFields()

    init(title: String = "", summary: String = "", voice: String = "", card: CardFields = CardFields()) {
        self.title = title
        self.summary = summary
        self.voice = voice
        self.card = card
    }

    init(_ d: [String: Any]) {
        title = d["title"] as? String ?? ""
        summary = d["summary"] as? String ?? ""
        let rawVoice = d["voice"] as? String ?? ""
        voice = isValidLibraryID(rawVoice) ? rawVoice : ""
        card = CardFields(d["card"] as? [String: Any] ?? [:])
    }

    var json: [String: Any] { ["title": title, "summary": summary, "voice": voice, "card": card.json] }
}

struct LibraryCharacter: Identifiable, Equatable {
    var id: String
    /// The picture's file name in images/ ("" for none).
    var image = ""
    var versions: [String: CharacterVersion] = [:]
    /// Optional ("" when absent): who made it, its version ("1.0"), and the
    /// Date of the last save (YYYY-MM-DD).
    var author = "", version = "", updated = ""

    init(id: String, image: String = "", versions: [String: CharacterVersion] = [:]) {
        self.id = id
        self.image = image
        self.versions = versions
    }

    init?(json d: [String: Any]) {
        let rawImage = d["image"] as? String ?? ""
        guard let id = d["id"] as? String, isValidLibraryID(id),
              rawImage.isEmpty || isSafeLibraryFileName(rawImage),
              let versions = d["versions"] as? [String: Any] else { return nil }
        self.id = id
        image = rawImage
        author = d["author"] as? String ?? ""
        version = d["version"] as? String ?? ""
        updated = d["updated"] as? String ?? ""
        self.versions = versions.compactMapValues { ($0 as? [String: Any]).map(CharacterVersion.init) }
    }

    var json: [String: Any] {
        var d: [String: Any] = ["format": libraryFormat, "id": id, "versions": versions.mapValues(\.json)]
        if !image.isEmpty { d["image"] = image }
        if !author.isEmpty { d["author"] = author }
        if !version.isEmpty { d["version"] = version }
        if !updated.isEmpty { d["updated"] = updated }
        return d
    }

    /// The language codes it has, in `order` (codes not in `order` last).
    func languages(_ order: [String]) -> [String] {
        versions.keys.sorted { (order.firstIndex(of: $0) ?? 99, $0) < (order.firstIndex(of: $1) ?? 99, $1) }
    }

    /// The version in `language`, else the first one.
    func shown(_ language: String, order: [String]) -> CharacterVersion? {
        versions[language] ?? languages(order).first.flatMap { versions[$0] }
    }

    /// A copy under `id`, made `today`: it keeps the author and drops the
    /// Version (it is not that release any more).
    func copied(id: String, today: String) -> LibraryCharacter {
        var copy = self
        copy.id = id
        copy.version = ""
        copy.updated = today
        return copy
    }
}

struct LibraryVoice: Identifiable, Equatable {
    var id: String
    var name = "", language = "", gender = ""
    var seconds = 0.0
    var text = "", source = "", license = ""
    /// Optional ("" when absent): who made it, and the date it was saved (YYYY-MM-DD).
    var author = "", updated = ""
    /// The audio file's name in voices/.
    var file: String
    /// The audio file (set by the store).
    var audioURL = URL(fileURLWithPath: "/")

    init(id: String, name: String, language: String, gender: String, seconds: Double,
         text: String = "", file: String) {
        self.id = id
        self.name = name
        self.language = language
        self.gender = gender
        self.seconds = seconds
        self.text = text
        self.file = file
    }

    init?(json d: [String: Any]) {
        guard let id = d["id"] as? String, isValidLibraryID(id) else { return nil }
        let rawFile = d["file"] as? String ?? "\(id).flac"
        guard isSafeLibraryFileName(rawFile) else { return nil }
        self.id = id
        name = d["name"] as? String ?? id
        language = d["language"] as? String ?? ""
        gender = d["gender"] as? String ?? ""
        seconds = (d["seconds"] as? NSNumber)?.doubleValue ?? 0
        text = d["text"] as? String ?? ""
        source = d["source"] as? String ?? ""
        license = d["license"] as? String ?? ""
        author = d["author"] as? String ?? ""
        updated = d["updated"] as? String ?? ""
        file = rawFile
    }

    var json: [String: Any] {
        var d: [String: Any] = ["format": libraryFormat, "id": id, "name": name, "language": language,
                                "gender": gender, "seconds": seconds, "text": text]
        if !source.isEmpty { d["source"] = source }
        if !license.isEmpty { d["license"] = license }
        if !author.isEmpty { d["author"] = author }
        if !updated.isEmpty { d["updated"] = updated }
        if file != "\(id).flac" { d["file"] = file }
        return d
    }
}

/// The library format this app reads and writes.
let libraryFormat = 1

func isValidLibraryID(_ s: String) -> Bool {
    s.range(of: #"^[a-z0-9][a-z0-9_-]{0,63}$"#, options: .regularExpression) != nil
}

func isSafeLibraryFileName(_ s: String) -> Bool {
    !s.isEmpty && s != "." && s != ".." && !s.contains("/") && !s.contains("\\") && !s.contains("\0")
}

func isContained(_ child: URL, in parent: URL) -> Bool {
    let base = parent.resolvingSymlinksInPath().standardizedFileURL.path
    let prefix = base.hasSuffix("/") ? base : base + "/"
    let std = child.standardizedFileURL
    let resolvedDir = std.deletingLastPathComponent().resolvingSymlinksInPath().standardizedFileURL
    let resolvedChild = resolvedDir.appendingPathComponent(std.lastPathComponent).standardizedFileURL.path
    return resolvedChild.hasPrefix(prefix)
}

/// Today's local date as YYYY-MM-DD, for `updated`.
func todayISO(_ date: Date = Date()) -> String {
    let c = Calendar(identifier: .gregorian).dateComponents([.year, .month, .day], from: date)
    return String(format: "%04d-%02d-%02d", c.year ?? 0, c.month ?? 0, c.day ?? 0)
}

/// A new id for the user's items: "u-" and 12 lowercase hex characters, not in `taken`.
func newLibraryID(taken: Set<String>) -> String {
    while true {
        let id = "u-" + (0..<12).map { _ in String(Int.random(in: 0..<16), radix: 16) }.joined()
        if !taken.contains(id) { return id }
    }
}

func readJSONObject(_ url: URL) -> [String: Any]? {
    guard let data = try? Data(contentsOf: url) else { return nil }
    return (try? JSONSerialization.jsonObject(with: data)) as? [String: Any]
}

func jsonData(_ object: [String: Any]) throws -> Data {
    try JSONSerialization.data(withJSONObject: object, options: [.prettyPrinted, .sortedKeys, .withoutEscapingSlashes])
}

/// The user's library, read from disk: characters/, voices/ and images/ in one root.
final class LibraryStore {
    let root: URL
    private(set) var characters: [LibraryCharacter] = []
    private(set) var voices: [LibraryVoice] = []

    /// ~/Library/Application Support/haru-kit/library
    static let defaultRoot = FileManager.default.homeDirectoryForCurrentUser
        .appendingPathComponent("Library/Application Support/haru-kit/library")

    init(root: URL = LibraryStore.defaultRoot) {
        self.root = root
        reload()
    }

    func reload() {
        characters = read("characters", ".character.json") { LibraryCharacter(json: $1) }
        voices = read("voices", ".voice.json") { url, d in
            LibraryVoice(json: d).map { var v = $0; v.audioURL = url.deletingLastPathComponent().appendingPathComponent(v.file); return v }
        }
    }

    /// The items of one folder, by creation time.
    private func read<T>(_ folder: String, _ suffix: String, _ make: (URL, [String: Any]) -> T?) -> [T] {
        let dir = root.appendingPathComponent(folder)
        let urls = (try? FileManager.default.contentsOfDirectory(
            at: dir, includingPropertiesForKeys: [.creationDateKey])) ?? []
        func created(_ url: URL) -> Date {
            (try? url.resourceValues(forKeys: [.creationDateKey]).creationDate) ?? .distantPast
        }
        return urls.filter { $0.lastPathComponent.hasSuffix(suffix) }
            .sorted { (created($0), $0.lastPathComponent) < (created($1), $1.lastPathComponent) }
            .compactMap { url in readJSONObject(url).flatMap { make(url, $0) } }
    }

    func character(_ id: String) -> LibraryCharacter? { characters.first { $0.id == id } }
    func voice(_ id: String) -> LibraryVoice? { voices.first { $0.id == id } }
    var ids: Set<String> { Set(characters.map(\.id) + voices.map(\.id)) }
    func newID() -> String { newLibraryID(taken: ids) }

    func imageURL(_ character: LibraryCharacter) -> URL? {
        guard isSafeLibraryFileName(character.image) else { return nil }
        let imagesDir = root.appendingPathComponent("images")
        let url = imagesDir.appendingPathComponent(character.image)
        return isContained(url, in: imagesDir) ? url : nil
    }

    private func folder(_ name: String) throws -> URL {
        let dir = root.appendingPathComponent(name)
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        return dir
    }

    /// Writes a character after the user changed it: `updated` becomes `today`.
    func saveEdit(_ character: LibraryCharacter, today: String = todayISO()) throws {
        var character = character
        character.updated = today
        try save(character)
    }

    func save(_ character: LibraryCharacter) throws {
        guard isValidLibraryID(character.id) else { throw HaruFileError.notHaru }
        let dir = try folder("characters")
        let target = dir.appendingPathComponent("\(character.id).character.json")
        guard isContained(target, in: dir) else { throw HaruFileError.notHaru }
        try jsonData(character.json).write(to: target, options: .atomic)
        reload()
    }

    /// Copies a picture into images/ as `<id>.<ext>` and returns its name.
    func savePicture(_ source: URL, for id: String) throws -> String {
        let ext = source.pathExtension.lowercased()
        guard isValidLibraryID(id), !ext.isEmpty, isSafeLibraryFileName(ext) else { throw HaruFileError.notHaru }
        let name = "\(id).\(ext)"
        let dir = try folder("images")
        let target = dir.appendingPathComponent(name)
        guard isContained(target, in: dir) else { throw HaruFileError.notHaru }
        guard source.standardizedFileURL != target.standardizedFileURL else { return name }
        try? FileManager.default.removeItem(at: target)
        try FileManager.default.copyItem(at: source, to: target)
        return name
    }

    func removePicture(_ name: String) {
        guard isSafeLibraryFileName(name) else { return }
        let dir = root.appendingPathComponent("images")
        let target = dir.appendingPathComponent(name)
        guard isContained(target, in: dir) else { return }
        try? FileManager.default.removeItem(at: target)
    }

    func deleteCharacter(_ id: String) throws {
        guard isValidLibraryID(id), let character = character(id) else { return }
        let dir = root.appendingPathComponent("characters")
        let target = dir.appendingPathComponent("\(id).character.json")
        guard isContained(target, in: dir) else { return }
        try FileManager.default.removeItem(at: target)
        if !characters.contains(where: { $0.id != id && $0.image == character.image }) {
            removePicture(character.image)
        }
        reload()
    }

    /// Writes a voice with a copy of its audio file.
    func save(_ voice: LibraryVoice, audio: URL) throws {
        guard isValidLibraryID(voice.id), isSafeLibraryFileName(voice.file) else { throw HaruFileError.notHaru }
        let dir = try folder("voices")
        let target = dir.appendingPathComponent(voice.file)
        let metaTarget = dir.appendingPathComponent("\(voice.id).voice.json")
        guard isContained(target, in: dir), isContained(metaTarget, in: dir) else { throw HaruFileError.notHaru }
        if audio.standardizedFileURL != target.standardizedFileURL {
            try? FileManager.default.removeItem(at: target)
            try FileManager.default.copyItem(at: audio, to: target)
        }
        try jsonData(voice.json).write(to: metaTarget, options: .atomic)
        reload()
    }

    func deleteVoice(_ id: String) throws {
        guard isValidLibraryID(id), let voice = voice(id) else { return }
        let dir = root.appendingPathComponent("voices")
        let metaTarget = dir.appendingPathComponent("\(id).voice.json")
        let audioTarget = dir.appendingPathComponent(voice.file)
        guard isContained(metaTarget, in: dir), isContained(audioTarget, in: dir) else { return }
        try FileManager.default.removeItem(at: metaTarget)
        try? FileManager.default.removeItem(at: audioTarget)
        reload()
    }
}
