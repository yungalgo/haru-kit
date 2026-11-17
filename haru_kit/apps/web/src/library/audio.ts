// Audio helpers: decode to the model's rate, the server's silence trim, and WAV files.

export const MODEL_RATE = 24000;
// The model uses at most the last 180 s of a voice (upstream `prepare_reference`).
export const MAX_REFERENCE_SECONDS = 180;

// The audio types of a voice: the file pickers accept these, and any other audio/* type that the
// Browser can decode.
export const AUDIO_ACCEPT = "audio/*,.wav,.flac,.mp3,.m4a,.aac,.mp4,.ogg,.oga,.opus,.webm,.aiff,.aif,.caf";

// Any audio the browser can decode, as mono float samples at 24 kHz. Throws when it cannot.
export const decodeMono24k = async (blob: Blob): Promise<Float32Array> => {
  const data = await blob.arrayBuffer();
  // DecodeAudioData resamples to the context rate; the length only sizes rendering.
  const context = new OfflineAudioContext(1, 1, MODEL_RATE);
  const buffer = await context.decodeAudioData(data);
  const out = new Float32Array(buffer.length);
  for (let ch = 0; ch < buffer.numberOfChannels; ch++) {
    const samples = buffer.getChannelData(ch);
    for (let i = 0; i < out.length; i++) {
      out[i] += samples[i] / buffer.numberOfChannels;
    }
  }
  return out;
};

// Same as upstream `prepare_reference`: 20 ms windows, voiced above -45 dBFS RMS, 0.2 s kept
// Before the first and after the last voiced window, then at most the last `maxSeconds`.
export const trimSilence = (audio: Float32Array, maxSeconds: number): Float32Array => {
  const window = Math.round(0.02 * MODEL_RATE);
  const windows = Math.floor(audio.length / window);
  if (windows === 0) {
    return audio;
  }
  let first = -1;
  let last = -1;
  for (let w = 0; w < windows; w++) {
    let sum = 0;
    for (let i = w * window; i < (w + 1) * window; i++) {
      sum += audio[i] * audio[i];
    }
    const rms = Math.sqrt(sum / window + 1e-12);
    if (20 * Math.log10(rms) > -45) {
      if (first < 0) first = w;
      last = w;
    }
  }
  if (first < 0) {
    return new Float32Array(0);
  }
  const pad = Math.round(0.2 * MODEL_RATE);
  const start = Math.max(0, first * window - pad);
  const end = Math.min(audio.length, (last + 1) * window + pad);
  const limit = Math.round(maxSeconds * MODEL_RATE);
  return audio.slice(Math.max(start, end - limit), end);
};

// A voice as the app stores it: `wav` = the speech after the silence trim, at most the last
// `maxSeconds`, as 24 kHz mono 16-bit WAV; `seconds` = its length; `speech` = the length before the limit.
export type StoredAudio = { wav: Blob; seconds: number; speech: number };

// Throws when the browser cannot decode `blob`.
export const toStoredAudio = async (blob: Blob, maxSeconds = MAX_REFERENCE_SECONDS): Promise<StoredAudio> => {
  const speech = trimSilence(await decodeMono24k(blob), Infinity);
  const kept = speech.subarray(Math.max(0, speech.length - Math.round(maxSeconds * MODEL_RATE)));
  return { wav: encodeWav([kept], MODEL_RATE), seconds: kept.length / MODEL_RATE, speech: speech.length / MODEL_RATE };
};

// The `seconds` of a voice file: the stored length, to 0.01 s.
export const roundSeconds = (seconds: number) => Math.round(seconds * 100) / 100;

// A float sample as 16-bit PCM.
export const toInt16 = (x: number) => {
  const s = Math.max(-1, Math.min(1, x));
  return s < 0 ? s * 0x8000 : s * 0x7fff;
};

// 16-bit PCM WAV from interleaved samples (any number of chunks, so a long recording needs no copy).
export const wavFromInt16 = (chunks: Int16Array<ArrayBuffer>[], channels: number, rate: number): Blob => {
  const bytes = chunks.reduce((sum, c) => sum + c.byteLength, 0);
  const view = new DataView(new ArrayBuffer(44));
  const text = (offset: number, s: string) => [...s].forEach((c, i) => view.setUint8(offset + i, c.charCodeAt(0)));
  text(0, "RIFF");
  view.setUint32(4, 36 + bytes, true);
  text(8, "WAVE");
  text(12, "fmt ");
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true);  // PCM
  view.setUint16(22, channels, true);
  view.setUint32(24, rate, true);
  view.setUint32(28, rate * channels * 2, true);
  view.setUint16(32, channels * 2, true);
  view.setUint16(34, 16, true);
  text(36, "data");
  view.setUint32(40, bytes, true);
  // WAV is little-endian, like every platform this app runs on.
  return new Blob([view.buffer, ...chunks], { type: "audio/wav" });
};

// 16-bit PCM WAV; `channels` have the same length.
export const encodeWav = (channels: Float32Array[], rate: number): Blob => {
  const frames = channels[0]?.length ?? 0;
  const samples = new Int16Array(frames * channels.length);
  for (let i = 0; i < frames; i++) {
    for (let ch = 0; ch < channels.length; ch++) {
      samples[i * channels.length + ch] = toInt16(channels[ch][i]);
    }
  }
  return wavFromInt16([samples], channels.length, rate);
};
