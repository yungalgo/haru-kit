// The server's reference id of each voice: its audio is uploaded with POST /api/reference_audio
// Once for this page load.
import { useEffect, useState } from "react";
import { Voice, voiceAudio } from "../../library/library";

export type Reference = { id: string; seconds: number; trimmed: boolean };

// An upload that the server refused: its JSON body (code, error, params) or text.
export class UploadError extends Error {
  constructor(readonly body: string) {
    super(body);
  }
}

const uploads = new Map<string, Promise<Reference>>();

export const uploadAudio = async (audio: Blob): Promise<Reference> => {
  const response = await fetch("/api/reference_audio", {
    method: "POST",
    headers: { "Content-Type": "application/octet-stream" },
    body: audio,
  });
  if (!response.ok) {
    throw new UploadError(await response.text());
  }
  return await response.json() as Reference;
};

// The reference of `voice`; a failed upload is tried again at the next call.
export const referenceFor = (voice: Voice): Promise<Reference> => {
  let upload = uploads.get(voice.id);
  if (!upload) {
    upload = voiceAudio(voice).then(uploadAudio);
    upload.catch(() => uploads.delete(voice.id));
    uploads.set(voice.id, upload);
  }
  return upload;
};

// The server no longer knows the reference of `voice` (it restarted): upload it again next time.
export const forgetReference = (voiceId: string) => {
  uploads.delete(voiceId);
};

export type ReferenceState =
  | { voiceId: string; status: "uploading" }
  | { voiceId: string; status: "ready"; reference: Reference }
  | { voiceId: string; status: "failed"; error: unknown };

// Upload the voice as soon as it is chosen, so that Start does not wait.
export const useReference = (voice: Voice | undefined): ReferenceState | null => {
  const [state, setState] = useState<ReferenceState | null>(null);
  useEffect(() => {
    if (!voice) {
      setState(null);
      return;
    }
    let current = true;
    setState({ voiceId: voice.id, status: "uploading" });
    referenceFor(voice).then(
      reference => current && setState({ voiceId: voice.id, status: "ready", reference }),
      error => current && setState({ voiceId: voice.id, status: "failed", error }),
    );
    return () => {
      current = false;
    };
    // The voice list is rebuilt on each render: follow the id, not the object.
  }, [voice?.id]);
  // Never the state of another voice.
  return state && voice && state.voiceId === voice.id ? state : null;
};
