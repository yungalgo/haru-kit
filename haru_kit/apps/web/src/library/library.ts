// The character and voice library: the user's items in IndexedDB (database "haru-kit", stores
// "characters", "voices", "images"). Same file format as the Mac app's library folders.
import { DialogueCard, parseCard } from "../pages/Queue/card";

export const FORMAT = 1;

export type Version = { title: string; summary: string; voice: string; card: DialogueCard };

export type CharacterFile = {
  format: number;
  id: string;
  image: string;
  versions: Record<string, Version>;
  // Optional: who made it, its version ("1.0"), the date of the last save (YYYY-MM-DD).
  author?: string;
  version?: string;
  updated?: string;
};

export type VoiceFile = {
  format: number;
  id: string;
  name: string;
  language: string;
  gender: string;
  seconds: number;
  text: string;
  source?: string;
  license?: string;
  // Name of the audio file when it is not `<id>.flac`.
  file?: string;
  author?: string;
  updated?: string;
};

export type Character = CharacterFile & { imageUrl: string };
export type Voice = VoiceFile & { audioUrl: string };

// A stored voice: the voice file and its audio.
type StoredVoice = VoiceFile & { audio: Blob };

export const newId = () => {
  const bytes = crypto.getRandomValues(new Uint8Array(6));
  return "u-" + Array.from(bytes, b => b.toString(16).padStart(2, "0")).join("");
};

export const audioFileName = (voice: VoiceFile) => voice.file || `${voice.id}.flac`;

const str = (value: unknown) => typeof value === "string" ? value : "";
export const SAFE_ID = /^[a-z0-9][a-z0-9_-]{0,63}$/;
const SAFE_LANG = /^[a-z]{2,8}(-[a-zA-Z0-9]{2,8})?$/;
export const isSafeFileName = (name: string) =>
  !!name && name !== "." && name !== ".." && !name.includes("/") && !name.includes("\\") && !name.includes("\0");

export const parseVersion = (value: unknown): Version | null => {
  if (!value || typeof value !== "object") {
    return null;
  }
  const v = value as Record<string, unknown>;
  const card = parseCard(v.card);
  const voice = str(v.voice);
  if (!card || (voice && !SAFE_ID.test(voice))) {
    return null;
  }
  return { title: str(v.title), summary: str(v.summary), voice, card };
};

export const parseCharacter = (value: unknown): CharacterFile | null => {
  if (!value || typeof value !== "object") {
    return null;
  }
  const c = value as Record<string, unknown>;
  const id = str(c.id);
  const image = str(c.image);
  if (!SAFE_ID.test(id) || (image && !isSafeFileName(image))) {
    return null;
  }
  const versions: Record<string, Version> = {};
  for (const [lang, raw] of Object.entries((c.versions ?? {}) as Record<string, unknown>)) {
    if (!SAFE_LANG.test(lang)) continue;
    const version = parseVersion(raw);
    if (version) {
      versions[lang] = version;
    }
  }
  const character: CharacterFile = { format: Number(c.format) || FORMAT, id, image, versions };
  for (const key of CHARACTER_META) {
    if (str(c[key])) {
      character[key] = str(c[key]);
    }
  }
  return character;
};

export const parseVoice = (value: unknown): VoiceFile | null => {
  if (!value || typeof value !== "object") {
    return null;
  }
  const v = value as Record<string, unknown>;
  const id = str(v.id);
  const file = str(v.file);
  if (!SAFE_ID.test(id) || (file && !isSafeFileName(file))) {
    return null;
  }
  const voice: VoiceFile = {
    format: Number(v.format) || FORMAT, id, name: str(v.name), language: str(v.language),
    gender: str(v.gender), seconds: Number(v.seconds) || 0, text: str(v.text),
  };
  for (const key of ["source", "license", "file", "author", "updated"] as const) {
    if (str(v[key])) {
      voice[key] = str(v[key]);
    }
  }
  return voice;
};

const CHARACTER_META = ["author", "version", "updated"] as const;

// The file content of a character (no app fields; the optional fields only when set).
export const characterFile = (c: CharacterFile): CharacterFile => {
  const file: CharacterFile = { format: FORMAT, id: c.id, image: c.image, versions: c.versions };
  for (const key of CHARACTER_META) {
    if (c[key]) file[key] = c[key];
  }
  return file;
};

