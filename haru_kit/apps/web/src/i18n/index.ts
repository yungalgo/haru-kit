// UI strings in the nine languages of the model. One language, chosen in the globe menu at the
// Top of the page and kept in this browser, is the language of the app's text and the language
// That Haru speaks (the card, the prompt headings, the default voice, the library). The strings
// Are shared with the Mac app: haru_kit/apps/strings/<lang>.json, generated into strings.gen.ts.
import { useSyncExternalStore } from "react";
import { en, Key, tables } from "./strings.gen";

export type { Key };
type Params = Record<string, string | number>;
export type Translate = (key: Key, params?: Params) => string;

const LANGUAGE_KEY = "languageV1";
// Before: the globe menu kept the app's language here and the model's language in the second key.
const OLD_UI_LANGUAGE_KEY = "uiLanguageV1";
const OLD_MODEL_LANGUAGE_KEY = "cardLanguageV1";

// The nine languages, each by its own name (the globe menu).
export const LANGUAGES: [string, string][] = [
  ["en", "English"], ["ja", "日本語"], ["zh", "中文"], ["ko", "한국어"], ["es", "Español"],
  ["fr", "Français"], ["de", "Deutsch"], ["pt", "Português"], ["ru", "Русский"],
];

export const isKey = (key: string): key is Key => key in en;

// One translator per language, so `t` keeps its identity until the language changes.
const translators = new Map<string, Translate>();
const translator = (lang: string): Translate => {
  let t = translators.get(lang);
  if (!t) {
    const table = tables[lang] ?? en;
    t = (key, params) => {
      const text = table[key] ?? en[key];
      return params ? text.replace(/\{(\w+)\}/g, (match, name) => name in params ? String(params[name]) : match) : text;
    };
    translators.set(lang, t);
  }
  return t;
};

// The language kept in this browser: the old app-language key (moved to the one key), else the one key.
const loadLanguage = (): string | null => {
  try {
    const old = localStorage.getItem(OLD_UI_LANGUAGE_KEY);
    if (old !== null) {
      localStorage.setItem(LANGUAGE_KEY, old);
    }
    localStorage.removeItem(OLD_UI_LANGUAGE_KEY);
    localStorage.removeItem(OLD_MODEL_LANGUAGE_KEY);
    return localStorage.getItem(LANGUAGE_KEY);
  } catch {
    return null;
  }
};

const remembered = loadLanguage();
// First start: the first browser language that is one of the nine, else English.
let current = remembered && tables[remembered] ? remembered :
  (navigator.languages ?? [navigator.language]).map(l => l.slice(0, 2).toLowerCase()).find(code => code in tables) ?? "en";
document.documentElement.lang = current;
const listeners = new Set<() => void>();

// Switch every mounted component to `lang` (the app's text and the language that Haru speaks)
// And keep it for the next visit.
export const setLanguage = (lang: string) => {
  current = lang;
  document.documentElement.lang = lang;
  try {
    localStorage.setItem(LANGUAGE_KEY, lang);
  } catch {
    // Storage may be unavailable (private mode); the page works without it.
  }
  listeners.forEach(listener => listener());
};

const subscribe = (listener: () => void) => {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
};

export const useLanguage = (): string => useSyncExternalStore(subscribe, () => current);

export const useT = (): Translate => translator(useLanguage());

// Message of a failed API response: its `code` translated, else the server's own text.
export const errorMessage = (t: Translate, body: string, fallback: Key): string => {
  try {
    const { code, error, ...params } = JSON.parse(body);
    const key = `error.${code}`;
    return isKey(key) ? t(key, params) : error || t(fallback);
  } catch {
    return body || t(fallback);
  }
};
