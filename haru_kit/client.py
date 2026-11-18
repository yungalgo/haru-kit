"""Shared client for Haru's ``/v1/realtime?duplex=1`` WebSocket API and 133.33 ms frame clock.

Used by ``haru-kit check``, the Pipecat adapter (:mod:`haru_kit.pipecat`), and the LiveKit
adapter (:mod:`haru_kit.livekit`).
"""

from __future__ import annotations

import asyncio
import base64
import io
import json
import logging
import secrets
import time
import wave
from collections import deque
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Literal
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import aiohttp
import numpy as np
import soundfile as sf

from . import card as card_mod
from .runtime.api_server import FRAME_SAMPLES, SAMPLE_RATE, _resample_mono, decode_audio_bytes, trim_and_cap_reference

logger = logging.getLogger(__name__)

FRAME_SECONDS = FRAME_SAMPLES / SAMPLE_RATE  # 0.133333... s (7.5 Hz)
MAX_MODAL_REF_URI_BYTES = 1_900_000  # safely below Modal's 2 MiB WebSocket message limit
InterruptPolicy = Literal["native", "mute"]


class HaruClientError(RuntimeError):
    """Base error raised by :class:`HaruSession`."""

    def __init__(self, message: str, *, code: str | None = None) -> None:
        super().__init__(message)
        self.code = code


class ServerFullError(HaruClientError):
    """The server has reached ``max_sessions`` (``resource_exhausted``)."""

    def __init__(self, message: str = "The Haru server is full (resource_exhausted).") -> None:
        super().__init__(message, code="resource_exhausted")


class HaruConnectionLostError(HaruClientError):
    """The WebSocket connection closed unexpectedly mid-conversation."""


@dataclass
class OutputChunk:
    """One 133.33 ms output frame from the model (or pre-roll notice)."""

    pcm16: bytes  # 3,200 signed 16-bit little-endian samples at 24 kHz (6,400 bytes)
    text: str = ""
    is_preroll: bool = False
    frame_index: int = 0


@dataclass
class CallStats:
    """Telemetry and diagnostics collected over a single :class:`HaruSession`."""

    started_at: float = 0.0
    ended_at: float = 0.0
    connect_seconds: float = 0.0
    prompt_seconds: float = 0.0
    frames_sent: int = 0
    frames_received: int = 0
    silence_samples_added: int = 0
    samples_dropped: int = 0
    frame_latencies_s: list[float] = field(default_factory=list)
    transcript_parts: list[str] = field(default_factory=list)
    recording_wav: Path | None = None
    recording_txt: Path | None = None

    @property
    def duration_seconds(self) -> float:
        end = self.ended_at or time.perf_counter()
        return max(0.0, end - self.started_at) if self.started_at else 0.0

    @property
    def silence_seconds_added(self) -> float:
        return self.silence_samples_added / SAMPLE_RATE

    @property
    def dropped_seconds(self) -> float:
        return self.samples_dropped / SAMPLE_RATE

    @property
    def frame_ms_p50(self) -> float:
        if not self.frame_latencies_s:
            return 0.0
        return round(float(np.percentile(self.frame_latencies_s, 50) * 1000.0), 1)

    @property
    def frame_ms_p95(self) -> float:
        if not self.frame_latencies_s:
            return 0.0
        return round(float(np.percentile(self.frame_latencies_s, 95) * 1000.0), 1)

    @property
    def transcript(self) -> str:
        return "".join(self.transcript_parts).strip()

    def summary_line(self) -> str:
        return (
            f"duration={self.duration_seconds:.1f}s frames={self.frames_sent}/{self.frames_received} "
            f"silence_added={self.silence_seconds_added:.2f}s dropped={self.dropped_seconds:.2f}s "
            f"frame_p50={self.frame_ms_p50:.1f}ms frame_p95={self.frame_ms_p95:.1f}ms"
        )


