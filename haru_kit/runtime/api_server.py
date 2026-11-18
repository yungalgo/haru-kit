"""WebSocket API server speaking ``/v1/realtime?duplex=1`` around any Haru ``Backend``.

Runs inside a ``haru-kit`` environment (imports only the standard library, ``aiohttp``,
``numpy``, ``soundfile``, and upstream ``haru`` / ``haru_mlx``) or in-process with a fake
backend in tests.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import binascii
import hmac
import io
import json
import math
import signal
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from typing import Any

import aiohttp
import numpy as np
from aiohttp import web

SAMPLE_RATE = 24_000
FRAME_SAMPLES = 3200
MAX_REFERENCE_SECONDS = 180.0
MAX_WS_MESSAGE_BYTES = 16 * 1024 * 1024
_SILENCE_DBFS = -45.0
_SILENCE_MARGIN_SECONDS = 0.2


@dataclass
class DefaultSessionSettings:
    """Fallback settings dataclass when used with a standalone or fake backend."""

    system_prompt: str = ""
    reference: np.ndarray | None = None
    guidance_scale: float = 1.5
    temperature: float = 0.7
    top_k: int = 25
    top_p: float = 1.0
    speak_first: bool = False
    max_frames: int = 27_000
    seed: int | None = None


def emit(**event: Any) -> None:
    print(json.dumps(event), flush=True)


def _resample_mono(waveform: np.ndarray, source_rate: int, target_rate: int = SAMPLE_RATE) -> np.ndarray:
    if source_rate == target_rate or waveform.size == 0:
        return waveform.astype(np.float32, copy=False)
    try:
        from scipy.signal import resample_poly

        g = math.gcd(source_rate, target_rate)
        return resample_poly(waveform, target_rate // g, source_rate // g).astype(np.float32)
    except ImportError:
        duration = waveform.shape[0] / float(source_rate)
        target_len = max(1, int(round(duration * target_rate)))
        src_idx = np.linspace(0.0, waveform.shape[0] - 1, num=waveform.shape[0], dtype=np.float64)
        dst_idx = np.linspace(0.0, waveform.shape[0] - 1, num=target_len, dtype=np.float64)
        return np.interp(dst_idx, src_idx, waveform).astype(np.float32)


def decode_audio_bytes(data: bytes, sample_rate: int = SAMPLE_RATE) -> np.ndarray:
    import soundfile as sf

    try:
        waveform, src_rate = sf.read(io.BytesIO(data), dtype="float32", always_2d=True)
        mono = waveform.mean(axis=1)
        return _resample_mono(mono, int(src_rate), sample_rate)
    except Exception:
        with tempfile.NamedTemporaryFile(suffix=".audio") as source:
            source.write(data)
            source.flush()
            cmd = [
                "ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-i", source.name,
                "-f", "s16le", "-acodec", "pcm_s16le", "-ac", "1", "-ar", str(sample_rate), "pipe:1",
            ]
            try:
                res = subprocess.run(cmd, capture_output=True, timeout=60, check=False)
            except FileNotFoundError as error:
                raise ValueError("unsupported audio format (install ffmpeg to decode it)") from error
        if res.returncode != 0:
            raise ValueError(res.stderr.decode("utf-8", "replace").strip() or "could not decode audio") from None
        return np.frombuffer(res.stdout, dtype="<i2").astype(np.float32) / 32768.0


def trim_and_cap_reference(audio: np.ndarray, sample_rate: int = SAMPLE_RATE) -> np.ndarray:
    audio = np.asarray(audio, dtype=np.float32).reshape(-1)
    window = sample_rate // 50
    count = audio.shape[0] // window
    if count > 0:
        rms = np.sqrt(np.mean(audio[: count * window].reshape(count, window) ** 2, axis=1) + 1e-12)
        voiced = np.flatnonzero(20.0 * np.log10(rms) > _SILENCE_DBFS)
        if voiced.size == 0:
            return audio[:0]
        margin = int(_SILENCE_MARGIN_SECONDS * sample_rate)
        start = max(0, int(voiced[0]) * window - margin)
        end = min(audio.shape[0], (int(voiced[-1]) + 1) * window + margin)
        audio = audio[start:end]
    return audio[-int(MAX_REFERENCE_SECONDS * sample_rate) :]


def decode_ref_audio_uri(ref_audio: str | None) -> np.ndarray | None:
    if not ref_audio:
        return None
    if not isinstance(ref_audio, str) or not ref_audio.startswith("data:") or ";base64," not in ref_audio:
        raise ValueError("ref_audio must be a base64 audio data URI")
    try:
        raw = base64.b64decode(ref_audio.split(";base64,", 1)[1], validate=True)
    except (ValueError, binascii.Error) as error:
        raise ValueError(f"invalid base64 in ref_audio: {error}") from error
    return trim_and_cap_reference(decode_audio_bytes(raw, SAMPLE_RATE), SAMPLE_RATE)


class ApiServer:
    """Serves ``/v1/realtime?duplex=1``, ``/health``, and ``/api/ready`` over ``aiohttp``."""

    def __init__(
        self,
        backend: Any,
        *,
        settings_cls: type[Any] = DefaultSessionSettings,
        api_key: str | None = None,
        max_sessions: int = 1,
        first_message_timeout: float = 10.0,
        idle_socket_timeout: float = 300.0,
    ) -> None:
        self.backend = backend
        self.settings_cls = settings_cls
        self.api_key = api_key or None
        self.max_sessions = max(1, int(max_sessions))
        self.first_message_timeout = first_message_timeout
        self.idle_socket_timeout = idle_socket_timeout
        self.active = 0
        self.opened = 0

    def _authorized(self, request: web.Request) -> bool:
        if not self.api_key:
            return True
        header = request.headers.get("Authorization", "")
        if not header.startswith("Bearer "):
            return False
        token = header[len("Bearer ") :].strip()
        return hmac.compare_digest(token.encode("utf-8"), self.api_key.encode("utf-8"))

    async def handle_health(self, _request: web.Request) -> web.Response:
        if self.backend.ready():
            return web.json_response({"status": "ok"}, status=200)
        return web.json_response({"status": "loading"}, status=503)

    async def handle_ready(self, _request: web.Request) -> web.Response:
        return web.json_response({"ready": bool(self.backend.ready())})

    @staticmethod
    async def _send_error(ws: web.WebSocketResponse, code: str, message: str) -> None:
        if not ws.closed:
            await ws.send_json({"type": "error", "error": {"code": code, "message": message}})

    def _parse_session_settings(self, session: dict[str, Any]) -> tuple[Any, dict[str, Any]]:
        if not isinstance(session, dict):
            raise ValueError("session must be an object")
        in_fmt = session.get("input_audio_format", "pcm_f32le")
        out_fmt = session.get("output_audio_format", "pcm16")
        sr = session.get("sample_rate_hz", SAMPLE_RATE)
        if in_fmt != "pcm_f32le":
            raise ValueError(f"unsupported input_audio_format: {in_fmt!r}")
        if out_fmt != "pcm16":
            raise ValueError(f"unsupported output_audio_format: {out_fmt!r}")
        if int(sr) != SAMPLE_RATE:
            raise ValueError(f"sample_rate_hz must be {SAMPLE_RATE}, got {sr!r}")

        extra = session.get("extra_body") or {}
        if not isinstance(extra, dict):
            raise ValueError("extra_body must be an object")

        guidance = float(extra.get("guidance_scale", 1.5))
        if not (1.0 <= guidance <= 2.0):
            raise ValueError(f"guidance_scale must be within [1.0, 2.0], got {guidance}")

        temperature = float(session.get("temperature", 0.7))
        top_k = int(extra.get("top_k", 25))
        top_p = float(extra.get("top_p", 1.0))
        speak_first = bool(extra.get("speak_first", False))
        seed_raw = extra.get("seed")
        seed = None if seed_raw is None else int(seed_raw)
        instructions = str(session.get("instructions") or "")
        reference = decode_ref_audio_uri(session.get("ref_audio"))

        settings = self.settings_cls(
            system_prompt=instructions,
            reference=reference,
            guidance_scale=guidance,
            temperature=temperature,
            top_k=top_k,
            top_p=top_p,
            speak_first=speak_first,
            seed=seed,
        )
        echoed = dict(session)
        echoed.setdefault("instructions", instructions)
        return settings, echoed

    async def handle_realtime(self, request: web.Request) -> web.StreamResponse:
        if not self._authorized(request):
            return web.json_response(
                {"error": {"code": "unauthorized", "message": "Invalid or missing Bearer API key"}},
                status=401,
            )

        ws = web.WebSocketResponse(max_msg_size=MAX_WS_MESSAGE_BYTES, heartbeat=20)
        await ws.prepare(request)

        if not self.backend.ready():
            await self._send_error(ws, "server_not_ready", "model is still loading")
            await ws.close()
            return ws

        if self.active >= self.max_sessions:
            await self._send_error(ws, "resource_exhausted", "server is at capacity")
            await ws.close()
            return ws

        self.active += 1
        self.opened += 1
        conversation = None
        initial_session: dict[str, Any] | None = None
        started = False
        pcm_buf = np.zeros(0, dtype=np.float32)

        try:
            # First message must be session.update within first_message_timeout
            try:
                first_msg = await asyncio.wait_for(ws.receive(), timeout=self.first_message_timeout)
            except TimeoutError:
                await self._send_error(ws, "session_timeout", "timed out waiting for session.update")
                await ws.close()
                return ws

            if first_msg.type in (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.CLOSING, aiohttp.WSMsgType.CLOSED):
                return ws
            if first_msg.type != aiohttp.WSMsgType.TEXT:
                await self._send_error(ws, "bad_event", "expected JSON text message")
                await ws.close()
                return ws

            try:
                first_ev = json.loads(first_msg.data)
            except ValueError:
                await self._send_error(ws, "bad_event", "invalid JSON")
                await ws.close()
                return ws

            if not isinstance(first_ev, dict) or first_ev.get("type") != "session.update":
                await self._send_error(ws, "bad_event", "first message must be session.update")
                await ws.close()
                return ws

            try:
                settings, initial_session = self._parse_session_settings(first_ev.get("session") or {})
                conversation = await self.backend.open(settings)
            except ValueError as error:
                code = "invalid_ref_audio" if "ref_audio" in str(error) else "bad_event"
                await self._send_error(ws, code, str(error))
                await ws.close()
                return ws
            except Exception as error:  # noqa: BLE001
                await self._send_error(ws, "internal_error", str(error))
                await ws.close()
                return ws

            await ws.send_json({"type": "session.created", "session": initial_session})
            await ws.send_json({"type": "session.updated", "session": initial_session})

            while not ws.closed:
                try:
                    msg = await asyncio.wait_for(ws.receive(), timeout=self.idle_socket_timeout)
                except TimeoutError:
                    await self._send_error(ws, "idle_timeout", "session idle timeout reached")
                    break

                if msg.type in (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.CLOSING, aiohttp.WSMsgType.CLOSED):
                    break
                if msg.type == aiohttp.WSMsgType.ERROR:
                    break
                if msg.type != aiohttp.WSMsgType.TEXT:
                    await self._send_error(ws, "bad_event", "expected JSON text message")
                    continue

                try:
                    ev = json.loads(msg.data)
                except ValueError:
                    await self._send_error(ws, "bad_event", "invalid JSON")
                    continue
                if not isinstance(ev, dict):
                    await self._send_error(ws, "bad_event", "event must be an object")
                    continue

                kind = ev.get("type")
                if kind == "session.close":
                    await ws.send_json({"type": "session.closed"})
                    break

                if kind == "session.update":
                    upd = ev.get("session")
                    if not isinstance(upd, dict):
                        await self._send_error(ws, "bad_event", "session must be an object")
                        continue
                    prev_ref = (initial_session or {}).get("ref_audio")
                    if "ref_audio" in upd and upd.get("ref_audio") and upd.get("ref_audio") != prev_ref:
                        await self._send_error(
                            ws,
                            "ref_audio_update_unsupported",
                            "ref_audio cannot be updated after the session is created",
                        )
                        continue
                    if "ref_audio" in upd and upd.get("ref_audio") and prev_ref:
                        await self._send_error(
                            ws,
                            "ref_audio_update_unsupported",
                            "ref_audio cannot be updated after the session is created",
                        )
                        continue
                    prev_instr = str((initial_session or {}).get("instructions") or "")
                    if "instructions" in upd and str(upd.get("instructions") or "") != prev_instr:
                        await self._send_error(
                            ws,
                            "instructions_update_unsupported",
                            "instructions cannot be changed after the session is created",
                        )
                        continue
                    await ws.send_json({"type": "session.updated", "session": initial_session})
                    continue

                if kind == "input_audio_buffer.append":
                    fmt = ev.get("format", "pcm_f32le")
                    sr = ev.get("sample_rate_hz", SAMPLE_RATE)
                    audio_b64 = ev.get("audio")
                    if fmt != "pcm_f32le":
                        await self._send_error(ws, "bad_event", f"format must be 'pcm_f32le', got {fmt!r}")
                        continue
                    if sr != SAMPLE_RATE:
                        await self._send_error(ws, "bad_event", f"sample_rate_hz must be {SAMPLE_RATE}, got {sr!r}")
                        continue
                    if not isinstance(audio_b64, str):
                        await self._send_error(ws, "bad_event", "audio must be a base64 string")
                        continue
                    try:
                        raw = base64.b64decode(audio_b64, validate=True)
                    except (ValueError, binascii.Error):
                        await self._send_error(ws, "bad_event", "invalid base64 audio payload")
                        continue
                    if len(raw) % 4 != 0:
                        await self._send_error(ws, "bad_event", "pcm_f32le byte length must be a multiple of 4")
                        continue

                    samples = np.frombuffer(raw, dtype="<f4").astype(np.float32, copy=True)
                    if samples.size == 0:
                        continue
                    pcm_buf = samples if pcm_buf.size == 0 else np.concatenate((pcm_buf, samples))

                    session_done = False
                    while pcm_buf.size >= FRAME_SAMPLES:
                        chunk = pcm_buf[:FRAME_SAMPLES]
                        pcm_buf = pcm_buf[FRAME_SAMPLES:]
                        if not started:
                            started = True
                            frame = await conversation.start()
                        else:
                            frame = await conversation.step(chunk)
                        out_pcm16 = np.clip(np.round(np.asarray(frame.audio, dtype=np.float32) * 32767.0),
                                            -32768, 32767).astype("<i2")
                        await ws.send_json({
                            "type": "response.output_audio_transcript.delta",
                            "delta": getattr(frame, "text", "") or "",
                        })
                        await ws.send_json({
                            "type": "response.output_audio.delta",
                            "delta": base64.b64encode(out_pcm16.tobytes()).decode("ascii"),
                        })
                        if getattr(frame, "done", False):
                            await ws.send_json({"type": "session.closed"})
                            session_done = True
                            break
                    if session_done:
                        break
                    continue

                # Ignore unknown optional client events cleanly
        finally:
            self.active -= 1
            if conversation is not None:
                try:
                    await conversation.close()
                except Exception:  # noqa: BLE001
                    pass
            if not ws.closed:
                await ws.close()
        return ws

    def build_app(self) -> web.Application:
        app = web.Application()
        app.router.add_get("/health", self.handle_health)
        app.router.add_get("/api/ready", self.handle_ready)
        app.router.add_get("/v1/realtime", self.handle_realtime)
        return app


def _make_backend(args: argparse.Namespace) -> tuple[Any, type[Any]]:
    if args.engine == "mlx":
        from haru_mlx.backends import LocalBackend, SessionSettings

        quantize = 8 if args.quantize == "8" else None
        return LocalBackend(args.model, quantize=quantize), SessionSettings
    if args.engine == "torch":
        from haru.backends import LocalBackend, SessionSettings

        codec = args.codec_backend
        if codec == "auto":
            try:
                import triton  # noqa: F401

                codec = "triton"
            except ImportError:
                codec = "torch"
        return LocalBackend(args.model, codec_backend=codec), SessionSettings
    raise ValueError(f"unknown engine: {args.engine}")


async def _serve_cli(args: argparse.Namespace) -> int:
    emit(event="step", text="Loading weights and warming up")
    backend, settings_cls = _make_backend(args)
    server = ApiServer(
        backend,
        settings_cls=settings_cls,
        api_key=args.api_key,
        max_sessions=args.max_sessions,
    )
    app = server.build_app()
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    try:
        site = web.TCPSite(runner, args.host, args.port)
        await site.start()
    except OSError as error:
        await runner.cleanup()
        emit(
            event="error",
            what=f"Port {args.port} is already in use.",
            why=str(error),
            fix=f"Stop the other program on port {args.port}, or pass --port {args.port + 1}.",
        )
        return 1

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig_name in ("SIGINT", "SIGTERM", "SIGBREAK"):
        sig = getattr(signal, sig_name, None)
        if sig is not None:
            try:
                loop.add_signal_handler(sig, stop_event.set)
            except NotImplementedError:
                signal.signal(sig, lambda *_: loop.call_soon_threadsafe(stop_event.set))

    try:
        await backend.load()
    except Exception as error:  # noqa: BLE001
        await runner.cleanup()
        emit(event="error", what="Haru could not load the model.", why=str(error), fix="Run haru-kit doctor.")
        return 1

    shown_host = "127.0.0.1" if args.host in ("0.0.0.0", "127.0.0.1", "localhost") else args.host
    emit(event="ready", url=f"ws://{shown_host}:{args.port}/v1/realtime?duplex=1")
    await stop_event.wait()
    await runner.cleanup()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--engine", choices=("mlx", "torch"), required=True)
    parser.add_argument("--model", default="higurashi-ai/Haru-4B")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--quantize", choices=("8", "none"), default="8")
    parser.add_argument("--codec-backend", choices=("auto", "torch", "triton"), default="auto")
    parser.add_argument("--max-sessions", type=int, default=1)
    parser.add_argument("--api-key", default=None)
    args = parser.parse_args()
    return asyncio.run(_serve_cli(args))


if __name__ == "__main__":
    sys.exit(main())
