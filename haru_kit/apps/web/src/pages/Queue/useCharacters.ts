// The character library and the draft in the editor: the version of the selected character
// In the current language, with the user's changes.
import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Character, CharacterFile, deleteCharacter, deleteImage, FORMAT, getImage, loadLibrary, newId, putCharacter, putImage,
  shownVersion, today, Version, Voice,
} from "../../library/library";
import { DialogueCard, EMPTY_CARD, parseCard } from "./card";

export type Draft = {
  // The character as saved (a new character: as it will be saved).
  base: CharacterFile;
  // A character that is not saved yet.
  isNew: boolean;
  // Language of the version in the editor.
  lang: string;
  version: Version;
  // Picture of the character (stored in IndexedDB as soon as it is chosen). A: required to save.
  image: string;
  // The character has no version in `lang`; `version` is the version shown before.
  missing: boolean;
  // A version that the character does not have yet (written from the version shown before).
  newVersion: boolean;
  // The character's author.
  author: string;
};

const DRAFT_KEY = "setupDraftV1";
// The character shown at the first visit.
export const FIRST_CHARACTER = "haru";

const copyVersion = (v: Version): Version => ({ ...v, card: { ...v.card } });

const sameVersion = (a: Version, b: Version | undefined) => !!b && a.title === b.title && a.summary === b.summary &&
  a.voice === b.voice && (Object.keys(a.card) as (keyof DialogueCard)[]).every(k => a.card[k] === b.card[k]);

const storeDraft = (draft: Draft | null) => {
  try {
    if (draft) localStorage.setItem(DRAFT_KEY, JSON.stringify(draft));
    else localStorage.removeItem(DRAFT_KEY);
  } catch {
    // Storage may be unavailable (private mode); the page works without it.
  }
};

const storedDraft = (): Draft | null => {
  try {
    const draft = JSON.parse(localStorage.getItem(DRAFT_KEY) ?? "null");
    const card = parseCard(draft?.version?.card);
    return draft?.base?.id && card ? {
      isNew: false, image: draft.base.image ?? "", missing: false, newVersion: false, author: draft.base.author ?? "", ...draft,
      version: { ...draft.version, card },
    } : null;
  } catch {
    return null;
  }
};

// The draft of `character` in `lang`. Without a version in `lang`: the "missing" state, with
// `previous` (else the character's first version) as the version to write from.
const draftOf = (character: Character, lang: string, previous?: Version): Draft | null => {
  const version = character.versions[lang] ?? previous ?? shownVersion(character, lang)?.[1];
  return version ? {
    base: character, isNew: false, lang, version: copyVersion(version), image: character.image,
    missing: !character.versions[lang], newVersion: false, author: character.author ?? "",
  } : null;
};

// Picture file extension of a blob.
const imageExtension = (blob: Blob) => ({ "image/jpeg": "jpg", "image/webp": "webp" } as Record<string, string>)[blob.type] ?? "png";

// The cards that the earlier web app kept in this browser, one per language (before the library).
const legacyCardKey = (lang: string) => lang === "ja" ? "dialogueCardV2" : `dialogueCardV2_${lang}`;

// Drop the draft's picture if it was never saved.
const dropUnsavedImage = async (d: Draft) => {
  if (d.image && d.image !== d.base.image) await deleteImage(d.image);
};

