// The shared character registry ("Browse characters"): a static site with
// Index.json and one .haru pack for each character (the haru-characters repo).
//
//   <registry>/index.json   {"format": 1, "entries": [{"id", "author", "version", "updated", "languages",
//                            "titles", "summaries", "image", "haru", "size", "sha256"}]}
//
// `image` and `haru` are relative to index.json.

import CryptoKit
import Foundation

let defaultRegistry = "https://yungalgo.github.io/haru-characters/"

private let maxPackBytes = 25 * 1024 * 1024

private func allowedRegistryURL(_ url: URL, baseScheme: String? = nil) -> Bool {
    guard let scheme = url.scheme?.lowercased() else { return false }
    if scheme == "https" { return true }
    return scheme == "http" && (baseScheme == nil || baseScheme == "http")
}

/// The registry's address: `override` (the environment variable HARU_KIT_REGISTRY)
/// Wins over `stored` (UserDefaults `registryURL`, which haru-kit writes); else
/// The default. An empty string: no registry (nil), and no Browse button.
func registryBase(override: String?, stored: String?) -> URL? {
    let text = (override ?? stored ?? defaultRegistry).trimmingCharacters(in: .whitespacesAndNewlines)
    guard !text.isEmpty, let url = URL(string: text.hasSuffix("/") ? text : text + "/"),
          allowedRegistryURL(url) else { return nil }
    return url
}

/// The registry of this launch: HARU_KIT_REGISTRY, else the `registryURL` setting, else the default.
func launchRegistry() -> URL? {
    registryBase(override: ProcessInfo.processInfo.environment["HARU_KIT_REGISTRY"],
                 stored: UserDefaults.standard.string(forKey: "registryURL"))
}

/// One shared character in the registry's list.
struct RegistryEntry: Identifiable, Equatable {
    var id: String
    var author = "", version = "", updated = ""
    var languages: [String] = []
    var titles: [String: String] = [:], summaries: [String: String] = [:]
    var image: URL?
    var haru: URL
    var size: Int
    var sha256: String

    init?(json d: [String: Any], index: URL) {
        let baseScheme = index.scheme?.lowercased()
        guard let id = d["id"] as? String, isValidLibraryID(id), let haru = d["haru"] as? String,
              let packURL = URL(string: haru, relativeTo: index)?.absoluteURL,
              allowedRegistryURL(packURL, baseScheme: baseScheme),
              let size = (d["size"] as? NSNumber)?.intValue, size > 0, size <= maxPackBytes,
              let rawHash = d["sha256"] as? String else { return nil }
        let sha256 = rawHash.lowercased()
        guard sha256.range(of: #"^[0-9a-f]{64}$"#, options: .regularExpression) != nil else { return nil }
        self.id = id
        self.haru = packURL
        self.size = size
        self.sha256 = sha256
        author = d["author"] as? String ?? ""
        version = d["version"] as? String ?? ""
        updated = d["updated"] as? String ?? ""
        languages = d["languages"] as? [String] ?? []
        titles = d["titles"] as? [String: String] ?? [:]
        summaries = d["summaries"] as? [String: String] ?? [:]
        if let image = d["image"] as? String, !image.isEmpty,
           let imgURL = URL(string: image, relativeTo: index)?.absoluteURL,
           allowedRegistryURL(imgURL, baseScheme: baseScheme) {
            self.image = imgURL
        }
    }

    /// The language to show: `language` when the entry has it, else its first language.
    func shownLanguage(_ language: String) -> String {
        titles[language] != nil ? language : languages.first { titles[$0] != nil } ?? titles.keys.sorted().first ?? language
    }

    func title(_ language: String) -> String { titles[shownLanguage(language)] ?? id }
    func summary(_ language: String) -> String { summaries[shownLanguage(language)] ?? "" }
}

enum RegistryError: LocalizedError {
    case unreachable

    var errorDescription: String? { t("registry.unreachable") }
}

/// What the list offers for an entry: a character with its id is in the
/// Library (same version) or older (another version), or it is not there.
enum RegistryState { case add, added, update }

func entryState(_ entry: RegistryEntry, characters: [LibraryCharacter]) -> RegistryState {
    guard let own = characters.first(where: { $0.id == entry.id }) else { return .add }
    return own.version == entry.version ? .added : .update
}

/// The entries of an index.json. A newer format: HaruFileError.newerFormat; not an index: unreachable.
func parseRegistry(_ data: Data, index: URL) throws -> [RegistryEntry] {
    guard let json = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any],
          let entries = json["entries"] as? [[String: Any]] else { throw RegistryError.unreachable }
    if ((json["format"] as? NSNumber)?.intValue ?? 1) > libraryFormat { throw HaruFileError.newerFormat }
    return entries.compactMap { RegistryEntry(json: $0, index: index) }
}

/// A file of the registry, never from the cache. No answer, or not 200: unreachable.
private func registryDownload(_ url: URL) async throws -> Data {
    guard allowedRegistryURL(url, baseScheme: url.scheme?.lowercased()) else { throw RegistryError.unreachable }
    let request = URLRequest(url: url, cachePolicy: .reloadIgnoringLocalCacheData, timeoutInterval: 30)
    let data: Data, response: URLResponse
    do { (data, response) = try await URLSession.shared.data(for: request) } catch { throw RegistryError.unreachable }
    guard let http = response as? HTTPURLResponse, http.statusCode == 200 else { throw RegistryError.unreachable }
    return data
}

/// The registry's list of characters.
func fetchRegistry(_ base: URL) async throws -> [RegistryEntry] {
    let index = base.appendingPathComponent("index.json")
    return try parseRegistry(try await registryDownload(index), index: index)
}

func sha256Hex(_ data: Data) -> String {
    SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined()
}

/// The pack has the size and the SHA-256 that the list says.
func packMatches(_ data: Data, _ entry: RegistryEntry) -> Bool {
    data.count == entry.size && sha256Hex(data) == entry.sha256
}

/// The entry's .haru pack, checked against the list (else file.notHaru).
func downloadPack(_ entry: RegistryEntry) async throws -> Data {
    let data = try await registryDownload(entry.haru)
    guard packMatches(data, entry) else { throw HaruFileError.notHaru }
    return data
}

extension LibraryStore {
    /// Adds a checked pack to the user's library with the .haru import, without
    /// A question: the entry's character and its voices replace items with
    /// The same ids; the voices are stored as WAV. The pack must have the entry's character.
    func addRegistryPack(_ data: Data, entry: RegistryEntry) throws -> ImportResult {
        let file = FileManager.default.temporaryDirectory.appendingPathComponent("haru-registry-\(UUID().uuidString).haru")
        try data.write(to: file)
        defer { try? FileManager.default.removeItem(at: file) }
        let folder = try unzip(file)
        defer { try? FileManager.default.removeItem(at: folder) }
        guard let index = readJSONObject(folder.appendingPathComponent("haru.json")),
              (index["characters"] as? [String]) == [entry.id] else { throw HaruFileError.notHaru }
        return try importHaru(folder) { _ in false }
    }
}
