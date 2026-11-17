// Voice: the voices of the library; the chosen one is the draft's voice.
import { FC, ReactNode, useCallback, useEffect, useRef, useState } from "react";
import { CircleCheck, LoaderCircle, Play, Square, TriangleAlert } from "lucide-react";
import { Button } from "../../components/Button/Button";
import { useT } from "../../i18n";
import { Voice } from "../../library/library";
import { setSink } from "./settings";
import { Card, SectionTitle, Toggle } from "./ui";

// One clip at a time, through the chosen output.
export const usePlayer = (output: string) => {
  const player = useRef<HTMLAudioElement | null>(null);
  const [playing, setPlaying] = useState<string | null>(null);
  const stop = useCallback(() => {
    player.current?.pause();
    player.current = null;
    setPlaying(null);
  }, []);
  const play = useCallback(async (key: string, url: string) => {
    stop();
    const audio = new Audio(url);
    audio.onended = stop;
    audio.onerror = stop;
    player.current = audio;
    setPlaying(key);
    await setSink(audio, output);
    audio.play().catch(stop);
  }, [output, stop]);
  useEffect(() => stop, [stop]);
  return { playing, play, stop };
};

type VoiceSectionProps = {
  voices: Voice[];
  languages: [string, string][];
  language: string;
  // The draft's voice id ("" before a character is selected).
  selected: string;
  // Language of the draft's text (warns when the chosen voice speaks another language).
  textLanguage: string;
  onUse: (voice: Voice) => void;
  onlyLanguage: boolean;
  setOnlyLanguage: (value: boolean) => void;
  output: string;
  disabled: boolean;
  // State of the upload of the chosen voice to the server.
  status: ReactNode;
  // What a voice needs (length).
  note: string;
  // Actions on a voice (delete, export), and the "add a voice" panel.
  actions?: (voice: Voice) => ReactNode;
  children?: ReactNode;
};

export const VoiceSection: FC<VoiceSectionProps> = ({
  voices, languages, language, selected, textLanguage, onUse, onlyLanguage, setOnlyLanguage, output, disabled, status, note, actions, children,
}) => {
  const t = useT();
  const { playing, play, stop } = usePlayer(output);
  const label = (code: string) => languages.find(([c]) => c === code)?.[1] ?? code;
  const gender = (g: string) => g === "female" ? t("option.female") : g === "male" ? t("option.male") : t("option.unspecified");
  const shown = onlyLanguage ? voices.filter(v => v.language === language || v.id === selected) : voices;
  const missing = !!selected && !voices.some(v => v.id === selected);
  const selectedVoice = voices.find(v => v.id === selected);
  const mismatch = !!selectedVoice?.language && !!textLanguage && selectedVoice.language !== textLanguage;
  return (
    <Card>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <SectionTitle title={t("voice.title")} note={note} />
        <Toggle label={t("voices.onlyLanguage", { language: label(language) })} checked={onlyLanguage} onChange={setOnlyLanguage} />
      </div>
      <ul className="flex flex-col gap-1.5">
        {shown.map(v => {
          const inUse = v.id === selected;
          return (
            <li key={v.id} className={`flex flex-wrap items-center gap-2.5 rounded-xl bg-paper px-3 py-2 ${inUse ? "ring-2 ring-accent" : ""}`}>
              <button type="button" aria-label={playing === v.id ? t("voice.stop") : t("voices.play")}
                title={playing === v.id ? t("voice.stop") : t("voices.play")}
                onClick={() => playing === v.id ? stop() : play(v.id, v.audioUrl)}
                className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-fill text-ink active:opacity-75">
                {playing === v.id ? <Square className="h-3 w-3 fill-current" /> : <Play className="h-3 w-3 fill-current" />}
              </button>
              <span className="flex min-w-0 flex-1 flex-col">
                <span className="truncate text-sm font-semibold">{v.name || v.id}</span>
                <span className="flex items-center gap-1 text-xs text-muted">
                  {inUse && mismatch && <TriangleAlert className="h-3 w-3 shrink-0 text-warning" />}
                  {label(v.language)} · {gender(v.gender)} · {t("voice.seconds", { seconds: v.seconds.toFixed(1) })}
                </span>
              </span>
              {actions?.(v)}
              <Button selected={inUse} disabled={disabled || inUse || !selected} onClick={() => onUse(v)}>
                {inUse ? t("voices.inUse") : t("voices.use")}
              </Button>
            </li>
          );
        })}
      </ul>
      {mismatch && selectedVoice && <p className="flex items-start gap-1.5 text-xs text-warning">
        <TriangleAlert className="mt-px h-3.5 w-3.5 shrink-0" />
        {t("voices.languageMismatch", { voiceLanguage: label(selectedVoice.language), textLanguage: label(textLanguage) })}
      </p>}
      {missing ?
        <p className="flex items-start gap-1.5 text-xs text-warning">
          <TriangleAlert className="mt-px h-3.5 w-3.5 shrink-0" />{t("voices.missing")}
        </p> : status}
      {children}
    </Card>
  );
};

// The upload state of the chosen voice, under the list.
export const VoiceStatus: FC<{ state: "uploading" | "ready" | "failed"; text: string }> = ({ state, text }) => (
  <p className={`flex items-start gap-1.5 text-xs ${state === "failed" ? "text-warning" : state === "ready" ? "text-accent" : "text-muted"}`}>
    {state === "uploading" ? <LoaderCircle className="mt-px h-3.5 w-3.5 shrink-0 animate-spin" />
      : state === "ready" ? <CircleCheck className="mt-px h-3.5 w-3.5 shrink-0" />
        : <TriangleAlert className="mt-px h-3.5 w-3.5 shrink-0" />}
    {text}
  </p>
);
