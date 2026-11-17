import processorUrl from "../../audio-processor.ts?worker&url";
import { PlayoutProfile, setPlayoutProfile } from "../Conversation/playoutProfile";
import { FC, useEffect, useState, useCallback, useRef, MutableRefObject } from "react";
import { CircleCheck, Download, FileUp, LoaderCircle, Plus, Trash2, TriangleAlert } from "lucide-react";
import { Conversation } from "../Conversation/Conversation";
import { loadTurnKey, saveTurnKey, TurnKey } from "../Conversation/turnKey";
import { Button } from "../../components/Button/Button";
import { Masthead } from "../../components/Haru/Masthead";
import { LanguageMenu } from "../../components/Haru/LanguageMenu";
import { Stat, loadLastLatency } from "../Conversation/components/ServerAudio/ServerAudioStats";
import { useModelParams } from "../Conversation/hooks/useModelParams";
import { CardTemplate, renderCard } from "./card";
import { errorMessage, Key, setLanguage, useLanguage, useT } from "../../i18n";
import { Card } from "./ui";
import { VoiceSection, VoiceStatus } from "./VoiceSection";
import { KitInfo, setSink, useDevices, useStored } from "./settings";
import { SettingsSection } from "./SettingsSection";
import { CharacterSection } from "./CharacterSection";
import { CharacterActions, CharacterMeta, HeaderFields } from "./CharacterTools";
import { FIRST_CHARACTER, useCharacters } from "./useCharacters";
import { useDialog } from "./Dialog";
import { forgetReference, referenceFor, useReference } from "./references";
import { Character, deleteVoice, shownVersion, Voice } from "../../library/library";
import {
  AskReplace, exportCharacter as exportHaru, exportVoice as exportHaruVoice, fileName, HaruFileError, importHaru,
} from "../../library/haruFile";
import { downloadPack, fetchRegistry, RegistryEntry, RegistryUnreachable } from "../../library/registry";
import { AddVoice } from "./AddVoice";
import { RegistrySection } from "./RegistrySection";

type ServerConfig = {
  languages: { code: string; label: string }[];
  card_templates: Record<string, CardTemplate>;
  system_prompt_max_chars: number;
  reference_min_seconds: number;
  reference_max_seconds: number;
  time_limit_seconds: number;
  webrtc?: boolean;
  playout?: Partial<PlayoutProfile>;
};

// The message of each kind of bad .haru file.
const HARU_FILE_ERRORS: Record<HaruFileError["code"], Key> = {
  notHaru: "file.notHaru", newerFormat: "file.newerFormat", unreadableAudio: "error.unreadable_audio",
};

function getFloatFromStorage(val: string | null) {
  return (val == null) ? undefined : parseFloat(val)
}