def realtime_url(url: str) -> str:
    """Normalize an HTTP(S) or WS(S) server address into ``ws(s)://.../v1/realtime?duplex=1``."""
    raw = url.strip()
    if not raw:
        raise HaruClientError("Haru server URL is empty.")
    if "://" not in raw:
        raw = f"ws://{raw}"
    parsed = urlparse(raw)
    scheme_map = {"http": "ws", "https": "wss", "ws": "ws", "wss": "wss"}
    scheme = scheme_map.get(parsed.scheme.lower())
    if not scheme or not parsed.netloc:
        raise HaruClientError(f"Invalid Haru server URL: {url!r}")
    path = parsed.path.rstrip("/")
    if not path.endswith("/v1/realtime"):
        path = f"{path}/v1/realtime"
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query.setdefault("duplex", "1")
    return urlunparse((scheme, parsed.netloc, path, "", urlencode(query), ""))


def encode_voice_uri(
    voice_source: Path | bytes | np.ndarray | None,
    *,
    max_uri_bytes: int = MAX_MODAL_REF_URI_BYTES,
) -> str | None:
    """Encode a voice reference as ``data:audio/flac;base64,...`` (24 kHz mono, silence-trimmed)."""
    if voice_source is None:
        return None
    if isinstance(voice_source, Path):
        samples = decode_audio_bytes(voice_source.read_bytes(), SAMPLE_RATE)
    elif isinstance(voice_source, (bytes, bytearray)):
        samples = decode_audio_bytes(bytes(voice_source), SAMPLE_RATE)
    elif isinstance(voice_source, np.ndarray):
        samples = np.asarray(voice_source, dtype=np.float32).reshape(-1)
    else:
        raise HaruClientError(f"Unsupported voice source type: {type(voice_source).__name__}")

    trimmed = trim_and_cap_reference(samples, SAMPLE_RATE)
    if trimmed.size < SAMPLE_RATE:  # minimum ~1s sanity check (upstream recommends >= 3s)
        if samples.size == 0:
            raise HaruClientError("Voice clip is empty or contains only silence.")
        trimmed = samples[-int(180 * SAMPLE_RATE) :]

    buf = io.BytesIO()
    sf.write(buf, trimmed, SAMPLE_RATE, format="FLAC", subtype="PCM_16")
    uri = "data:audio/flac;base64," + base64.b64encode(buf.getvalue()).decode("ascii")
    if max_uri_bytes and len(uri) > max_uri_bytes:
        raise HaruClientError(
            f"Encoded voice clip is {len(uri) / 1e6:.2f} MB (limit {max_uri_bytes / 1e6:.2f} MB for WebSocket frames). "
            "Use a shorter voice clip (10–30 seconds is ideal).",
            code="voice_too_large",
        )
    return uri


def dbfs_rms(samples_f32: np.ndarray) -> float:
    if samples_f32.size == 0:
        return -120.0
    rms = float(np.sqrt(np.mean(np.square(samples_f32, dtype=np.float64)) + 1e-12))
    return 20.0 * float(np.log10(rms))


def pcm16_to_f32(pcm16: bytes | np.ndarray) -> np.ndarray:
    if isinstance(pcm16, (bytes, bytearray, memoryview)):
        arr = np.frombuffer(pcm16, dtype="<i2")
    else:
        arr = np.asarray(pcm16, dtype="<i2")
    return arr.astype(np.float32) / 32768.0


def f32_to_pcm16_bytes(samples: np.ndarray) -> bytes:
    clipped = np.clip(np.round(np.asarray(samples, dtype=np.float32) * 32767.0), -32768, 32767).astype("<i2")
    return clipped.tobytes()


