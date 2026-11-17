import { FC, MutableRefObject, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useSocket } from "./hooks/useSocket";
import { SocketContext } from "./SocketContext";
import { ServerAudio } from "./components/ServerAudio/ServerAudio";
import { UserAudio } from "./components/UserAudio/UserAudio";
import { ArrowDown, AudioLines, FileText, Film, Mic, MicOff, TriangleAlert } from "lucide-react";
import { Button, capsuleClass } from "../../components/Button/Button";
import { Masthead } from "../../components/Haru/Masthead";
import { WaveLanes } from "./components/WaveLanes/WaveLanes";
import { ServerAudioStats } from "./components/ServerAudio/ServerAudioStats";
import { AudioStats } from "./hooks/useServerAudio";
import { TextDisplay } from "./components/TextDisplay/TextDisplay";
import { MODEL_CHANNEL, MediaContext, USER_CHANNEL } from "./MediaContext";
import { ModelParamsValues, useModelParams } from "./hooks/useModelParams";
import fixWebmDuration from "webm-duration-fix";
import { toInt16, wavFromInt16 } from "../../library/audio";
import { getMimeType, getVideoExtension } from "./getMimeType";
import { RtcSocket } from "./rtcSocket";
import { VIDEO_PIXELS } from "../../haru/painters";
import { useT } from "../../i18n";
import { useDialog } from "../Queue/Dialog";

// The microphone chosen in Settings ("" = the system default).
const micConstraints = (deviceId: string): MediaStreamConstraints => ({
  audio: {
    echoCancellation: true, noiseSuppression: true, autoGainControl: true, channelCount: 1,
    ...(deviceId ? { deviceId: { exact: deviceId } } : {}),
  },
  video: false,
});

type ConversationProps = {
  audioContext: MutableRefObject<AudioContext>;
  worklet: MutableRefObject<AudioWorkletNode>;
  // Audio over WebRTC (Opus/UDP) instead of the /api/chat WebSocket.
  useWebRtc?: boolean;
  // Names on the two lanes (from the character card).
  userName?: string;
  modelName?: string;
  // Devices chosen in Settings ("" = the system default).
  microphoneId?: string;
  outputId?: string;
  // The server's limit for one conversation.
  timeLimitSeconds?: number;
} & Partial<ModelParamsValues>;

// The prompt, voice and settings were registered with POST /api/session; the socket
// Only carries the session id.
const chatURL = (sessionId: string) => {
  const protocol = window.location.protocol === "https:" ? "wss" : "ws";
  const url = new URL(`${protocol}://${window.location.host}/api/chat`);
  url.searchParams.append("session_id", sessionId);
  return url.toString();
};

// Pixel size of the exported video.
const VIDEO_SIZE = VIDEO_PIXELS;

