// Settings kept between launches (UserDefaults): the character and its draft,
// Guidance, margin, devices and precision.
// The language (of the app and of the model) is kept by UILanguage (Strings.swift).

import Foundation

enum Stored {
    static let defaults = UserDefaults.standard

    static func double(_ key: String, _ fallback: Double) -> Double {
        defaults.object(forKey: key) as? Double ?? fallback
    }

    static func bool(_ key: String, _ fallback: Bool) -> Bool {
        defaults.object(forKey: key) as? Bool ?? fallback
    }

    static func string(_ key: String) -> String { defaults.string(forKey: key) ?? "" }

    static func set(_ key: String, _ value: Any) { defaults.set(value, forKey: key) }

    /// The editor's draft, with the character and the language it belongs to
    /// (`writing`: a new language version, not saved yet).
    static func saveDraft(_ draft: Draft, character: String, language: String, writing: Bool) {
        var json = draft.version.json
        json["image"] = draft.image
        json["author"] = draft.author
        json["character"] = character
        json["language"] = language
        json["writing"] = writing
        if let data = try? JSONSerialization.data(withJSONObject: json) { defaults.set(data, forKey: "draft") }
    }

    /// The stored draft; `author` is nil in a draft stored before the author field existed.
    static func draft() -> (draft: Draft, author: String?, character: String, language: String, writing: Bool)? {
        guard let data = defaults.data(forKey: "draft"),
              let json = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any],
              let character = json["character"] as? String, let language = json["language"] as? String else { return nil }
        let author = json["author"] as? String
        return (Draft(CharacterVersion(json), image: json["image"] as? String ?? "", author: author ?? ""), author,
                character, language, json["writing"] as? Bool ?? false)
    }
}