export const useCharacters = (language: string | null, untitled: string) => {
  // The library (null until it is read).
  const [lib, setLib] = useState<{ characters: Character[]; voices: Voice[] } | null>(null);
  const [draft, setDraftBase] = useState<Draft | null>(null);
  // URL of the draft's picture.
  const [imageUrl, setImageUrl] = useState("");

  const setDraft = useCallback((next: Draft | null) => {
    setDraftBase(next);
    storeDraft(next);
  }, []);

  const reload = useCallback(async () => {
    try {
      const loaded = await loadLibrary();
      setLib(loaded);
      return loaded;
    } catch (e) {
      console.error("IndexedDB", e);
      return null;
    }
  }, []);

  useEffect(() => {
    const load = async () => {
      const loaded = await reload();
      await migrateLegacyCards(untitled, loaded?.characters.find(c => c.id === FIRST_CHARACTER));
      if (!await reload()) setLib({ characters: [], voices: [] });
    };
    load();
    // Once per page load (the title of a migrated card is in the first UI language).
  }, [reload]);

  const characters = useMemo(() => lib?.characters ?? [], [lib]);
  const voices = useMemo(() => lib?.voices ?? [], [lib]);
  const find = useCallback((id: string) => characters.find(c => c.id === id), [characters]);

  // A voice for a new version in `lang`: Haru's (when in the library), else the first one in that language.
  const voiceFor = useCallback((lang: string) =>
    find(FIRST_CHARACTER)?.versions[lang]?.voice ?? voices.find(v => v.language === lang)?.id ?? "", [find, voices]);

  // First load: the stored draft, else the first character in the current language.
  useEffect(() => {
    if (!lib || !language || draft) return;
    const stored = storedDraft();
    const saved = stored && find(stored.base.id);
    if (stored && (saved || stored.isNew) && stored.lang === language) {
      setDraftBase(saved ? { ...stored, base: saved } : stored);
      return;
    }
    const first = saved || find(FIRST_CHARACTER) || characters[0];
    const next = first && draftOf(first, language);
    if (next) setDraft(next);
  }, [lib, language, draft, characters, find, setDraft]);

  // The draft's picture: the saved one's URL, else the chosen one from IndexedDB.
  const savedImageUrl = draft ? find(draft.base.id)?.imageUrl ?? "" : "";
  useEffect(() => {
    if (!draft?.image) {
      setImageUrl("");
      return;
    }
    if (draft.image === draft.base.image && savedImageUrl) {
      setImageUrl(savedImageUrl);
      return;
    }
    let url = "";
    getImage(draft.image).then(blob => {
      if (blob) {
        url = URL.createObjectURL(blob);
        setImageUrl(url);
      }
    });
    return () => {
      if (url) URL.revokeObjectURL(url);
    };
  }, [draft?.image, draft?.base.image, savedImageUrl]);

  const unsaved = !!draft && !draft.missing && (draft.isNew || draft.newVersion || draft.image !== draft.base.image ||
    draft.author !== (draft.base.author ?? "") || !sameVersion(draft.version, draft.base.versions[draft.lang]));

  const select = useCallback((character: Character, lang: string, previous?: Version) => {
    setDraft(draftOf(character, lang, previous));
  }, [setDraft]);

  // The language changed: the selected character's version in that language (else the
  // "missing" state, from the version shown now).
  const changeLanguage = useCallback((lang: string) => {
    const character = draft && find(draft.base.id);
    if (draft?.isNew) {
      setDraft({ ...draft, lang });
    } else if (character) {
      select(character, lang, draft.missing ? draft.version : character.versions[draft.lang] ?? draft.version);
    }
  }, [draft, find, select, setDraft]);

  // Start the missing version from the fields of the version shown before. The character keeps its
  // Voice (the editor warns when the voice speaks another language).
  const writeVersion = useCallback(() => {
    if (draft?.missing) {
      setDraft({ ...draft, missing: false, newVersion: true, version: copyVersion(draft.version) });
    }
  }, [draft, setDraft]);

  const editVersion = useCallback((change: Partial<Version>) => {
    if (draft) setDraft({ ...draft, version: { ...draft.version, ...change } });
  }, [draft, setDraft]);

  const editAuthor = useCallback((author: string) => {
    if (draft) setDraft({ ...draft, author });
  }, [draft, setDraft]);

  const setField = useCallback((key: keyof DialogueCard, value: string) => {
    if (draft) setDraft({ ...draft, version: { ...draft.version, card: { ...draft.version.card, [key]: value } } });
  }, [draft, setDraft]);

  // A new character: empty card, a version in `lang`, no picture yet.
  const create = useCallback((lang: string) => {
    setDraft({
      base: { format: FORMAT, id: newId(), image: "", versions: {} }, isNew: true, lang, image: "", author: "",
      missing: false, newVersion: false,
      version: { title: untitled, summary: "", voice: voiceFor(lang), card: { ...EMPTY_CARD } },
    });
  }, [setDraft, untitled, voiceFor]);

  const revert = useCallback(async () => {
    if (!draft) return;
    await dropUnsavedImage(draft);
    const character = find(draft.base.id);
    if (character) select(character, draft.lang);
    else create(draft.lang);
  }, [draft, find, select, create]);

  // A new picture replaces the draft's picture.
  const setImage = useCallback(async (blob: Blob) => {
    if (!draft) return;
    await dropUnsavedImage(draft);
    const image = `${draft.base.id}-${Date.now().toString(36)}.${imageExtension(blob)}`;
    await putImage(image, blob);
    setDraft({ ...draft, image });
  }, [draft, setDraft]);

  // Store `character` and show its `lang` version; returns it as stored.
  const store = useCallback(async (character: CharacterFile, lang: string) => {
    await putCharacter(character);
    const loaded = await reload();
    const saved = loaded?.characters.find(c => c.id === character.id);
    if (saved) select(saved, lang);
    return saved;
  }, [reload, select]);

  // Save the version of the current language (with its voice) and the picture (a picture is required).
  const save = useCallback(async () => {
    if (!draft || !draft.image) return;
    const old = draft.base.image;
    const saved = await store({
      ...draft.base, image: draft.image, versions: { ...draft.base.versions, [draft.lang]: draft.version },
      author: draft.author.trim() || undefined, updated: today(),
    }, draft.lang);
    if (old && old !== draft.image) await deleteImage(old);
    return saved;
  }, [draft, store]);

  // A copy in the user's library with all versions (the draft's edits in its version), titled `title(old)`.
  const copy = useCallback(async (title: (old: string) => string) => {
    if (!draft || !draft.image) return;
    const id = newId();
    let image = "";
    const picture = await getImage(draft.image);
    if (picture) {
      image = `${id}.${imageExtension(picture)}`;
      await putImage(image, picture);
    }
    const edited = draft.missing ? {} : { [draft.lang]: draft.version };
    const versions = Object.fromEntries(Object.entries({ ...draft.base.versions, ...edited })
      .map(([lang, v]) => [lang, { ...copyVersion(v), title: title(v.title) }]));
    await dropUnsavedImage(draft);
    // A copy keeps the author, is updated today, and has no version number.
    return await store({ format: FORMAT, id, image, versions, author: draft.author.trim() || undefined, updated: today() }, draft.lang);
  }, [draft, store]);

  const remove = useCallback(async () => {
    if (!draft) return;
    await dropUnsavedImage(draft);
    if (!draft.isNew) {
      await deleteCharacter(draft.base.id);
      if (draft.base.image) await deleteImage(draft.base.image);
    }
    const rest = (await reload())?.characters ?? [];
    const first = rest.find(c => c.id === FIRST_CHARACTER) ?? rest[0];
    setDraft(first ? draftOf(first, draft.lang) : null);
  }, [draft, reload, setDraft]);

  // Delete a character from its tile: the selected one as remove(); another one keeps the draft.
  const removeCharacter = useCallback(async (c: Character) => {
    if (draft?.base.id === c.id) return remove();
    await deleteCharacter(c.id);
    if (c.image) await deleteImage(c.image);
    await reload();
  }, [draft, remove, reload]);

  // Delete the version of the current language (the character keeps its other versions).
  const removeVersion = useCallback(async () => {
    if (!draft || draft.isNew) return;
    const versions = { ...draft.base.versions };
    delete versions[draft.lang];
    await store({ ...draft.base, versions, updated: today() }, draft.lang);
  }, [draft, store]);

  return {
    loaded: !!lib, characters, voices, reload, draft, setDraft, unsaved, imageUrl,
    select, changeLanguage, writeVersion, editVersion, editAuthor, setField, revert, create, setImage, save, copy, remove,
    removeCharacter, removeVersion,
  };
};

// The earlier web app kept one edited card per language in localStorage. Keep the user's work: the
// Cards that differ from Haru's card (when Haru is in the library) become one character (once), with
// Haru's voices; then the old keys are removed.
const migrateLegacyCards = async (title: string, first: Character | undefined) => {
  const versions: Record<string, Version> = {};
  const keys: string[] = [];
  try {
    for (const lang of ["ja", "en", "zh", "ko", "es", "fr", "de", "pt", "ru"]) {
      const raw = localStorage.getItem(legacyCardKey(lang));
      if (raw == null) continue;
      keys.push(legacyCardKey(lang));
      const card = parseCard(JSON.parse(raw));
      const original = first?.versions[lang];
      if (card && card.persona.trim() && !(original && sameVersion({ ...original, card }, original))) {
        versions[lang] = { title: card.name.trim() || title, summary: "", voice: original?.voice ?? "", card };
      }
    }
    if (Object.keys(versions).length > 0) {
      await putCharacter({ format: FORMAT, id: newId(), image: "", versions });
    }
    keys.forEach(key => localStorage.removeItem(key));
  } catch (e) {
    console.error("Keeping the earlier cards failed", e);
  }
};
