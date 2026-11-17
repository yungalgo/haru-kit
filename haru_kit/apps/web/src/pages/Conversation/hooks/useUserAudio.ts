import { useCallback, useEffect, useRef, useState } from "react";
import { getPlayoutProfile } from "../playoutProfile";
import Recorder from "opus-recorder";
import encoderPath from "opus-recorder/dist/encoderWorker.min.js?url";
import { USER_CHANNEL, useMediaContext } from "../MediaContext";

export enum UserMediaStatuses {
  IDLE = "IDLE",
  READY = "READY",
  WAITING_FOR_PERMISSION = "WAITING_FOR_PERMISSION",
  ERROR = "ERROR",
  RECORDING = "RECORDING",
  STOPPED = "STOPPED",
  STOPPING = "STOPPING",
}

type useUserAudioArgs = {
  constraints: MediaStreamConstraints;
  onDataChunk?: (chunk: Uint8Array) => void;
  onRecordingStart?: () => void;
  onRecordingStop?: () => void;
};

// One shared default: a new function per render would restart the recorder on every render.
const noop = () => {};

export const useUserAudio = ({
  constraints,
  onDataChunk,
  onRecordingStart = noop,
  onRecordingStop = noop,
}: useUserAudioArgs) => {
  const { recordingMerger, audioContext, micDuration, micClock, muted } = useMediaContext();
  const [error, setError] = useState<string | null>(null);
  const [status, setStatus] = useState<UserMediaStatuses>(
    UserMediaStatuses.IDLE,
  );

  const recorder = useRef<any>(null);
  // The stream for the waveform and the recording (the Opus recorder opens its own).
  const stream = useRef<MediaStream | null>(null);
  // The call ended: whatever is still opening must close at once.
  const stopWanted = useRef(false);

  // Muted = silence to the model (its clock keeps running) and in the recording.
  const mutedRef = useRef(muted);
  mutedRef.current = muted;
  useEffect(() => {
    recorder.current?.setRecordingGain(muted ? 0 : 1);
    stream.current?.getAudioTracks().forEach(track => track.enabled = !muted);
  }, [muted]);

  const getMediaStream = useCallback(async () => {
    setStatus(UserMediaStatuses.WAITING_FOR_PERMISSION);
    try {
      const stream =
        await window.navigator.mediaDevices.getUserMedia(constraints);
      setStatus(UserMediaStatuses.IDLE);
      return stream;
    } catch (error: any) {
      console.error(error);
      setError(error.name);
      setStatus(UserMediaStatuses.ERROR);
      return null;
    }
  }, [constraints, setStatus]);

  const startRecordingUser = useCallback(async () => {
    stopWanted.current = false;
    const mediaStream = await getMediaStream();
    if (mediaStream && stopWanted.current) {
      // The call ended while the browser opened the microphone.
      mediaStream.getTracks().forEach(track => track.stop());
    } else if (mediaStream) {
      stream.current = mediaStream;
      mediaStream.getAudioTracks().forEach(track => track.enabled = !mutedRef.current);
      const settings: any = mediaStream.getAudioTracks()[0]?.getSettings() ?? {};
      micClock.current = { pageAt: 0, captureLatency: Number(settings.latency) || 0 };
      const analyser = audioContext.current.createAnalyser();
      const source = audioContext.current.createMediaStreamSource(mediaStream);
      source.connect(analyser);
      source.connect(recordingMerger.current, 0, USER_CHANNEL);

      // For buffer length: 960 = 24000 / 12.5 / 2
      // The /2 is a bit optional, but won't hurt for recording the mic.
      // Note that bufferLength actually has 0 impact for mono audio, only
      // The frameSize and maxFramesPerPage seems to have any.
      const recorderOptions = {
        // The recorder opens its own stream with these track constraints (the chosen microphone).
        mediaTrackConstraints: constraints.audio,
        encoderPath,
        bufferLength: Math.round(960 * audioContext.current.sampleRate / 24000),
        encoderFrameSize: 20,
        encoderSampleRate: 24000,
        maxFramesPerPage: getPlayoutProfile().mic_frames_per_page,
        numberOfChannels: 1,
        recordingGain: mutedRef.current ? 0 : 1,
        resampleQuality: 3,
        encoderComplexity: 0,
        encoderApplication: 2049,
        streamPages: true,
      };
      recorder.current = new Recorder(recorderOptions);
      recorder.current.ondataavailable = (data: Uint8Array) => {
        // Opus positions are always counted at 48 kHz.
        micDuration.current = recorder.current.encodedSamplePosition / 48000;
        micClock.current.pageAt = performance.now();
        if (onDataChunk) {
          onDataChunk(data);
        }
      };
      recorder.current.onstart = () => {
        setStatus(UserMediaStatuses.RECORDING);
        onRecordingStart();
        if (stopWanted.current) {
          recorder.current?.stop();
        }
      };
      recorder.current.onstop = () => {
        setStatus(UserMediaStatuses.STOPPED);
        source.disconnect();
        mediaStream.getTracks().forEach(track => track.stop());
        onRecordingStop();

        recorder.current = null;
      };

      // The recorder opens its own stream: it can fail on its own.
      recorder.current.start().catch((e: unknown) => {
        console.error(e);
        setError(e instanceof Error ? e.name : "Error");
        setStatus(UserMediaStatuses.ERROR);
      });

      return {
        analyser,
        mediaStream,
        source,
      };
    }
    return {
      analyser: null,
      mediaStream: null,
      source: null,
    };
  }, [setStatus, onDataChunk, onRecordingStart, onRecordingStop]);

  // Stops the Opus recorder (it closes its own stream) and the stream of the waveform,
  // Also when the recorder or the stream is still opening.
  const stopRecording = useCallback(() => {
    setStatus(UserMediaStatuses.STOPPING);
    stopWanted.current = true;
    recorder.current?.stop();
    stream.current?.getTracks().forEach(track => track.stop());
    stream.current = null;
  }, [setStatus]);

  return {
    status,
    error,
    startRecordingUser,
    stopRecording,
  };
};