export const Queue: FC = () => {
  const t = useT();
  const [hasMicrophoneAccess, setHasMicrophoneAccess] = useState<boolean>(false);
  const [showMicrophoneAccessMessage, setShowMicrophoneAccessMessage] = useState<boolean>(false);
  const [backendReady, setBackendReady] = useState<boolean>(false);
  const [config, setConfig] = useState<ServerConfig | null>(null);
  // Language of the app and of the conversation: card template, library versions and voices
  // (null until the config loads).
  const oneLanguage = useLanguage();
  const language = config ? oneLanguage : null;
  // Visitor's own Cloudflare TURN key for WebRTC (this browser only), for servers without TURN.
  const [turnKey, setTurnKeyBase] = useState<TurnKey>(() => loadTurnKey() ?? { keyId: "", token: "" });
  const setTurnKey = useCallback((value: TurnKey) => {
    setTurnKeyBase(value);
    saveTurnKey(value.keyId.trim() && value.token.trim() ? value : null);
  }, []);
  const hasTurnKey = !!(turnKey.keyId.trim() && turnKey.token.trim());
  const [isStarting, setIsStarting] = useState(false);
  const [startError, setStartError] = useState<string | null>(null);
  const modelParams = useModelParams({
    cfgScale: getFloatFromStorage(localStorage.getItem("cfgScaleV4")),
  });
  // Audio devices ("" = automatic: the system default, also after a device change).
  const devices = useDevices();
  const [microphone, setMicrophone] = useStored("microphoneV1", "");
  const [output, setOutput] = useStored("outputV1", "");
  const [kit, setKit] = useState<KitInfo | null>(null);
  // The precision being loaded after a switch, until the model is ready.
  const [reloading, setReloading] = useState<string | null>(null);
  const kitRef = useRef<KitInfo | null>(null);
  // The server did not answer the last checks (no endless spinner).
  const [serverLost, setServerLost] = useState(false);
  const [onlyLanguage, setOnlyLanguage] = useStored("libraryOnlyLanguageV1", false);
  const [onlyVoiceLanguage, setOnlyVoiceLanguage] = useStored("voicesOnlyLanguageV1", false);
  const [dialog, ask] = useDialog();
  // The characters of the registry were added to the library once (first start).
  const [seeded, setSeeded] = useStored("librarySeededV1", false);
  const [seeding, setSeeding] = useState(false);
  const seedStarted = useRef(false);

  const library = useCharacters(language, t("library.untitled"));
  // A short note next to the character actions (saved, or what failed).
  const [notice, setNotice] = useState<{ text: string; error?: boolean } | null>(null);
  const { draft } = library;
  const voice = draft ? library.voices.find(v => v.id === draft.version.voice) : undefined;
  const reference = useReference(voice);

  // Latency of the conversation before this page load, if there was one.
  const [lastLatency] = useState(loadLastLatency);

  const audioContext = useRef<AudioContext | null>(null);
  const worklet = useRef<AudioWorkletNode | null>(null);
  useEffect(() => {
    let cancelled = false;
    const loadConfig = async () => {
      try {
        const response = await fetch("/api/config", { cache: "no-store" });
        const data = await response.json() as ServerConfig;
        if (!cancelled) {
          setConfig(data);
          setPlayoutProfile(data.playout);
        }
      } catch (error) {
        console.error("Config fetch failed", error);
        if (!cancelled) {
          window.setTimeout(loadConfig, 2000);
        }
      }
    };
    loadConfig();
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    let cancelled = false;
    let failures = 0;

    const checkBackend = async () => {
      try {
        const response = await fetch("/api/ready", { cache: "no-store" });
        const status = await response.json() as { ready?: boolean };
        const ready = response.ok && status.ready === true;
        // What the server can do; while the model loads, also whether it failed.
        const info = ready && kitRef.current ? kitRef.current : await (await fetch("/api/kit", { cache: "no-store" })).json() as KitInfo;
        if (!cancelled) {
          failures = 0;
          setServerLost(false);
          setBackendReady(ready);
          setKit(info);
          kitRef.current = info;
          if (ready) {
            setReloading(null);
          }
        }
      } catch (error) {
        console.error("Backend readiness check failed", error);
        if (!cancelled) {
          setBackendReady(false);
          failures += 1;
          setServerLost(failures >= 2);
        }
      }
    };

    checkBackend();
    const intervalId = window.setInterval(checkBackend, 2000);
    return () => {
      cancelled = true;
      window.clearInterval(intervalId);
    };
  }, []);

  // A device chosen by hand stays while it exists; when it is removed, go back to automatic.
  // Never during a conversation (the Conversation screen holds its devices).
  const inConversation = !!modelParams.chatSessionId;
  useEffect(() => {
    if (!devices.known || inConversation) {
      return;
    }
    if (microphone && !devices.inputs.some(d => d.deviceId === microphone)) {
      setMicrophone("");
    }
    if (output && !devices.outputs.some(d => d.deviceId === output)) {
      setOutput("");
    }
  }, [devices, microphone, output, inConversation, setMicrophone, setOutput]);

  // BF16 or int8: the server reloads the model (Mac way only).
  const setPrecision = useCallback(async (value: "bf16" | "int8") => {
    setStartError(null);
    try {
      const response = await fetch("/api/kit/precision", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ quantize: value === "int8" ? "8" : "none" }),
      });
      if (!response.ok) {
        throw new Error(errorMessage(t, await response.text(), "error.start"));
      }
      const info = await response.json() as KitInfo;
      kitRef.current = null;
      setKit(info);
      setBackendReady(false);
      setReloading(value === "int8" ? "int8" : "BF16");
    } catch (e) {
      console.error(e);
      setStartError(e instanceof Error && e.message ? e.message : t("error.start"));
    }
  }, [t]);

  // Unsaved changes are never dropped without a question.
  const confirmDiscard = useCallback(async () => {
    if (!library.unsaved || !draft) return true;
    const answer = await ask(t("character.discardConfirm", { name: draft.version.title || t("library.untitled") }), [
      ["keep", t("character.keepEditing")],
      ["discard", t("character.discard"), true],
    ]);
    return answer === "discard";
  }, [library.unsaved, draft, ask, t]);

  const selectCharacter = useCallback(async (character: Character) => {
    if (language && await confirmDiscard()) {
      library.select(character, language);
    }
  }, [language, confirmDiscard, library]);

  const newCharacter = useCallback(async () => {
    if (language && await confirmDiscard()) {
      library.create(language);
    }
  }, [language, confirmDiscard, library]);

  // Runs a library change; tells the user when it is done or failed.
  const run = useCallback(async (action: () => Promise<unknown>, done?: string) => {
    setNotice(null);
    try {
      await action();
      if (done) setNotice({ text: done });
    } catch (e) {
      console.error(e);
      setNotice({ text: String(e), error: true });
    }
  }, []);

  // .haru files.
  const download = useCallback((blob: Blob, name: string) => {
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = fileName(name);
    link.click();
    window.setTimeout(() => URL.revokeObjectURL(url), 10000);
  }, []);
  // The saved character. With unsaved changes, the user saves them first (or cancels),
  // So the file equals the library.
  const exportCharacter = useCallback(() => run(async () => {
    if (!draft) return;
    let saved = library.characters.find(c => c.id === draft.base.id);
    if (library.unsaved) {
      if (!draft.image) {
        setNotice({ text: t("character.pictureRequired"), error: true });
        return;
      }
      const answer = await ask(t("file.saveFirst"), [["cancel", t("voices.cancel")], ["save", t("character.save"), true]]);
      if (answer !== "save") return;
      saved = await library.save();
    }
    if (saved) {
      download(await exportHaru(saved, library.voices), shownVersion(saved, draft.lang)?.[1].title || t("library.untitled"));
    }
  }), [run, draft, library, download, ask, t]);
  const exportVoice = useCallback((v: Voice) => run(async () => download(await exportHaruVoice(v), v.name || v.id)),
    [run, download]);

  // Reads a .haru file into the library. Unless `quiet`: selects the imported character (else uses the
  // Imported voice) and says what came in. Throws when the file is bad.
  const importPack = useCallback(async (file: Blob, askReplace: AskReplace, quiet = false, expectedId?: string) => {
    if (!language) return;
    const ids = new Set([...library.characters, ...library.voices].map(item => item.id));
    const imported = await importHaru(file, ids, askReplace, expectedId);
    if (!imported) return;
    const loaded = await library.reload();
    if (quiet) return;
    const first = imported.characters[0] && loaded?.characters.find(c => c.id === imported.characters[0].id);
    if (first) {
      library.select(first, language);
    } else if (imported.voices[0] && draft && !draft.missing) {
      library.editVersion({ voice: imported.voices[0].id });
    }
    const names = [
      ...imported.characters.map(c => shownVersion(c, language)?.[1].title || c.id),
      ...imported.voices.map(v => v.name || v.id),
    ];
    setNotice({ text: t("file.imported", { items: names.join(", ") }) });
  }, [language, library, t, draft]);

  // The message of a failed import, download or registry list.
  const importError = useCallback((e: unknown) => {
    console.error(e);
    return e instanceof RegistryUnreachable ? t("registry.unreachable")
      : e instanceof HaruFileError ? t(HARU_FILE_ERRORS[e.code])
        : t("file.importFailed", { error: String(e) });
  }, [t]);

  const importInput = useRef<HTMLInputElement>(null);
  const importFile = useCallback(async (file: File) => {
    if (!language || !await confirmDiscard()) return;
    setNotice(null);
    try {
      await importPack(file, async name => {
        const answer = await ask(t("file.replaceConfirm", { name }),
          [["cancel", t("voices.cancel")], ["keepBoth", t("file.keepBoth")], ["replace", t("file.replace"), true]]);
        return answer === "replace" || answer === "keepBoth" ? answer : null;
      });
    } catch (e) {
      setNotice({ text: importError(e), error: true });
    }
  }, [language, confirmDiscard, importPack, importError, ask, t]);

  // A shared character: the same id replaces that character and its voices, without a question.
  const addFromRegistry = useCallback(async (entry: RegistryEntry) => {
    if (!language || !await confirmDiscard()) return null;
    setNotice(null);
    try {
      await importPack(await downloadPack(entry), async () => "replace", false, entry.id);
      return null;
    } catch (e) {
      return importError(e);
    }
  }, [language, confirmDiscard, importPack, importError]);

  // At the first start, every character of the registry goes into the library (as Add in Browse does).
  // Only when all are added is this done; else the next start tries again.
  useEffect(() => {
    if (!language || !library.loaded || !kit || seedStarted.current) return;
    seedStarted.current = true;
    const registry = kit.registry;
    if (seeded || !registry) return;
    setSeeding(true);
    (async () => {
      try {
        // Haru first: the first character in the library is the one shown.
        const entries = (await fetchRegistry(registry)).sort((a, b) =>
          Number(b.id === FIRST_CHARACTER) - Number(a.id === FIRST_CHARACTER));
        for (const entry of entries) {
          await importPack(await downloadPack(entry), async () => "replace", true, entry.id);
        }
        setSeeded(true);
      } catch (e) {
        console.error("Adding the shared characters failed", e);
        if (library.characters.length === 0) {
          setNotice({ text: t("registry.unreachable"), error: true });
        }
      } finally {
        setSeeding(false);
      }
    })();
  }, [language, library.loaded, library.characters.length, kit, seeded, setSeeded, importPack, t]);

  const characterName = draft?.version.title || t("library.untitled");
  const deleteCharacter = useCallback(async () => {
    const answer = await ask(t("character.deleteConfirm", { name: characterName }),
      [["cancel", t("voices.cancel")], ["delete", t("character.delete"), true]]);
    if (answer === "delete") await run(library.remove);
  }, [ask, t, characterName, run, library]);

  const deleteTile = useCallback(async (c: Character) => {
    const name = shownVersion(c, language ?? "")?.[1].title || t("library.untitled");
    const answer = await ask(t("character.deleteConfirm", { name }),
      [["cancel", t("voices.cancel")], ["delete", t("character.delete"), true]]);
    if (answer === "delete") await run(() => library.removeCharacter(c));
  }, [ask, t, language, run, library]);

  const deleteVersion = useCallback(async () => {
    const languageName = config?.languages.find(l => l.code === draft?.lang)?.label ?? draft?.lang ?? "";
    const answer = await ask(t("character.deleteVersionConfirm", { language: languageName, name: characterName }),
      [["cancel", t("voices.cancel")], ["delete", t("character.delete"), true]]);
    if (answer === "delete") await run(library.removeVersion);
  }, [ask, t, config, draft, characterName, run, library]);

  const removeVoice = useCallback(async (v: Voice) => {
    const answer = await ask(t("voices.deleteConfirm", { name: v.name }),
      [["cancel", t("voices.cancel")], ["delete", t("voices.delete"), true]]);
    if (answer === "delete") await run(async () => {
      await deleteVoice(v.id);
      await library.reload();
    });
  }, [ask, t, run, library]);

  // Leaving the page with unsaved changes: the browser asks first. Not from the conversation screen:
  // "Back to setup" reloads the page and asks its own question; the draft is kept in this browser.
  useEffect(() => {
    if (!library.unsaved || inConversation) return;
    const onBeforeUnload = (e: BeforeUnloadEvent) => {
      e.preventDefault();
      e.returnValue = "";
    };
    window.addEventListener("beforeunload", onBeforeUnload);
    return () => window.removeEventListener("beforeunload", onBeforeUnload);
  }, [library.unsaved, inConversation]);

  // The globe menu: switch the app, the model and the editor to the selected character's version in `lang`.
  const chooseLanguage = useCallback(async (lang: string) => {
    if (lang === oneLanguage || !await confirmDiscard()) {
      return;
    }
    setLanguage(lang);
    setStartError(null);
    library.changeLanguage(lang);
  }, [oneLanguage, confirmDiscard, library]);

  const getMicrophoneAccess = useCallback(async () => {
    try {
      // Only asks for the permission: the conversation opens its own streams (close this one).
      const stream = await window.navigator.mediaDevices.getUserMedia({ audio: true });
      stream.getTracks().forEach(track => track.stop());
      setHasMicrophoneAccess(true);
      return true;
    } catch (e) {
      console.error(e);
      setShowMicrophoneAccessMessage(true);
      setHasMicrophoneAccess(false);
    }
    return false;
  }, [setHasMicrophoneAccess, setShowMicrophoneAccessMessage]);

  const startProcessor = useCallback(async () => {
    if (!audioContext.current) {
      // The model's own rate, so the decoded Opus needs no resampling.
      audioContext.current = new AudioContext({ sampleRate: 24000 });
      await setSink(audioContext.current, output);
    }
    if (worklet.current) {
      return;
    }
    let ctx = audioContext.current;
    ctx.resume();
    try {
      worklet.current = new AudioWorkletNode(ctx, "haru-processor");
    } catch (err) {
      await ctx.audioWorklet.addModule(processorUrl);
      worklet.current = new AudioWorkletNode(ctx, "haru-processor");
    }
    worklet.current.connect(ctx.destination);
  }, [audioContext, worklet, output]);

  const card = draft?.version.card;
  const template = config && draft ? config.card_templates[draft.lang] : undefined;
  const promptText = card && template ? renderCard(card, template) : "";
  const missingPersona = !card || card.persona.trim() === "";
  const promptTooLong = !!config && promptText.length > config.system_prompt_max_chars;
  // A language version that is missing, or written but not saved yet.
  const versionPending = !!draft && (draft.missing || draft.newVersion);
  const canStart = backendReady && reference?.status === "ready" && !versionPending && !missingPersona && !promptTooLong && !isStarting;

  const onConnect = useCallback(async () => {
    if (!canStart || !voice) {
      return;
    }
    setIsStarting(true);
    setStartError(null);
    try {
      // Unlock audio inside the click handler (autoplay policies), and confirm microphone permission
      // Before asking the server to open a model session.
      await startProcessor();
      if (!(await getMicrophoneAccess())) {
        return;
      }
      // Always use the reference of the voice chosen now (an upload that still runs is awaited).
      const createSession = async () => fetch("/api/session", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          system_prompt: promptText,
          reference_audio_id: (await referenceFor(voice)).id,
          guidance_scale: modelParams.cfgScale,
        }),
      });
      let response = await createSession();
      if (response.status === 404) {
        // The server lost the uploaded voice (it restarted): upload it again, once.
        forgetReference(voice.id);
        response = await createSession();
      }
      if (!response.ok) {
        throw new Error(errorMessage(t, await response.text(), "error.start"));
      }
      const data = await response.json() as { id: string };
      modelParams.setChatSessionId(data.id);
      localStorage.setItem("cfgScaleV4", modelParams.cfgScale.toString());
    } catch (e) {
      console.error(e);
      setStartError(e instanceof Error && e.message ? e.message : t("error.start"));
    } finally {
      setIsStarting(false);
    }
  }, [canStart, voice, promptText, modelParams, startProcessor, getMicrophoneAccess, t]);

  if (hasMicrophoneAccess && audioContext.current && worklet.current && modelParams.chatSessionId) {
    return (
      <Conversation
        audioContext={audioContext as MutableRefObject<AudioContext>}
        worklet={worklet as MutableRefObject<AudioWorkletNode>}
        useWebRtc={!!config?.webrtc || hasTurnKey}
        userName={card?.other_name}
        modelName={card?.name}
        chatSessionId={modelParams.chatSessionId}
        cfgScale={modelParams.cfgScale}
        microphoneId={microphone}
        outputId={output}
        timeLimitSeconds={config?.time_limit_seconds}
      />
    );
  }

  // Why the conversation cannot start yet, if anything.
  // An audio type that only ffmpeg reads, on a server without ffmpeg.
  const uploadBody = reference?.status === "failed" && reference.error instanceof Error ? reference.error.message : "";
  const uploadError = uploadBody.includes("\"unreadable_audio\"") && kit?.ffmpeg === false ? t("error.ffmpegMissing")
    : uploadBody ? errorMessage(t, uploadBody, "error.upload") : t("error.upload");
  // The server could not load (or reload) the model.
  const modelFailed = !backendReady && !!kit?.model_failed;
  let blocker = "";
  if (serverLost) {
    blocker = t("error.connect");
  } else if (modelFailed) {
    blocker = t("error.modelLoad");
  } else if (!backendReady) {
    blocker = reloading ? t("launch.reloading", { precision: reloading }) : t("state.modelLoading");
  } else if (!draft) {
    blocker = seeding ? t("library.adding") : !library.loaded ? t("loading")
      : t("library.empty", { language: config?.languages.find(l => l.code === language)?.label ?? language ?? "" });
  } else if (versionPending) {
    blocker = t("state.versionUnsaved", { language: config?.languages.find(l => l.code === draft.lang)?.label ?? draft.lang });
  } else if (missingPersona) {
    blocker = t("state.personaMissing");
  } else if (config && promptTooLong) {
    blocker = t("error.prompt_too_long", { max_chars: config.system_prompt_max_chars });
  } else if (!voice) {
    blocker = t("state.voiceMissing");
  } else if (reference?.status === "failed") {
    blocker = uploadError;
  } else if (reference?.status !== "ready") {
    blocker = t("voice.processing");
  }
  const busy = isStarting || (seeding && !draft) || (!backendReady && !serverLost && !modelFailed) ||
    (!!voice && reference?.status === "uploading");
  const problem = startError ??
    (showMicrophoneAccessMessage ? `${t("error.microphoneDenied")} ${t("error.microphoneFixWeb")}` : null);
  const languages = config?.languages.map(l => [l.code, l.label] as [string, string]) ?? [];

  return (
    <div className="mx-auto flex w-full max-w-[720px] flex-col gap-5 px-4 py-7 sm:px-8">
      {dialog}
      <div className="relative flex justify-center pb-2 pt-3">
        <Masthead />
        <div className="absolute right-0 top-0">
          <LanguageMenu onChoose={chooseLanguage} disabled={isStarting} />
        </div>
      </div>

      <div className="flex flex-col items-end gap-2 pb-2">
        <div className="flex items-center justify-end gap-3.5">
          {busy ?
            <span className="flex items-center gap-1.5 text-xs text-muted">
              <LoaderCircle className="h-3.5 w-3.5 shrink-0 animate-spin" />{isStarting ? t("state.preparing") : blocker}
            </span>
            : blocker ?
              <span className="flex items-center gap-1.5 text-xs text-warning">
                <TriangleAlert className="h-3.5 w-3.5 shrink-0" />{blocker}
              </span>
              :
              <span className="flex items-center gap-1.5 text-xs text-accent">
                <CircleCheck className="h-3.5 w-3.5 shrink-0" />{t("state.ready")}
              </span>}
          <Button accent large disabled={!canStart} onClick={async () => await onConnect()} className="shrink-0">
            {isStarting ? t("state.connecting") : t("action.start")}
          </Button>
        </div>
        {problem && <p className="flex items-start gap-1.5 whitespace-pre-wrap text-xs text-warning">
          <TriangleAlert className="mt-px h-3.5 w-3.5 shrink-0" />{problem}
        </p>}
      </div>

      {lastLatency && <Card>
        <div className="flex items-end gap-7">
          <Stat big label={t("stats.lastLatency")} value={`${Math.round(lastLatency.p50 * 1000)} ms`} />
          <Stat label="p95" value={`${Math.round(lastLatency.p95 * 1000)} ms`} />
        </div>
      </Card>}

      {config && language ?
        <CharacterSection characters={library.characters} draft={draft} language={language} languages={languages}
          onSelect={selectCharacter} onDelete={deleteTile} onlyLanguage={onlyLanguage} setOnlyLanguage={setOnlyLanguage}
          setField={library.setField} rendered={promptText} maxChars={config.system_prompt_max_chars}
          disabled={isStarting} unsaved={library.unsaved} placeholder={seeding ? t("library.adding") : library.loaded ? "" : t("loading")}
          onWriteVersion={library.writeVersion} onChooseAnother={() => setOnlyLanguage(true)}
          browse={kit?.registry && <RegistrySection url={kit.registry} language={language} library={library.characters}
            disabled={isStarting} onAdd={addFromRegistry} errorText={importError} />}
          extraTiles={<button type="button" disabled={isStarting} onClick={newCharacter}
            className={`flex min-h-[72px] items-center justify-center gap-2 rounded-xl border border-dashed p-3 text-[13px] font-medium disabled:cursor-not-allowed disabled:opacity-60 ${draft?.isNew ? "border-accent text-accent" : "border-line text-muted hover:text-ink"}`}>
            <Plus className="h-4 w-4" />{t("library.new")}
          </button>}
          header={draft && <>
            <CharacterMeta character={draft.base} />
            <HeaderFields version={draft.version} editVersion={library.editVersion} author={draft.author}
              editAuthor={library.editAuthor} imageUrl={library.imageUrl}
              setImage={blob => run(() => library.setImage(blob))} disabled={isStarting} />
          </>}
          actions={draft && <>
            {!draft.missing && <CharacterActions isNew={draft.isNew} unsaved={library.unsaved} hasPicture={!!draft.image}
              otherVersions={Object.keys(draft.base.versions).some(l => l !== draft.lang)}
              languageLabel={languages.find(([c]) => c === draft.lang)?.[1] ?? draft.lang} disabled={isStarting}
              onSave={() => run(library.save, t("character.saved"))}
              onRevert={() => run(library.revert)}
              onDuplicate={() => run(() => library.copy(name => t("character.copyName", { name })))}
              onDelete={deleteCharacter} onDeleteVersion={deleteVersion} />}
            <Button disabled={isStarting || draft.missing} onClick={exportCharacter}>
              <Download className="h-3.5 w-3.5" />{t("file.exportCharacter")}
            </Button>
            <Button disabled={isStarting} onClick={() => importInput.current?.click()}>
              <FileUp className="h-3.5 w-3.5" />{t("file.import")}
            </Button>
            <input ref={importInput} type="file" accept=".haru,application/zip" className="hidden" onChange={e => {
              const file = e.target.files?.[0];
              if (file) importFile(file);
              e.target.value = "";
            }} />
            {notice && <span className={`text-xs ${notice.error ? "text-warning" : "text-accent"}`}>{notice.text}</span>}
          </>} />
        : <Card><p className="text-[13px] text-muted">{t("loading")}</p></Card>}

      {config && language && <VoiceSection voices={library.voices} languages={languages} language={language}
        selected={draft && !draft.missing ? draft.version.voice : ""} textLanguage={draft && !draft.missing ? draft.lang : ""}
        onUse={v => library.editVersion({ voice: v.id })}
        note={t("voice.note", { min: config.reference_min_seconds, max: Math.round(config.reference_max_seconds / 60) })}
        onlyLanguage={onlyVoiceLanguage} setOnlyLanguage={setOnlyVoiceLanguage} output={output} disabled={isStarting}
        status={voice && reference && <VoiceStatus state={reference.status}
          text={reference.status === "uploading" ? t("voice.processing") : reference.status === "failed" ? uploadError
            : t(reference.reference.trimmed ? "voice.inUseTrimmed" : "voice.inUse", {
              name: voice.name, seconds: reference.reference.seconds.toFixed(1), max: config.reference_max_seconds,
            })} />}
        actions={v => <>
          <button type="button" disabled={isStarting} onClick={() => exportVoice(v)}
            aria-label={t("file.exportVoice")} title={t("file.exportVoice")}
            className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full text-muted hover:bg-fill hover:text-ink disabled:opacity-40">
            <Download className="h-3.5 w-3.5" />
          </button>
          <button type="button" disabled={isStarting} onClick={() => removeVoice(v)}
            aria-label={t("voices.delete")} title={t("voices.delete")}
            className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full text-muted hover:bg-fill hover:text-ink disabled:opacity-40">
            <Trash2 className="h-3.5 w-3.5" />
          </button>
        </>}>
        <AddVoice languages={languages} language={language} microphone={microphone} output={output}
          minSeconds={config.reference_min_seconds} maxSeconds={config.reference_max_seconds} disabled={isStarting}
          onSaved={async v => {
            await library.reload();
            if (draft && !draft.missing) library.editVersion({ voice: v.id });
          }} />
      </VoiceSection>}

      <SettingsSection disabled={isStarting} devices={devices}
        microphone={microphone} setMicrophone={setMicrophone} output={output} setOutput={setOutput}
        cfgScale={modelParams.cfgScale} setCfgScale={modelParams.setCfgScale} resetCfgScale={modelParams.resetParams}
        kit={kit} setPrecision={setPrecision} turnKey={turnKey} setTurnKey={setTurnKey} />

      <p className="pb-2 text-center text-xs text-muted">
        {t("footer.limit", { minutes: Math.round((config?.time_limit_seconds ?? 300) / 60) })} <a
          href="https://huggingface.co/higurashi-ai" className="text-accent hover:underline">@higurashi</a>
      </p>
    </div>
  )
};
