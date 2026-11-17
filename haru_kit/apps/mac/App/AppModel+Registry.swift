// "Browse characters": the list of shared characters (Registry.swift), and
// Adding one to the user's library.

import Foundation

/// The Browse sheet's list.
enum RegistryList: Equatable {
    case loading
    case failed(String)
    case loaded([RegistryEntry])
}

extension AppModel {
    func browseRegistry() {
        registryError = nil
        browsing = true
        loadRegistry()
    }

    /// Reads the list again (also "Try again").
    func loadRegistry() {
        guard let registry else { return }
        registryList = .loading
        Task {
            let list: RegistryList
            do { list = .loaded(try await fetchRegistry(registry)) } catch { list = .failed(error.localizedDescription) }
            await MainActor.run { self.registryList = list }
        }
    }

    func registryState(_ entry: RegistryEntry) -> RegistryState {
        entryState(entry, characters: characters)
    }

    /// Add or Update: downloads the pack, checks its size and SHA-256, imports
    /// It without the replace question, and selects the character. Unsaved
    /// Changes in the editor are not lost without a question.
    func addFromRegistry(_ entry: RegistryEntry) {
        guard registryAdding == nil, confirmDiscard() else { return }
        registryAdding = entry.id
        registryError = nil
        Task {
            let pack: Result<Data, Error>
            do { pack = .success(try await downloadPack(entry)) } catch { pack = .failure(error) }
            await MainActor.run { self.finishAdding(entry, pack) }
        }
    }

    private func finishAdding(_ entry: RegistryEntry, _ pack: Result<Data, Error>) {
        registryAdding = nil
        do {
            let result = try library.addRegistryPack(try pack.get(), entry: entry)
            reloadLibrary()
            selectCharacter(result.characters.first ?? entry.id)
        } catch {
            reloadLibrary()
            registryError = error.localizedDescription
        }
    }

    /// First start (the stored flag `librarySeeded` is not set): adds every
    /// Character of the registry to the library, with the same download and
    /// Import as Add. Characters that are already there are not added again.
    /// The flag is set only when all are in the library; else the next start
    /// Tries again. Without the registry, the library stays as it is.
    func seedLibrary() {
        guard !Stored.bool("librarySeeded", false), !seeding, let registry else { return }
        seeding = true
        Task {
            var packs: [(RegistryEntry, Data)] = []
            var complete = true
            do {
                let entries = try await fetchRegistry(registry)
                for entry in await MainActor.run(body: { entries.filter { self.registryState($0) == .add } }) {
                    do { packs.append((entry, try await downloadPack(entry))) } catch { complete = false }
                }
            } catch {
                complete = false
            }
            await MainActor.run { [packs, complete] in self.finishSeeding(packs, complete: complete) }
        }
    }

    private func finishSeeding(_ packs: [(RegistryEntry, Data)], complete: Bool) {
        var complete = complete
        for (entry, data) in packs {
            do { _ = try library.addRegistryPack(data, entry: entry) } catch {
                fileLog("[app] first start: \(entry.id) not added: \(error.localizedDescription)")
                complete = false
            }
        }
        seeding = false
        if complete { Stored.set("librarySeeded", true) }
        guard !packs.isEmpty else { return }
        // A new character that is not saved yet stays in the list.
        let kept = library.character(selectedID) == nil ? selectedCharacter : nil
        reloadLibrary()
        if let kept { characters.append(kept) }
        if selectedCharacter == nil { selectCharacter(defaultCharacterID) }
    }
}