export const Conversation: FC<ConversationProps> = ({
  audioContext,
  worklet,
  useWebRtc = false,
  userName,
  modelName,
  microphoneId = "",
  outputId = "",
  timeLimitSeconds = 3600,
  ...params
}) => {
  const t = useT();
  const getAudioStats = useRef<() => AudioStats>(() => ({
    playedAudioDuration: 0,
    missedAudioDuration: 0,
    totalAudioMessages: 0,
    delay: 0,
    minPlaybackDelay: 0,
    maxPlaybackDelay: 0,
  }));
  const isRecording = useRef<boolean>(false);
  const videoChunks = useRef<Blob[]>([]);
  // Both channels as heard (user left, model right), 16-bit, at the context's rate.
  const pcmChunks = useRef<Int16Array<ArrayBuffer>[]>([]);
  const pcmNode = useRef<ScriptProcessorNode | null>(null);
  // The model's words so far (for the .txt file).
  const transcript = useRef<string[]>([]);

  const audioStreamDestination = useRef<MediaStreamAudioDestinationNode>(audioContext.current.createMediaStreamDestination());
  const recordingMerger = useRef<ChannelMergerNode | null>(null);
  if (!recordingMerger.current) {
    audioStreamDestination.current.channelCount = 2;
    recordingMerger.current = audioContext.current.createChannelMerger(2);
    recordingMerger.current.connect(audioStreamDestination.current);
  }
  const mediaRecorder = useRef<MediaRecorder | null>(null);
  const [videoURL, setVideoURL] = useState<string>("");
  const [audioURL, setAudioURL] = useState<string>("");
  const [textURL, setTextURL] = useState<string>("");
  const [isOver, setIsOver] = useState(false);
  const modelParams = useModelParams(params);
  const micDuration = useRef<number>(0);
  const micClock = useRef({ pageAt: 0, captureLatency: 0 });
  const actualAudioPlayed = useRef<number>(0);
  const textContainerRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const [userAnalyser, setUserAnalyser] = useState<AnalyserNode | null>(null);
  const [modelAnalyser, setModelAnalyser] = useState<AnalyserNode | null>(null);

  const WSURL = chatURL(modelParams.chatSessionId ?? "");

  // Why the conversation ended: the user, the server's time limit, or a lost server.
  const [endReason, setEndReason] = useState<"user" | "limit" | "lost" | null>(null);
  const endedByUser = useRef(false);
  const connectedAt = useRef<number | null>(null);
  const onDisconnect = useCallback(() => {
    setIsOver(true);
    stopRecording();
    const seconds = connectedAt.current === null ? 0 : (performance.now() - connectedAt.current) / 1000;
    setEndReason(reason => reason ?? (endedByUser.current ? "user" : seconds >= timeLimitSeconds - 5 ? "limit" : "lost"));
  }, [setIsOver, timeLimitSeconds]);

  const constraints = useMemo(() => micConstraints(microphoneId), [microphoneId]);
  const createSocket = useCallback(
    () => new RtcSocket(modelParams.chatSessionId ?? "", constraints, WSURL) as unknown as WebSocket,
    [modelParams.chatSessionId, WSURL, constraints],
  );
  const { isConnected, isWaitingForHandshake, sendMessage, socket, start, stop } = useSocket({
    uri: WSURL,
    onDisconnect,
    createSocket: useWebRtc ? createSocket : undefined,
  });
  useEffect(() => {
    if (isConnected && connectedAt.current === null) {
      connectedAt.current = performance.now();
    }
  }, [isConnected]);

  // The microphone could not be used (name of the getUserMedia error).
  const [micError, setMicError] = useState<string | null>(null);
  const micMessage = !micError ? null
    : micError === "NotAllowedError" || micError === "SecurityError" ? `${t("error.microphoneDenied")} ${t("error.microphoneFixWeb")}`
      : micError === "NotFoundError" || micError === "OverconstrainedError" ? t("error.noDevice") : t("error.microphone");

  // Mute (the model gets silence and keeps its clock).
  const [muted, setMuted] = useState(false);
  useEffect(() => {
    if (socket instanceof RtcSocket) {
      socket.setMuted(muted);
    }
  }, [socket, muted]);

  // Time left of the server's limit, while connected.
  const [secondsLeft, setSecondsLeft] = useState(timeLimitSeconds);
  useEffect(() => {
    if (!isConnected) return;
    const tick = () => setSecondsLeft(Math.max(0, timeLimitSeconds - (performance.now() - (connectedAt.current ?? performance.now())) / 1000));
    tick();
    const id = window.setInterval(tick, 1000);
    return () => window.clearInterval(id);
  }, [isConnected, timeLimitSeconds]);
  const timeLeft = `${Math.floor(secondsLeft / 60)}:${Math.floor(secondsLeft % 60).toString().padStart(2, "0")}`;

  // The text follows the newest words only while the user is at the end.
  const [following, setFollowing] = useState(true);
  const onTextScroll = useCallback(() => {
    const box = textContainerRef.current;
    if (box) setFollowing(box.scrollHeight - box.scrollTop - box.clientHeight < 24);
  }, []);
  const jumpToLatest = useCallback(() => {
    const box = textContainerRef.current;
    box?.scroll({ top: box.scrollHeight });
    setFollowing(true);
  }, []);

  useEffect(() => {
    start();
    return () => {
      stop();
    };
  }, [start]);

  useEffect(() => {
    const ctx = canvasRef.current?.getContext("2d");
    if (ctx) {
      // The video is laid out like this screen (white page): WaveLanes draws the name
      // And the lanes every frame, TextDisplay the transcript below them.
      ctx.setTransform(1, 0, 0, 1, 0, 0);
      ctx.fillStyle = "white";
      ctx.fillRect(0, 0, VIDEO_SIZE, VIDEO_SIZE);
    }
  }, [canvasRef]);

  const startRecording = useCallback(() => {
    if (isRecording.current) {
      return;
    }
    if (canvasRef.current) {
      // Note: Attaching a track from this stream to the existing MediaRecorder
      // Rather than creating a new MediaRecorder for the canvas stream
      // Doesn't work on Safari as it just ends the recording immediately.
      // It works on Chrome though and is much cleaner.
      const captureStream = canvasRef.current.captureStream(30);
      captureStream.addTrack(audioStreamDestination.current.stream.getAudioTracks()[0]);
      mediaRecorder.current = new MediaRecorder(captureStream, { mimeType: getMimeType("video"), videoBitsPerSecond: 10000000, audioBitsPerSecond: 192000 });
      mediaRecorder.current.ondataavailable = (e) => {
        videoChunks.current.push(e.data);
      };
      mediaRecorder.current.onstop = async () => {
        let blob: Blob;
        const mimeType = getMimeType("video");
        if (mimeType.includes("webm")) {
          blob = await fixWebmDuration(new Blob(videoChunks.current, { type: mimeType }));
        } else {
          blob = new Blob(videoChunks.current, { type: mimeType });
        }
        setVideoURL(URL.createObjectURL(blob));
        videoChunks.current = [];
      };
    }
    worklet.current?.connect(recordingMerger.current!, 0, MODEL_CHANNEL);

    // The merger mixes the microphone and the model's audio as played, on the context's clock.
    const ctx = audioContext.current;
    const node = ctx.createScriptProcessor(4096, 2, 2);
    node.channelCount = 2;
    node.channelCountMode = "explicit";
    node.channelInterpretation = "discrete";
    pcmChunks.current = [];
    node.onaudioprocess = e => {
      const left = e.inputBuffer.getChannelData(USER_CHANNEL);
      const right = e.inputBuffer.getChannelData(MODEL_CHANNEL);
      const out = new Int16Array(left.length * 2);
      for (let i = 0; i < left.length; i++) {
        out[2 * i] = toInt16(left[i]);
        out[2 * i + 1] = toInt16(right[i]);
      }
      pcmChunks.current.push(out);
    };
    recordingMerger.current!.connect(node);
    node.connect(ctx.destination);  // A script processor runs only when connected; its output is silence
    pcmNode.current = node;

    setVideoURL("");
    setAudioURL("");
    setTextURL("");
    mediaRecorder.current?.start();
    isRecording.current = true;
  }, [isRecording, setVideoURL, worklet, audioStreamDestination, mediaRecorder, canvasRef, audioContext]);

  const stopRecording = useCallback(() => {
    if (!isRecording.current) {
      return;
    }
    worklet.current?.disconnect(recordingMerger.current!, 0, MODEL_CHANNEL);
    if (pcmNode.current) {
      recordingMerger.current?.disconnect(pcmNode.current);
      pcmNode.current.disconnect();
      pcmNode.current.onaudioprocess = null;
      pcmNode.current = null;
      setAudioURL(URL.createObjectURL(wavFromInt16(pcmChunks.current, 2, audioContext.current.sampleRate)));
      pcmChunks.current = [];
    }
    setTextURL(URL.createObjectURL(new Blob([transcript.current.join("").trim() + "\n"], { type: "text/plain;charset=utf-8" })));
    mediaRecorder.current?.stop();
    isRecording.current = false;
  }, [isRecording, worklet, audioStreamDestination, mediaRecorder, audioContext]);

  // "Back to setup" reloads the page and closing the tab drops it: both lose the last
  // Conversation, so ask first while nothing (audio, video or text) was saved.
  const [dialog, ask] = useDialog();
  const [saved, setSaved] = useState(false);
  const unsaved = !saved && !!(audioURL || videoURL || textURL);
  const saveRef = useRef<HTMLDivElement>(null);
  const leaving = useRef(false);
  useEffect(() => {
    if (!unsaved) return;
    // The browser shows its own question; a page cannot change its text.
    const onBeforeUnload = (e: BeforeUnloadEvent) => {
      if (leaving.current) return;
      e.preventDefault();
      e.returnValue = "";
    };
    window.addEventListener("beforeunload", onBeforeUnload);
    return () => window.removeEventListener("beforeunload", onBeforeUnload);
  }, [unsaved]);
  const restart = async () => {
    if (unsaved) {
      const answer = await ask(t("save.notSaved"), [["dontSave", t("save.dontSave")], ["save", t("save.choose"), true]]);
      if (answer !== "dontSave") {
        // "Save…" (or the question closed): stay here, with the save buttons in view.
        if (answer === "save") {
          saveRef.current?.scrollIntoView({ block: "nearest" });
          saveRef.current?.querySelector("a")?.focus();
        }
        return;
      }
    }
    leaving.current = true;
    localStorage.setItem("cfgScaleV4", modelParams.cfgScale.toString());
    document.location.reload();
  };
  // Which transport the conversation ended up on (WebRTC falls back to WebSocket).
  const transport = (socket as unknown as { transport?: string } | null)?.transport === "webrtc" ? "WebRTC" : "WebSocket";
  const endProblem = isOver && (endReason === "limit" || endReason === "lost");
  const statusText = isConnected ? t("status.connected", { transport })
    : isWaitingForHandshake ? t("status.waiting")
      : !isOver ? t("status.disconnected")
        : endReason === "limit" ? t("status.timeLimit", { minutes: Math.round(timeLimitSeconds / 60) })
          : endReason === "lost" ? t("error.disconnected") : t("status.ended");

  return (
    <SocketContext.Provider
      value={{
        isConnected,
        sendMessage,
        socket,
      }}
    >
      <MediaContext.Provider value={
        {
          startRecording,
          stopRecording,
          audioContext,
          worklet,
          audioStreamDestination,
          recordingMerger: recordingMerger as React.MutableRefObject<ChannelMergerNode>,
          micDuration,
          micClock,
          actualAudioPlayed,
          micConstraints: constraints,
          outputId,
          muted,
        }
      }>
        <div className="flex h-screen flex-col gap-5 p-4 sm:p-6" style={{ height: "100dvh" }}>
          {dialog}
          <div className="flex items-center gap-3">
            {isOver && (
              <Button accent onClick={restart}>{t("action.backToSetup")}</Button>
            )}
            {!isOver && !isWaitingForHandshake && (
              <Button
                onClick={() => {
                  audioContext.current.resume();
                  if (isConnected) {
                    endedByUser.current = true;
                    stop();
                  } else {
                    start();
                  }
                }}
              >
                {isConnected ? t("action.end") : t("action.connect")}
              </Button>
            )}
            {isConnected && (
              <Button selected={muted} onClick={() => setMuted(!muted)}>
                {muted ? <MicOff className="h-3.5 w-3.5" /> : <Mic className="h-3.5 w-3.5" />}
                {muted ? t("live.unmute") : t("live.mute")}
              </Button>
            )}
            <span className={`h-2 w-2 shrink-0 rounded-full ${isConnected ? "bg-accent" : endProblem ? "bg-warning" : "bg-line"}`} />
            <span className={`min-w-0 flex-1 text-[13px] leading-tight ${endProblem ? "text-warning" : "text-muted"}`}>
              {statusText}
              {isConnected && muted && <span className="text-warning"> · {t("live.muted")}</span>}
              {isConnected && <span className="tabular-nums"> · {t("live.timeLeft", { time: timeLeft })}</span>}
            </span>
            <div className="ml-auto shrink-0">
              <Masthead compact />
            </div>
          </div>

          {micMessage && (
            <p className="flex items-start gap-1.5 rounded-[18px] bg-card p-[18px] text-[13px] text-warning">
              <TriangleAlert className="mt-0.5 h-3.5 w-3.5 shrink-0" />{micMessage}
            </p>
          )}
          {(audioURL || videoURL || textURL) && (
            <div ref={saveRef} className="flex flex-col gap-2 rounded-[18px] bg-card p-[18px]">
              <div className="flex flex-wrap items-center gap-2.5"
                onClick={e => e.target instanceof Element && e.target.closest("a[download]") && setSaved(true)}>
                <span className="text-[13px] font-medium">{t("save.title")}</span>
                {audioURL && <a href={audioURL} download="haru audio.wav" className={capsuleClass({ accent: true })}>
                  <AudioLines className="h-3.5 w-3.5" />{t("save.audio")}
                </a>}
                {textURL && <a href={textURL} download="haru text.txt" className={capsuleClass({ accent: true })}>
                  <FileText className="h-3.5 w-3.5" />{t("save.text")}
                </a>}
                {videoURL && <a href={videoURL} download={`haru video.${getVideoExtension()}`} className={capsuleClass({ accent: true })}>
                  <Film className="h-3.5 w-3.5" />{t("save.video")}
                </a>}
              </div>
              {audioURL && <p className="text-xs text-muted">{t("save.audioHelp")}</p>}
            </div>
          )}

          <ServerAudioStats getAudioStats={getAudioStats} sessionId={modelParams.chatSessionId} />

          <ServerAudio
            onAnalyser={setModelAnalyser}
            setGetAudioStats={(callback: () => AudioStats) =>
              (getAudioStats.current = callback)
            }
          />
          <UserAudio onAnalyser={setUserAnalyser} onError={setMicError} />
          <WaveLanes
            userAnalyser={userAnalyser}
            modelAnalyser={modelAnalyser}
            userName={userName?.trim() || t("speaker.you")}
            modelName={modelName?.trim() || t("speaker.model")}
            copyCanvasRef={canvasRef}
          />

          <div className="relative flex min-h-[96px] flex-1 flex-col">
            <div className="min-h-0 flex-1 overflow-y-auto sm:pl-[126px]" ref={textContainerRef} onScroll={onTextScroll}>
              <TextDisplay containerRef={textContainerRef} copyCanvasRef={canvasRef} follow={following}
                onText={text => transcript.current = text} />
            </div>
            {!following && (
              <Button className="absolute bottom-2 left-1/2 -translate-x-1/2 shadow" onClick={jumpToLatest}>
                <ArrowDown className="h-3.5 w-3.5" />{t("live.jumpToLatest")}
              </Button>
            )}
          </div>

          <canvas height={VIDEO_SIZE} width={VIDEO_SIZE} className="hidden" ref={canvasRef} />
        </div>
      </MediaContext.Provider>
    </SocketContext.Provider >
  );
};
