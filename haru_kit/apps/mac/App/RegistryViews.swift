// The Browse sheet: the shared characters of the registry, each with Add to my
// Library, Update, or In your library.

import SwiftUI

struct RegistrySheet: View {
    @EnvironmentObject var model: AppModel

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack {
                Text(t("registry.title")).font(.system(size: 18, weight: .semibold))
                Spacer()
                Button(t("action.close")) { model.browsing = false }
                    .buttonStyle(HaruButtonStyle())
                    .keyboardShortcut(.cancelAction)
            }
            Text(t("registry.note")).font(.system(size: 12)).foregroundStyle(Theme.muted)
                .fixedSize(horizontal: false, vertical: true)
            if let error = model.registryError {
                Label(error, systemImage: "exclamationmark.triangle.fill")
                    .font(.system(size: 12)).foregroundStyle(Theme.warning)
                    .fixedSize(horizontal: false, vertical: true)
            }
            switch model.registryList {
            case .loading:
                HStack(spacing: 8) {
                    ProgressView().controlSize(.small)
                    Text(t("registry.loading")).font(.system(size: 13)).foregroundStyle(Theme.muted)
                }
            case .failed(let message):
                HStack(spacing: 10) {
                    Label(message, systemImage: "exclamationmark.triangle.fill")
                        .font(.system(size: 13)).foregroundStyle(Theme.warning)
                        .fixedSize(horizontal: false, vertical: true)
                    Button(t("action.retry")) { model.loadRegistry() }
                        .buttonStyle(HaruButtonStyle(accent: true))
                }
            case .loaded(let entries) where entries.isEmpty:
                Text(t("registry.empty")).font(.system(size: 13)).foregroundStyle(Theme.muted)
            case .loaded(let entries):
                ScrollView {
                    VStack(spacing: 10) {
                        ForEach(entries) { RegistryRow(entry: $0) }
                    }
                }
            }
            Spacer(minLength: 0)
        }
        .padding(24)
        .frame(width: 640, height: 560)
        .background(Theme.card)
        .foregroundStyle(Theme.ink)
        .tint(Theme.accent)
    }
}

/// One shared character: picture, title and summary in the model's language (else
/// Its first language), languages, author, version, date, and its button.
struct RegistryRow: View {
    @EnvironmentObject var model: AppModel
    let entry: RegistryEntry

    var body: some View {
        let title = entry.title(model.language)
        HStack(alignment: .top, spacing: 12) {
            AsyncImage(url: entry.image) { phase in
                if let image = phase.image {
                    image.resizable().scaledToFill().clipShape(RoundedRectangle(cornerRadius: 10, style: .continuous))
                } else {
                    Picture(url: nil, title: title)
                }
            }
            .frame(width: 56, height: 56)
            VStack(alignment: .leading, spacing: 3) {
                Text(title).font(.system(size: 14, weight: .semibold)).lineLimit(1)
                Text(entry.summary(model.language)).font(.system(size: 12)).foregroundStyle(Theme.muted)
                    .lineLimit(3).fixedSize(horizontal: false, vertical: true)
                HStack(spacing: 6) {
                    Text(entry.languages.joined(separator: " ")).font(.system(size: 10, design: .monospaced))
                    if !entry.author.isEmpty { Text(t("registry.by", ["author": entry.author])) }
                    if let release = releaseLine(version: entry.version, updated: entry.updated) { Text(release) }
                }
                .font(.system(size: 10)).foregroundStyle(Theme.muted).lineLimit(1)
            }
            Spacer(minLength: 8)
            action
        }
        .padding(12)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(RoundedRectangle(cornerRadius: 12, style: .continuous).fill(Theme.paper))
    }

    @ViewBuilder private var action: some View {
        if model.registryAdding == entry.id {
            ProgressView().controlSize(.small).frame(height: 28)
        } else {
            switch model.registryState(entry) {
            case .added:
                Button(t("registry.added")) {}.buttonStyle(HaruButtonStyle()).disabled(true)
            case .update:
                Button(t("registry.update")) { model.addFromRegistry(entry) }
                    .buttonStyle(HaruButtonStyle(accent: true)).disabled(model.registryAdding != nil)
            case .add:
                Button(t("registry.add")) { model.addFromRegistry(entry) }
                    .buttonStyle(HaruButtonStyle(accent: true)).disabled(model.registryAdding != nil)
            }
        }
    }
}
