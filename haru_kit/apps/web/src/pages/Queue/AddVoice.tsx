// Own voices: record (with a text to read aloud) or upload, then name, language, gender, and a
// Preview of the audio after the silence trim (what the model gets).
import { FC, useCallback, useEffect, useRef, useState } from "react";
import { Mic, Play, Square, TriangleAlert, Upload } from "lucide-react";
import { Button } from "../../components/Button/Button";
import { useT } from "../../i18n";
import { AUDIO_ACCEPT, roundSeconds, StoredAudio, toStoredAudio } from "../../library/audio";
import { FORMAT, newId, putVoice, today, VoiceFile } from "../../library/library";
import { getMimeType } from "../Conversation/getMimeType";
import { usePlayer } from "./VoiceSection";
import { SelectField, TextField } from "./ui";

// Recording: stop is possible after the minimum; it stops by itself only at the server's maximum
// (180 s: the model uses at most the last 180 s).
const RECORD_MIN_SECONDS = 3;

type AddVoiceProps = {
  languages: [string, string][];
  language: string;
  microphone: string;
  output: string;
  minSeconds: number;
  maxSeconds: number;
  disabled: boolean;
  onSaved: (voice: VoiceFile) => void;
};

// `seconds`: what the model gets; `speech`: the speech after the silence trim (longer: the last `seconds` are used).
type Clip = { wav: Blob; url: string; seconds: number; speech: number; recorded: boolean };

