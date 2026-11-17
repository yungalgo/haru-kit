import { FC, useCallback, useEffect } from "react";
import { useSocketContext } from "../../SocketContext";
import { useUserAudio } from "../../hooks/useUserAudio";
import { useMediaContext } from "../../MediaContext";

type UserAudioProps = {
  // The analyser on your voice, for the waveform lane.
  onAnalyser: (analyser: AnalyserNode | null) => void;
  // The name of the microphone error (getUserMedia), else null.
  onError: (error: string | null) => void;
};
export const UserAudio: FC<UserAudioProps> = ({ onAnalyser, onError }) => {
  const { sendMessage, isConnected } = useSocketContext();
  const onRecordingChunk = useCallback(
    (chunk: Uint8Array) => {
      if (!isConnected) {
        return;
      }
      sendMessage({
        type: "audio",
        data: chunk,
      });
    },
    [sendMessage, isConnected],
  );

  const { micConstraints } = useMediaContext();
  const { startRecordingUser, stopRecording, error } = useUserAudio({
    constraints: micConstraints,
    onDataChunk: onRecordingChunk,
  });
  useEffect(() => {
    onError(error);
  }, [error, onError]);

  useEffect(() => {
    let res: Awaited<ReturnType<typeof startRecordingUser>>;
    if (isConnected) {
      startRecordingUser().then(result => {
        if (result) {
          res = result;
          onAnalyser(result.analyser);
        }
      });
    }
    return () => {
      stopRecording();
      res?.source?.disconnect();
    };
  }, [startRecordingUser, stopRecording, isConnected, onAnalyser]);

  return null;
};
