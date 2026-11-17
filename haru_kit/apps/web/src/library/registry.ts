// The shared character registry: a static site with index.json and one .haru pack per character.
// The browser reads it directly (GitHub Pages sends CORS headers).
import { HaruFileError } from "./haruFile";
import { FORMAT, SAFE_ID } from "./library";

export type RegistryEntry = {
  id: string;
  author: string;
  version: string;
  updated: string;
  languages: string[];
  titles: Record<string, string>;
  summaries: Record<string, string>;
  // Absolute URLs ("" = no picture).
  image: string;
  haru: string;
  size: number;
  sha256: string;
};

// The registry cannot be reached (network, HTTP error, or no valid list).
export class RegistryUnreachable extends Error {}

const str = (value: unknown) => typeof value === "string" ? value : "";
const texts = (value: unknown): Record<string, string> => Object.fromEntries(
  Object.entries(value && typeof value === "object" ? value : {}).filter(([, v]) => typeof v === "string"));

const get = async (url: string): Promise<Response> => {
  let response: Response;
  try {
    response = await fetch(url, { cache: "no-store" });
  } catch (error) {
    throw new RegistryUnreachable(`${url}: ${error}`);
  }
  if (!response.ok) {
    throw new RegistryUnreachable(`${url}: HTTP ${response.status}`);
  }
  return response;
};

const MAX_PACK_BYTES = 25 * 1024 * 1024;
const HEX_SHA256 = /^[0-9a-f]{64}$/;

const resolveHttpUrl = (raw: string, base: URL): string | null => {
  try {
    const url = new URL(raw, base);
    if (url.protocol !== "https:" && !(url.protocol === "http:" && base.protocol === "http:")) {
      return null;
    }
    return url.href;
  } catch {
    return null;
  }
};

// The entries of the registry at `base` (the folder of index.json).
export const fetchRegistry = async (base: string): Promise<RegistryEntry[]> => {
  let indexBase: URL;
  try {
    indexBase = new URL("index.json", base.endsWith("/") ? base : `${base}/`);
    if (indexBase.protocol !== "https:" && indexBase.protocol !== "http:") {
      throw new RegistryUnreachable(`unsupported registry URL: ${base}`);
    }
  } catch (error) {
    throw error instanceof RegistryUnreachable ? error : new RegistryUnreachable(`${base}: ${error}`);
  }
  const indexUrl = indexBase.href;
  let index: { format?: number; entries?: unknown[] };
  try {
    index = await (await get(indexUrl)).json();
  } catch (error) {
    throw error instanceof RegistryUnreachable ? error : new RegistryUnreachable(`${indexUrl}: ${error}`);
  }
  if ((index.format ?? 0) > FORMAT) {
    throw new HaruFileError("newerFormat", `registry format ${index.format}`);
  }
  return (index.entries ?? []).flatMap(raw => {
    const e = (raw && typeof raw === "object" ? raw : {}) as Record<string, unknown>;
    const id = str(e.id);
    const haru = str(e.haru) ? resolveHttpUrl(str(e.haru), indexBase) : null;
    const size = Number(e.size);
    const sha256 = str(e.sha256).toLowerCase();
    if (!SAFE_ID.test(id) || !haru || !Number.isInteger(size) || size <= 0 || size > MAX_PACK_BYTES || !HEX_SHA256.test(sha256)) {
      return [];
    }
    const image = str(e.image) ? (resolveHttpUrl(str(e.image), indexBase) ?? "") : "";
    return [{
      id, author: str(e.author), version: str(e.version), updated: str(e.updated),
      languages: Array.isArray(e.languages) ? e.languages.filter(l => typeof l === "string") : [],
      titles: texts(e.titles), summaries: texts(e.summaries),
      image, haru, size, sha256,
    }];
  });
};

// The .haru pack of `entry`, checked against its size and SHA-256.
export const downloadPack = async (entry: RegistryEntry): Promise<Blob> => {
  if (!Number.isInteger(entry.size) || entry.size <= 0 || entry.size > MAX_PACK_BYTES || !HEX_SHA256.test(entry.sha256)) {
    throw new HaruFileError("notHaru", `${entry.haru}: invalid pack metadata`);
  }
  let pack: Blob;
  try {
    pack = await (await get(entry.haru)).blob();
  } catch (error) {
    throw error instanceof RegistryUnreachable ? error : new RegistryUnreachable(`${entry.haru}: ${error}`);
  }
  if (pack.size !== entry.size) {
    throw new HaruFileError("notHaru", `${entry.haru}: ${pack.size} bytes, expected ${entry.size}`);
  }
  if (!crypto.subtle) {
    throw new HaruFileError("notHaru", "SHA-256 verification requires a secure context (HTTPS or localhost)");
  }
  const digest = new Uint8Array(await crypto.subtle.digest("SHA-256", await pack.arrayBuffer()));
  const hex = Array.from(digest, b => b.toString(16).padStart(2, "0")).join("");
  if (hex !== entry.sha256) {
    throw new HaruFileError("notHaru", `${entry.haru}: SHA-256 ${hex}, expected ${entry.sha256}`);
  }
  return pack;
};