export const AddVoice: FC<AddVoiceProps> = ({ languages, language, microphone, output, minSeconds, maxSeconds, disabled, onSaved }) => {
  const t = useT();
  const [open, setOpen] = useState(false);
  const [recording, setRecording] = useState<{ recorder: MediaRecorder; startedAt: number } | null>(null);
  const [elapsed, setElapsed] = useState(0);
  const [clip, setClip] = useState<Clip | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [name, setName] = useState("");
  const [voiceLanguage, setVoiceLanguage] = useState(language);
  const [gender, setGender] = useState("");
  const [saving, setSaving] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);
  const { playing, play, stop } = usePlayer(output);

  // Recorded or uploaded, any type that the browser decodes: stored as 24 kHz mono 16-bit WAV after the
  // Silence trim; any server reads it, with or without ffmpeg.
  const accept = useCallback(async (blob: Blob, recorded: boolean) => {
    setError(null);
    let stored: StoredAudio;
    try {
      stored = await toStoredAudio(blob, maxSeconds);
    } catch (e) {
      console.error(e);
      setError(t("error.unreadable_audio"));
      return;
    }
    const { wav, seconds, speech } = stored;
    if (seconds < minSeconds) {
      setError(t("error.audio_too_short", { seconds: seconds.toFixed(1), min_seconds: minSeconds }));
      return;
    }
    setClip(old => {
      if (old) URL.revokeObjectURL(old.url);
      return { wav, url: URL.createObjectURL(wav), seconds, speech, recorded };
    });
  }, [t, minSeconds, maxSeconds]);

  const startRecording = useCallback(async () => {
    setError(null);
    let stream: MediaStream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({
        audio: { channelCount: 1, ...(microphone ? { deviceId: { exact: microphone } } : {}) },
      });
    } catch (e) {
      console.error(e);
      setError(`${t("error.microphoneDenied")} ${t("error.microphoneFixWeb")}`);
      return;
    }
    const mimeType = getMimeType("audio");
    const recorder = mimeType ? new MediaRecorder(stream, { mimeType }) : new MediaRecorder(stream);
    const chunks: Blob[] = [];
    recorder.ondataavailable = e => e.data.size > 0 && chunks.push(e.data);
    recorder.onstop = () => {
      stream.getTracks().forEach(track => track.stop());
      accept(new Blob(chunks, { type: recorder.mimeType || mimeType }), true);
    };
    recorder.start();
    setElapsed(0);
    setRecording({ recorder, startedAt: performance.now() });
  }, [microphone, accept, t]);

  const stopRecording = useCallback(() => {
    if (recording?.recorder.state === "recording") recording.recorder.stop();
    setRecording(null);
  }, [recording]);

  // The clock while recording; it stops by itself at the maximum.
  useEffect(() => {
    if (!recording) return;
    const id = window.setInterval(() => {
      const seconds = (performance.now() - recording.startedAt) / 1000;
      setElapsed(seconds);
      if (seconds >= maxSeconds) stopRecording();
    }, 100);
    return () => window.clearInterval(id);
  }, [recording, stopRecording, maxSeconds]);

  // Leaving the panel stops the microphone.
  useEffect(() => () => {
    if (recording?.recorder.state === "recording") {
      recording.recorder.onstop = null;
      recording.recorder.stop();
      recording.recorder.stream.getTracks().forEach(track => track.stop());
    }
  }, [recording]);

  const reset = useCallback(() => {
    stop();
    setClip(old => {
      if (old) URL.revokeObjectURL(old.url);
      return null;
    });
    setError(null);
    setName(t("voices.unnamed"));
    setGender("");
    setVoiceLanguage(language);
  }, [stop, language, t]);

  const save = useCallback(async () => {
    if (!clip) return;
    setSaving(true);
    try {
      const id = newId();
      const voice: VoiceFile = {
        format: FORMAT, id, name: name.trim() || t("voices.unnamed"), language: voiceLanguage, gender,
        seconds: roundSeconds(clip.seconds), text: clip.recorded ? t("voices.readText") : "", file: `${id}.wav`,
        updated: today(),
      };
      await putVoice(voice, clip.wav);
      reset();
      setOpen(false);
      onSaved(voice);
    } catch (e) {
      console.error(e);
      setError(String(e));
    } finally {
      setSaving(false);
    }
  }, [clip, name, voiceLanguage, gender, t, reset, onSaved]);

  if (!open) {
    return <Button className="self-start" disabled={disabled} onClick={() => {
      reset();
      setOpen(true);
    }}>{t("voices.add")}</Button>;
  }
  return (
    <div className="flex flex-col gap-3.5 rounded-xl bg-paper p-3.5">
      <p className="text-[13px] font-semibold">{t("voices.add")}</p>
      {!clip && <>
        <div className="flex flex-col gap-1.5 rounded-xl bg-card p-3">
          <p className="text-xs text-muted">{t("voices.readThis")}</p>
          <p className="text-sm leading-relaxed">{t("voices.readText")}</p>
        </div>
        <p className="text-xs text-muted">{t("voices.lengthHint")}</p>
        <div className="flex flex-wrap items-center gap-2.5">
          {recording ? <>
            <span className="h-[9px] w-[9px] shrink-0 rounded-full bg-[#ff3b30]" />
            <span className="text-xs tabular-nums text-muted">
              {t("voices.recording", { seconds: Math.floor(elapsed), min: RECORD_MIN_SECONDS, max: maxSeconds })}
            </span>
            <Button selected disabled={elapsed < RECORD_MIN_SECONDS} onClick={stopRecording}>
              <Square className="h-3 w-3 fill-current" />{t("voice.stopRecording")}
            </Button>
          </> : <>
            <Button accent disabled={disabled} onClick={startRecording}><Mic className="h-3.5 w-3.5" />{t("voices.record")}</Button>
            <Button disabled={disabled} onClick={() => fileInput.current?.click()}><Upload className="h-3.5 w-3.5" />{t("voices.upload")}</Button>
          </>}
          <input ref={fileInput} type="file" accept={AUDIO_ACCEPT} className="hidden" onChange={e => {
            const file = e.target.files?.[0];
            if (file) accept(file, false);
            e.target.value = "";
          }} />
        </div>
      </>}
      {clip && <>
        <div className="grid grid-cols-1 gap-x-3.5 gap-y-4 sm:grid-cols-3">
          <TextField label={t("voices.name")} value={name} placeholder={t("voices.unnamed")} onChange={setName} />
          <SelectField label={t("voices.language")} value={voiceLanguage} onChange={setVoiceLanguage} options={languages} />
          <SelectField label={t("voices.gender")} value={gender} onChange={setGender}
            options={[["", t("option.unspecified")], ["female", t("option.female")], ["male", t("option.male")]]} />
        </div>
        <div className="flex flex-wrap items-center gap-2.5">
          <Button onClick={() => playing ? stop() : play("clip", clip.url)}>
            {playing ? <Square className="h-3 w-3 fill-current" /> : <Play className="h-3 w-3 fill-current" />}
            {playing ? t("voice.stop") : t("voices.preview")}
          </Button>
          <span className="text-xs text-muted">
            {t("voices.trimmed", { seconds: clip.speech.toFixed(1) })}
            {clip.speech > clip.seconds + 0.05 && <> {t("voices.lastUsed", { max: maxSeconds })}</>}
          </span>
        </div>
      </>}
      {error && <p className="flex items-start gap-1.5 text-xs text-warning">
        <TriangleAlert className="mt-px h-3.5 w-3.5 shrink-0" />{error}
      </p>}
      <div className="flex flex-wrap gap-2">
        {clip && <Button accent disabled={saving} onClick={save}>{t("voices.save")}</Button>}
        <Button disabled={saving || !!recording} onClick={() => {
          reset();
          setOpen(false);
        }}>{t("voices.cancel")}</Button>
      </div>
    </div>
  );
};
