// .haru files: a ZIP with haru.json, characters/<id>.character.json, images/<name>,
// Voices/<id>.voice.json and voices/<audio file>. The Mac app reads and writes the same files.
import { readZip, writeZip, ZipEntry } from "./zip";
import { roundSeconds, toStoredAudio } from "./audio";
import {
  audioFileName, Character, characterFile, characterImage, CharacterFile, FORMAT, newId, parseCharacter,
  parseVoice, putCharacter, putImage, putVoice, SAFE_ID, Voice, voiceAudio, voiceFile, VoiceFile,
} from "./library";

const KIND = "haru-kit";

export class HaruFileError extends Error {
  constructor(readonly code: "notHaru" | "newerFormat" | "unreadableAudio", message: string) {
    super(message);
  }
}

const encoder = new TextEncoder();
const json = (value: unknown) => encoder.encode(JSON.stringify(value, null, 2) + "\n");
const bytes = async (blob: Blob) => new Uint8Array(await blob.arrayBuffer());

// A safe file name from a title (the user's system adds nothing).
export const fileName = (title: string) => (title.replace(/[\\/:*?"<>|\u0000-\u001f]+/g, " ").trim() || "haru") + ".haru";

const voiceEntries = async (voice: Voice): Promise<ZipEntry[]> => [
  { name: `voices/${voice.id}.voice.json`, data: json(voiceFile(voice)) },
  { name: `voices/${audioFileName(voice)}`, data: await bytes(await voiceAudio(voice)) },
];

// The character with all its versions, its picture, and every voice that its versions use.
export const exportCharacter = async (character: Character, voices: Voice[]): Promise<Blob> => {
  const used = new Set(Object.values(character.versions).map(v => v.voice));
  const included = voices.filter(v => used.has(v.id));
  const entries: ZipEntry[] = [{
    name: "haru.json",
    data: json({ format: FORMAT, kind: KIND, characters: [character.id], voices: included.map(v => v.id) }),
  }, { name: `characters/${character.id}.character.json`, data: json(characterFile(character)) }];
  const image = await characterImage(character);
  if (image) {
    entries.push({ name: `images/${character.image}`, data: await bytes(image) });
  }
  for (const voice of included) {
    entries.push(...await voiceEntries(voice));
  }
  return writeZip(entries);
};

export const exportVoice = async (voice: Voice): Promise<Blob> => writeZip([
  { name: "haru.json", data: json({ format: FORMAT, kind: KIND, characters: [], voices: [voice.id] }) },
  ...await voiceEntries(voice),
]);

const IMAGE_TYPES: Record<string, string> = { png: "image/png", jpg: "image/jpeg", jpeg: "image/jpeg", webp: "image/webp" };
const extension = (name: string) => name.includes(".") ? name.slice(name.lastIndexOf(".") + 1).toLowerCase() : "";

// What to do with an item whose id is already in the user's library (null: stop the import).
export type AskReplace = (name: string) => Promise<"replace" | "keepBoth" | null>;

export type Imported = { characters: CharacterFile[]; voices: VoiceFile[] };

// Reads a .haru file into the user's library. Ids of items that the user keeps both of get a new id;
// The characters' voice references follow.
export const importHaru = async (
  file: Blob,
  ownIds: Set<string>,
  ask: AskReplace,
  expectedCharacterId?: string,
): Promise<Imported | null> => {
  let entries: ZipEntry[];
  try {
    entries = await readZip(await file.arrayBuffer());
  } catch (error) {
    throw new HaruFileError("notHaru", String(error));
  }
  const files = new Map(entries.map(e => [e.name, e.data]));
  const text = (name: string) => {
    const data = files.get(name);
    if (!data) throw new HaruFileError("notHaru", `missing ${name}`);
    try {
      return JSON.parse(new TextDecoder().decode(data));
    } catch {
      throw new HaruFileError("notHaru", `invalid JSON in ${name}`);
    }
  };
  let index: { format?: number; kind?: string; characters?: unknown; voices?: unknown };
  try {
    index = text("haru.json");
  } catch {
    throw new HaruFileError("notHaru", "missing haru.json");
  }
  if (index.kind !== KIND) {
    throw new HaruFileError("notHaru", "not a haru-kit file");
  }
  if ((index.format ?? 0) > FORMAT) {
    throw new HaruFileError("newerFormat", `format ${index.format}`);
  }
  const voiceList = Array.isArray(index.voices) ? index.voices : [];
  const charList = Array.isArray(index.characters) ? index.characters : [];
  if (expectedCharacterId && (charList.length !== 1 || charList[0] !== expectedCharacterId)) {
    throw new HaruFileError("notHaru", `expected character ${expectedCharacterId}`);
  }

  // Read and check everything before anything is stored.
  const voices = voiceList.map(rawId => {
    const id = typeof rawId === "string" ? rawId : "";
    if (!SAFE_ID.test(id)) throw new HaruFileError("notHaru", `invalid voice id`);
    const voice = parseVoice(text(`voices/${id}.voice.json`));
    if (!voice || voice.id !== id) throw new HaruFileError("notHaru", `voice ${id}`);
    if (voice.format > FORMAT) throw new HaruFileError("newerFormat", `voice ${id}`);
    const audio = files.get(`voices/${audioFileName(voice)}`);
    if (!audio) throw new HaruFileError("notHaru", `missing audio of ${id}`);
    return { voice, audio };
  });
  const characters = charList.map(rawId => {
    const id = typeof rawId === "string" ? rawId : "";
    if (!SAFE_ID.test(id)) throw new HaruFileError("notHaru", `invalid character id`);
    const character = parseCharacter(text(`characters/${id}.character.json`));
    if (!character || character.id !== id) throw new HaruFileError("notHaru", `character ${id}`);
    if (character.format > FORMAT) throw new HaruFileError("newerFormat", `character ${id}`);
    return { character, image: character.image ? files.get(`images/${character.image}`) ?? null : null };
  });
  if (voices.length === 0 && characters.length === 0) {
    throw new HaruFileError("notHaru", "empty .haru archive");
  }

  // The id of each item in the user's library.
  const finalId = async (id: string, name: string): Promise<string | null> => {
    if (!ownIds.has(id)) return id;
    const answer = await ask(name);
    return answer === null ? null : answer === "replace" ? id : newId();
  };
  // Every voice is stored as 24 kHz mono 16-bit WAV, after the silence trim.
  const converted = await Promise.all(voices.map(async ({ voice, audio }) => {
    try {
      return await toStoredAudio(new Blob([audio]));
    } catch (error) {
      throw new HaruFileError("unreadableAudio", `audio of ${voice.id}: ${error}`);
    }
  }));
  const voiceIds = new Map<string, string>();
  for (const { voice } of voices) {
    const id = await finalId(voice.id, voice.name);
    if (id === null) return null;
    voiceIds.set(voice.id, id);
  }
  const characterIds = new Map<string, string>();
  for (const { character } of characters) {
    const shown = Object.values(character.versions)[0];
    const id = await finalId(character.id, shown?.title || character.id);
    if (id === null) return null;
    characterIds.set(character.id, id);
  }

  const result: Imported = { characters: [], voices: [] };
  for (const [i, { voice }] of voices.entries()) {
    const id = voiceIds.get(voice.id)!;
    const stored: VoiceFile = { ...voice, id, file: `${id}.wav`, seconds: roundSeconds(converted[i].seconds) };
    await putVoice(stored, converted[i].wav);
    result.voices.push(stored);
  }
  for (const { character, image } of characters) {
    const id = characterIds.get(character.id)!;
    const versions = Object.fromEntries(Object.entries(character.versions).map(
      ([lang, v]) => [lang, { ...v, voice: voiceIds.get(v.voice) ?? v.voice }]));
    let imageName = "";
    if (image) {
      const ext = extension(character.image) || "png";
      imageName = `${id}.${ext}`;
      await putImage(imageName, new Blob([image], { type: IMAGE_TYPES[ext] ?? "application/octet-stream" }));
    }
    const stored: CharacterFile = { ...characterFile(character), id, image: imageName, versions };
    await putCharacter(stored);
    result.characters.push(stored);
  }
  return result;
};
