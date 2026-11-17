// Modified from higurashi-ai/Haru (Apache-2.0) for haru-kit.
// UI strings of the app in the nine languages of the model. One language, chosen in the
// Globe menu at the top of the window, is the language of the app's text and the language
// That the model speaks (AppModel.language). The strings are shared with the
// Web app: haru_kit/apps/strings/<lang>.json, generated into Strings.gen.swift.

import Foundation

enum UILanguage {
    static let supported = ["en", "ja", "zh", "ko", "es", "fr", "de", "pt", "ru"]
    /// Each language by its own name (the globe menu).
    static let names = ["en": "English", "ja": "日本語", "zh": "中文", "ko": "한국어", "es": "Español",
                        "fr": "Français", "de": "Deutsch", "pt": "Português", "ru": "Русский"]
    private static let key = "language"
    /// Before: the globe menu kept the app's language under this key, apart from `key` (the model's language).
    private static let oldKey = "uiLanguage"
    /// The language chosen last time (after an update, the old app language). First start: the first
    /// Preferred system language that is one of the nine, else English.
    static var current: String = {
        let defaults = UserDefaults.standard
        if let old = defaults.string(forKey: oldKey) {
            defaults.set(old, forKey: key)
            defaults.removeObject(forKey: oldKey)
        }
        let stored = defaults.string(forKey: key) ?? ""
        if supported.contains(stored) { return stored }
        return Locale.preferredLanguages.lazy.map { String($0.prefix(2)).lowercased() }.first(where: supported.contains) ?? "en"
    }() {
        didSet { UserDefaults.standard.set(current, forKey: key) }
    }
}

private let placeholderPattern = try! NSRegularExpression(pattern: #"\{(\w+)\}"#)

/// The string of `key` in the current UI language, with `{name}` replaced by `params[name]` in a single pass.
func t(_ key: String, _ params: [String: Any] = [:]) -> String {
    let text = uiStrings[UILanguage.current]?[key] ?? uiStrings["en"]?[key] ?? key
    guard !params.isEmpty else { return text }
    let ns = text as NSString
    let matches = placeholderPattern.matches(in: text, range: NSRange(location: 0, length: ns.length))
    guard !matches.isEmpty else { return text }
    var out = ""
    var cursor = 0
    for match in matches {
        out += ns.substring(with: NSRange(location: cursor, length: match.range.location - cursor))
        let name = ns.substring(with: match.range(at: 1))
        out += params[name].map { "\($0)" } ?? ns.substring(with: match.range)
        cursor = match.range.location + match.range.length
    }
    out += ns.substring(from: cursor)
    return out
}
