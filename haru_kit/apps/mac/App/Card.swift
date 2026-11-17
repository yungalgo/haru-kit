// The character card text, rendered exactly as upstream `render_card` (and the web app's
// RenderCard): lead sentence and field headers of the card's language template.

import Foundation

/// The 7 upstream card fields, in the order of the rendered card.
let cardFieldKeys = ["persona", "name", "gender", "backchannel", "other_name", "relationship", "situation"]

func renderCard(_ card: [String: String], _ template: [String: Any]) -> String {
    var lines = [template["lead"] as? String ?? ""]
    let headers = template["headers"] as? [String: String] ?? [:]
    for key in cardFieldKeys {
        var text = (card[key] ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
        if key == "gender" || key == "backchannel" {
            text = (template[key] as? [String: String])?[text] ?? ""
        }
        if !text.isEmpty { lines += [headers[key] ?? "", text] }
    }
    return lines.joined(separator: "\n")
}