export const voiceFile = (v: VoiceFile): VoiceFile => {
  const { format, id, name, language, gender, seconds, text, source, license, file, author, updated } = v;
  return JSON.parse(JSON.stringify({ format, id, name, language, gender, seconds, text, source, license, file, author, updated }));
};

// Today's date in the local time zone, as YYYY-MM-DD (the `updated` field).
export const today = () => {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
};

// ---------------------------------------------------------------- IndexedDB

const STORES = ["characters", "voices", "images"] as const;
type Store = typeof STORES[number];

let database: Promise<IDBDatabase> | null = null;
const db = () => {
  database ??= new Promise((resolve, reject) => {
    const request = indexedDB.open("haru-kit", 1);
    request.onupgradeneeded = () => {
      const d = request.result;
      if (!d.objectStoreNames.contains("characters")) d.createObjectStore("characters", { keyPath: "id" });
      if (!d.objectStoreNames.contains("voices")) d.createObjectStore("voices", { keyPath: "id" });
      if (!d.objectStoreNames.contains("images")) d.createObjectStore("images");
    };
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });
  return database;
};

const run = async <T>(store: Store, mode: IDBTransactionMode, action: (s: IDBObjectStore) => IDBRequest<T>): Promise<T> => {
  const transaction = (await db()).transaction(store, mode);
  const request = action(transaction.objectStore(store));
  return new Promise((resolve, reject) => {
    transaction.oncomplete = () => resolve(request.result);
    transaction.onerror = () => reject(transaction.error);
    transaction.onabort = () => reject(transaction.error);
  });
};

// Object URLs of the stored pictures and voices, one for each name for this page load.
const objectUrls = new Map<string, string>();
const objectUrl = (key: string, blob: Blob) => {
  const old = objectUrls.get(key);
  if (old) URL.revokeObjectURL(old);
  const url = URL.createObjectURL(blob);
  objectUrls.set(key, url);
  return url;
};

export const loadLibrary = async (): Promise<{ characters: Character[]; voices: Voice[] }> => {
  const [characters, voices, imageNames] = await Promise.all([
    run("characters", "readonly", s => s.getAll() as IDBRequest<CharacterFile[]>),
    run("voices", "readonly", s => s.getAll() as IDBRequest<StoredVoice[]>),
    run("images", "readonly", s => s.getAllKeys()),
  ]);
  const images = new Set(imageNames.map(String));
  const imageUrls = new Map<string, string>();
  for (const c of characters) {
    if (c.image && images.has(c.image) && !imageUrls.has(c.image)) {
      const blob = await run("images", "readonly", s => s.get(c.image) as IDBRequest<Blob>);
      imageUrls.set(c.image, objectUrl(`image:${c.image}`, blob));
    }
  }
  return {
    characters: characters.map(c => ({ ...characterFile(c), imageUrl: imageUrls.get(c.image) ?? "" })),
    voices: voices.map(v => ({ ...voiceFile(v), audioUrl: objectUrl(`voice:${v.id}`, v.audio) })),
  };
};

export const putCharacter = (c: CharacterFile) => run("characters", "readwrite", s => s.put(characterFile(c)));
export const deleteCharacter = (id: string) => run("characters", "readwrite", s => s.delete(id));
export const putVoice = (v: VoiceFile, audio: Blob) => run("voices", "readwrite", s => s.put({ ...voiceFile(v), audio }));
export const deleteVoice = (id: string) => run("voices", "readwrite", s => s.delete(id));
export const putImage = (name: string, blob: Blob) => run("images", "readwrite", s => s.put(blob, name));
export const deleteImage = (name: string) => run("images", "readwrite", s => s.delete(name));
export const getImage = (name: string) => run("images", "readonly", s => s.get(name) as IDBRequest<Blob | undefined>);

// The audio of a voice.
export const voiceAudio = async (voice: Voice): Promise<Blob> => {
  const stored = await run("voices", "readonly", s => s.get(voice.id) as IDBRequest<StoredVoice | undefined>);
  if (!stored) {
    throw new Error(`voice ${voice.id}: no audio`);
  }
  return stored.audio;
};

// The picture of a character.
export const characterImage = async (c: Character): Promise<Blob | null> =>
  c.image ? (await getImage(c.image)) ?? null : null;

// The version shown for a language: that language's, else the first one.
export const shownVersion = (c: CharacterFile, lang: string): [string, Version] | null => {
  if (c.versions[lang]) return [lang, c.versions[lang]];
  const first = Object.entries(c.versions)[0];
  return first ?? null;
};