class IdleGate:
    """Zeroes output frames quieter than ``threshold_dbfs`` RMS after ``hold_frames`` frames of tail."""

    def __init__(self, threshold_dbfs: float = -65.0, hold_frames: int = 2) -> None:
        self.threshold_dbfs = float(threshold_dbfs)
        self.hold_frames = max(0, int(hold_frames))
        self._remaining_hold = 0

    def apply(self, pcm16: bytes) -> bytes:
        if not pcm16:
            return pcm16
        if dbfs_rms(pcm16_to_f32(pcm16)) >= self.threshold_dbfs:
            self._remaining_hold = self.hold_frames
            return pcm16
        if self._remaining_hold > 0:
            self._remaining_hold -= 1
            return pcm16
        return b"\x00" * len(pcm16)


class HaruSession:
    """One live full-duplex conversation with steady 133.33 ms clock, optional pre-roll, and recording."""

    def __init__(
        self,
        url: str,
        *,
        api_key: str | None = None,
        card: str | Path | Mapping[str, Any] | None = None,
        voice: str | Path | bytes | np.ndarray | None = None,
        language: str = "en",
        library_dir: Path | None = None,
        seed_registry: bool = True,
        guidance_scale: float = 1.5,
        temperature: float = 0.7,
        top_k: int = 25,
        top_p: float = 1.0,
        speak_first: bool = False,
        seed: int | None = None,
        preroll: str | Path | bytes | np.ndarray | None = None,
        interrupt_policy: InterruptPolicy = "native",
        caller_speech_dbfs: float = -40.0,
        record_dir: str | Path | None = None,
        connect_timeout: float = 180.0,
        max_uri_bytes: int = MAX_MODAL_REF_URI_BYTES,
        on_transcript: Callable[[str], None] | None = None,
    ) -> None:
        if interrupt_policy not in ("native", "mute"):
            raise ValueError(f"interrupt_policy must be 'native' or 'mute', got {interrupt_policy!r}")
        self.ws_url = realtime_url(url)
        self.api_key = api_key or None
        self.card_spec = card
        self.voice_spec = voice
        self.language = language
        self.library_dir = library_dir
        self.seed_registry = seed_registry
        self.guidance_scale = guidance_scale
        self.temperature = temperature
        self.top_k = top_k
        self.top_p = top_p
        self.speak_first = speak_first
        self.seed = seed
        self.preroll_spec = preroll
        self.interrupt_policy = interrupt_policy
        self.caller_speech_dbfs = caller_speech_dbfs
        self.record_dir = Path(record_dir) if record_dir else None
        self.connect_timeout = connect_timeout
        self.max_uri_bytes = max_uri_bytes
        self.on_transcript = on_transcript

        self.stats = CallStats()
        self._http: aiohttp.ClientSession | None = None
        self._ws: aiohttp.ClientWebSocketResponse | None = None
        self._reader_task: asyncio.Task[None] | None = None
        self._clock_task: asyncio.Task[None] | None = None

        self._input_chunks: deque[np.ndarray] = deque()
        self._input_samples = 0
        self._sent_times: deque[float] = deque()
        self._pending_text: list[str] = []
        self._output_queue: asyncio.Queue[OutputChunk | Exception | None] = asyncio.Queue()

        self._muted_phrase = False
        self._quiet_model_frames = 0
        self._closing = False
        self._connected = False

        self._rec_caller: list[np.ndarray] = []
        self._rec_model: list[np.ndarray] = []
        self._rec_lines: list[str] = []

    @property
    def connected(self) -> bool:
        return self._connected and not self._closing

    def push_audio(self, audio: bytes | np.ndarray, *, sample_rate: int = SAMPLE_RATE, fmt: str = "pcm16") -> None:
        """Append incoming caller audio (``pcm16`` bytes/array or ``f32`` array) to the clock buffer."""
        if self._closing:
            return
        if fmt == "pcm16":
            mono = pcm16_to_f32(audio)
        elif fmt == "f32":
            mono = np.asarray(audio, dtype=np.float32).reshape(-1)
        else:
            raise ValueError(f"unsupported audio fmt: {fmt!r}")
        if mono.size == 0:
            return
        if sample_rate != SAMPLE_RATE:
            mono = _resample_mono(mono, sample_rate, SAMPLE_RATE)
        self._input_chunks.append(mono)
        self._input_samples += int(mono.size)

    def _pop_caller_frame(self) -> np.ndarray:
        # Drop oldest samples when backlog exceeds 2 frames (266.7 ms) so lag never accumulates.
        max_backlog = 2 * FRAME_SAMPLES
        if self._input_samples > max_backlog:
            drop = self._input_samples - max_backlog
            self.stats.samples_dropped += drop
            self._consume_samples(drop)

        if self._input_samples >= FRAME_SAMPLES:
            return self._consume_samples(FRAME_SAMPLES)

        have = self._input_samples
        taken = self._consume_samples(have) if have > 0 else np.zeros(0, dtype=np.float32)
        missing = FRAME_SAMPLES - taken.size
        self.stats.silence_samples_added += missing
        if taken.size == 0:
            return np.zeros(FRAME_SAMPLES, dtype=np.float32)
        return np.pad(taken, (0, missing))

    def _consume_samples(self, count: int) -> np.ndarray:
        if count <= 0:
            return np.zeros(0, dtype=np.float32)
        parts: list[np.ndarray] = []
        remaining = count
        while remaining > 0 and self._input_chunks:
            head = self._input_chunks[0]
            if head.size <= remaining:
                parts.append(self._input_chunks.popleft())
                self._input_samples -= int(head.size)
                remaining -= int(head.size)
            else:
                parts.append(head[:remaining])
                self._input_chunks[0] = head[remaining:]
                self._input_samples -= remaining
                remaining = 0
        return parts[0] if len(parts) == 1 else np.concatenate(parts)

    @staticmethod
    def _error_from_event(event: Mapping[str, Any]) -> HaruClientError:
        err = event.get("error")
        if isinstance(err, Mapping):
            code = str(err.get("code") or "")
            msg = str(err.get("message") or code or err)
        else:
            code = ""
            msg = str(err or event)
        if code == "resource_exhausted":
            return ServerFullError(msg)
        return HaruClientError(msg, code=code or None)

    async def connect(self, *, start_clock: bool = True) -> HaruSession:
        """Connect to the server, send ``session.update``, pre-warm prompt frame 0, and optionally start the clock."""
        if self._connected:
            return self
        prompt_text, voice_source = await asyncio.to_thread(
            card_mod.resolve_card_and_voice,
            self.card_spec,
            self.voice_spec,
            language=self.language,
            library_dir=self.library_dir,
            seed_registry=self.seed_registry,
        )
        ref_uri = await asyncio.to_thread(encode_voice_uri, voice_source, max_uri_bytes=self.max_uri_bytes)

        headers: dict[str, str] = {}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        t0 = time.perf_counter()
        self._http = aiohttp.ClientSession()
        try:
            try:
                self._ws = await self._http.ws_connect(
                    self.ws_url,
                    headers=headers,
                    max_msg_size=0,
                    heartbeat=20,
                    timeout=aiohttp.ClientWSTimeout(ws_close=5.0),
                )
            except aiohttp.WSServerHandshakeError as error:
                if error.status in (401, 403):
                    raise HaruClientError(
                        f"Authentication rejected by {self.ws_url} (HTTP {error.status}). Check your API key.",
                        code="unauthorized",
                    ) from error
                raise HaruClientError(f"Could not connect to {self.ws_url}: HTTP {error.status}") from error
            except (aiohttp.ClientError, OSError) as error:
                raise HaruClientError(f"Could not connect to {self.ws_url}: {error}") from error

            session_payload: dict[str, Any] = {
                "modalities": ["audio", "text"],
                "instructions": prompt_text,
                "input_audio_format": "pcm_f32le",
                "output_audio_format": "pcm16",
                "sample_rate_hz": SAMPLE_RATE,
                "audio": {"input": {"sample_rate_hz": SAMPLE_RATE}, "output": {"sample_rate_hz": SAMPLE_RATE}},
                "turn_detection": None,
                "temperature": self.temperature,
                "extra_body": {
                    "auto_response": True,
                    "guidance_scale": self.guidance_scale,
                    "top_k": self.top_k,
                    "top_p": self.top_p,
                    "speak_first": self.speak_first,
                    "seed": self.seed,
                },
            }
            if ref_uri:
                session_payload["ref_audio"] = ref_uri

            await self._ws.send_json({"type": "session.update", "session": session_payload})

            async def _wait_created() -> None:
                assert self._ws is not None
                async for msg in self._ws:
                    if msg.type == aiohttp.WSMsgType.TEXT:
                        ev = json.loads(msg.data)
                        kind = ev.get("type")
                        if kind == "session.created":
                            return
                        if kind == "error":
                            raise self._error_from_event(ev)
                    elif msg.type in (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                        break
                raise HaruConnectionLostError("Server closed the socket before session.created.")

            await asyncio.wait_for(_wait_created(), timeout=self.connect_timeout)
            self.stats.connect_seconds = time.perf_counter() - t0

            # Start reader and send initial silence frame to run prompt immediately (C2: prompt warm before speech).
            self._reader_task = asyncio.create_task(self._read_loop())
            t_prompt = time.perf_counter()
            await self._send_frame(np.zeros(FRAME_SAMPLES, dtype=np.float32), count_stat=False)
            first_item = await asyncio.wait_for(self._output_queue.get(), timeout=self.connect_timeout)
            if isinstance(first_item, Exception):
                raise first_item
            if first_item is None:
                raise HaruConnectionLostError("Server closed during prompt execution.")
            self.stats.prompt_seconds = time.perf_counter() - t_prompt
            self.stats.started_at = time.perf_counter()
            self._connected = True

            # Enqueue optional preroll notice first, then frame 0 if speak_first produced speech.
            if self.preroll_spec is not None:
                preroll_chunks = await asyncio.to_thread(self._load_preroll_chunks, self.preroll_spec)
                for chunk in preroll_chunks:
                    self._output_queue.put_nowait(chunk)
            if self.speak_first:
                if self.record_dir is not None:
                    self._rec_caller.append(np.zeros(FRAME_SAMPLES, dtype=np.float32))
                    self._rec_model.append(pcm16_to_f32(first_item.pcm16))
                    if first_item.text:
                        self._rec_lines.append(f"[00:00.0] {first_item.text}")
                self._output_queue.put_nowait(first_item)

            if start_clock:
                self.start_clock()
            return self
        except BaseException:
            await self.close()
            raise

    @staticmethod
    def _load_preroll_chunks(preroll: str | Path | bytes | np.ndarray) -> list[OutputChunk]:
        if isinstance(preroll, (str, Path)):
            audio = decode_audio_bytes(Path(preroll).read_bytes(), SAMPLE_RATE)
        elif isinstance(preroll, (bytes, bytearray)):
            audio = decode_audio_bytes(bytes(preroll), SAMPLE_RATE)
        else:
            audio = np.asarray(preroll, dtype=np.float32).reshape(-1)
        if audio.size == 0:
            return []
        n_frames = -(-audio.size // FRAME_SAMPLES)
        padded = np.pad(audio, (0, n_frames * FRAME_SAMPLES - audio.size))
        return [
            OutputChunk(
                pcm16=f32_to_pcm16_bytes(padded[i * FRAME_SAMPLES : (i + 1) * FRAME_SAMPLES]),
                is_preroll=True,
                frame_index=i,
            )
            for i in range(n_frames)
        ]

    def start_clock(self) -> None:
        if self._clock_task is None:
            self._clock_task = asyncio.create_task(self._clock_loop())

    async def _send_frame(self, samples_f32: np.ndarray, *, count_stat: bool = True) -> None:
        if self._ws is None or self._ws.closed:
            raise HaruConnectionLostError("WebSocket is closed.")
        pcm = np.asarray(samples_f32, dtype="<f4").reshape(FRAME_SAMPLES)
        if count_stat:
            if self.interrupt_policy == "mute" and dbfs_rms(pcm) > self.caller_speech_dbfs:
                self._muted_phrase = True
                self._quiet_model_frames = 0
            if self.record_dir is not None:
                self._rec_caller.append(pcm.copy())
            self._sent_times.append(time.perf_counter())
            self.stats.frames_sent += 1
        await self._ws.send_json({
            "type": "input_audio_buffer.append",
            "audio": base64.b64encode(pcm.tobytes()).decode("ascii"),
            "format": "pcm_f32le",
            "sample_rate_hz": SAMPLE_RATE,
        })

    async def _clock_loop(self) -> None:
        try:
            # If preroll was queued, pace preroll playback before ticking the live conversation clock.
            if self.preroll_spec is not None:
                preroll_frames = sum(
                    1 for item in self._output_queue._queue if isinstance(item, OutputChunk) and item.is_preroll
                )
                if preroll_frames > 0:
                    await asyncio.sleep(preroll_frames * FRAME_SECONDS)
            t0 = time.perf_counter()
            tick = 0
            while not self._closing:
                target = t0 + tick * FRAME_SECONDS
                wait = target - time.perf_counter()
                if wait > 0:
                    await asyncio.sleep(wait)
                if self._closing:
                    break
                frame = self._pop_caller_frame()
                await self._send_frame(frame, count_stat=True)
                tick += 1
        except asyncio.CancelledError:
            return
        except Exception as error:  # noqa: BLE001
            if not self._closing:
                self._output_queue.put_nowait(
                    error if isinstance(error, HaruClientError) else HaruConnectionLostError(str(error))
                )

    async def _read_loop(self) -> None:
        assert self._ws is not None
        frame_idx = 0
        try:
            async for msg in self._ws:
                if msg.type != aiohttp.WSMsgType.TEXT:
                    if msg.type in (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                        break
                    continue
                ev = json.loads(msg.data)
                kind = ev.get("type")
                if kind == "response.output_audio_transcript.delta":
                    delta = str(ev.get("delta") or "")
                    if delta:
                        self._pending_text.append(delta)
                        self.stats.transcript_parts.append(delta)
                        if self.on_transcript is not None:
                            try:
                                self.on_transcript(delta)
                            except Exception:  # noqa: BLE001
                                pass
                elif kind == "response.output_audio.delta":
                    now = time.perf_counter()
                    raw_pcm16 = base64.b64decode(ev["delta"])
                    model_f32 = pcm16_to_f32(raw_pcm16)
                    if frame_idx > 0:  # frame 0 is the pre-warm prompt frame
                        self.stats.frames_received += 1
                        if self._sent_times:
                            self.stats.frame_latencies_s.append(max(0.0, now - self._sent_times.popleft()))
                        if self.interrupt_policy == "mute" and self._muted_phrase:
                            if dbfs_rms(model_f32) < -60.0:
                                self._quiet_model_frames += 1
                                if self._quiet_model_frames >= 2:
                                    self._muted_phrase = False
                            else:
                                self._quiet_model_frames = 0
                            raw_pcm16 = b"\x00" * len(raw_pcm16)
                            model_f32 = np.zeros_like(model_f32)
                        if self.record_dir is not None:
                            self._rec_model.append(model_f32)
                    text = "".join(self._pending_text)
                    self._pending_text.clear()
                    if frame_idx > 0 and text and self.record_dir is not None:
                        ts = (self.stats.frames_received * FRAME_SECONDS)
                        self._rec_lines.append(f"[{int(ts // 60):02d}:{ts % 60:04.1f}] {text}")
                    self._output_queue.put_nowait(OutputChunk(pcm16=raw_pcm16, text=text, frame_index=frame_idx))
                    frame_idx += 1
                elif kind == "error":
                    self._output_queue.put_nowait(self._error_from_event(ev))
                    return
                elif kind == "session.closed":
                    break
            if not self._closing:
                self._output_queue.put_nowait(HaruConnectionLostError("The Haru server closed the connection."))
        except asyncio.CancelledError:
            return
        except Exception as error:  # noqa: BLE001
            if not self._closing:
                self._output_queue.put_nowait(HaruConnectionLostError(str(error)))
        finally:
            self._output_queue.put_nowait(None)

    async def next_chunk(self) -> OutputChunk | None:
        """Await the next output frame; returns ``None`` on clean close or raises :class:`HaruClientError`."""
        item = await self._output_queue.get()
        if isinstance(item, Exception):
            raise item
        return item

    async def iter_output(self):
        """Async iterator yielding :class:`OutputChunk` until the session ends."""
        while True:
            chunk = await self.next_chunk()
            if chunk is None:
                return
            yield chunk

    def _write_recording(self) -> None:
        if self.record_dir is None or (not self._rec_caller and not self._rec_model):
            return
        self.record_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S") + "_" + secrets.token_hex(3)
        wav_path = self.record_dir / f"call_{stamp}.wav"
        txt_path = self.record_dir / f"call_{stamp}.txt"

        caller = np.concatenate(self._rec_caller) if self._rec_caller else np.zeros(0, dtype=np.float32)
        model = np.concatenate(self._rec_model) if self._rec_model else np.zeros(0, dtype=np.float32)
        total = max(caller.size, model.size)
        if total > 0:
            if caller.size < total:
                caller = np.pad(caller, (0, total - caller.size))
            if model.size < total:
                model = np.pad(model, (0, total - model.size))
            stereo = np.stack((caller, model), axis=1)
            pcm16 = np.clip(np.round(stereo * 32767.0), -32768, 32767).astype("<i2")
            with wave.open(str(wav_path), "wb") as wf:
                wf.setnchannels(2)
                wf.setsampwidth(2)
                wf.setframerate(SAMPLE_RATE)
                wf.writeframes(pcm16.tobytes())
            self.stats.recording_wav = wav_path

        body = [f"# {self.stats.summary_line()}", ""]
        body.extend(self._rec_lines)
        if self.stats.transcript:
            body.extend(["", "# Full transcript", self.stats.transcript])
        txt_path.write_text("\n".join(body) + "\n", encoding="utf-8")
        self.stats.recording_txt = txt_path

    async def close(self) -> CallStats:
        """Close the session cleanly, save any call recording, and log call telemetry."""
        if self._closing:
            return self.stats
        self._closing = True
        if self.stats.started_at and not self.stats.ended_at:
            self.stats.ended_at = time.perf_counter()

        if self._clock_task is not None:
            self._clock_task.cancel()
            try:
                await self._clock_task
            except asyncio.CancelledError:
                pass
            self._clock_task = None

        if self._ws is not None and not self._ws.closed:
            try:
                await self._ws.send_json({"type": "session.close"})
            except Exception:  # noqa: BLE001
                pass
            try:
                await self._ws.close()
            except Exception:  # noqa: BLE001
                pass

        if self._reader_task is not None:
            self._reader_task.cancel()
            try:
                await self._reader_task
            except asyncio.CancelledError:
                pass
            self._reader_task = None

        if self._http is not None and not self._http.closed:
            await self._http.close()
        self._http = None

        if self._connected:
            await asyncio.to_thread(self._write_recording)
            logger.info("haru-kit call finished: %s", self.stats.summary_line())
        return self.stats

    async def __aenter__(self) -> HaruSession:
        return await self.connect()

    async def __aexit__(self, *_: object) -> None:
        await self.close()
