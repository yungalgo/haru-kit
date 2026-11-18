"""LiveKit Agents adapter for Haru-4B (`pip install "haru-kit[livekit]"`).

Implements :class:`livekit.agents.llm.DuplexModel` and :class:`~livekit.agents.llm.DuplexSession`
for models that listen and speak simultaneously and manage their own turn-taking:

    session = AgentSession(
        llm=HaruModel(url="wss://...", api_key="...", character="haru"),
        aec_warmup_duration=None,
    )
"""

from __future__ import annotations

import logging
import time
from collections.abc import AsyncIterable, Mapping
from pathlib import Path
from typing import Any

import numpy as np

try:
    from livekit import rtc
    from livekit.agents import llm
    from livekit.agents.types import NOT_GIVEN, NotGivenOr
except ImportError as error:  # pragma: no cover - surfaced clearly to users missing the extra
    raise ImportError(
        'LiveKit support requires the "livekit" extra: uv pip install "haru-kit[livekit]"'
    ) from error

from .client import (
    FRAME_SAMPLES,
    FRAME_SECONDS,
    SAMPLE_RATE,
    CallStats,
    HaruClientError,
    HaruSession,
    InterruptPolicy,
)

logger = logging.getLogger(__name__)


def _rtc_frame_to_mono_pcm16(frame: rtc.AudioFrame) -> bytes:
    raw = bytes(frame.data)
    channels = int(frame.num_channels)
    if channels <= 1 or not raw:
        return raw
    samples = np.frombuffer(raw, dtype="<i2")
    frames = samples.size // channels
    if frames == 0:
        return b""
    mono = samples[: frames * channels].reshape(frames, channels).mean(axis=1)
    return np.clip(np.round(mono), -32768, 32767).astype("<i2").tobytes()


class HaruModel(llm.DuplexModel):
    """Full-duplex Haru-4B speech model for ``livekit-agents``."""

    def __init__(
        self,
        *,
        url: str,
        api_key: str | None = None,
        card: str | Path | Mapping[str, Any] | None = None,
        character: str | Path | Mapping[str, Any] | None = None,
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
        record_dir: str | Path | None = None,
        connect_timeout: float = 180.0,
    ) -> None:
        super().__init__(
            capabilities=llm.DuplexCapabilities(
                user_transcription=False,
                auto_tool_reply_generation=False,
                mutable_chat_context=False,
                mutable_instructions=False,
                mutable_tools=False,
            )
        )
        self.url = url
        self.api_key = api_key
        self.card = card if card is not None else character
        self.voice = voice
        self.language = language
        self.library_dir = library_dir
        self.seed_registry = seed_registry
        self.guidance_scale = guidance_scale
        self.temperature = temperature
        self.top_k = top_k
        self.top_p = top_p
        self.speak_first = speak_first
        self.seed = seed
        self.preroll = preroll
        self.interrupt_policy: InterruptPolicy = interrupt_policy
        self.record_dir = record_dir
        self.connect_timeout = connect_timeout
        self._sessions: set[HaruDuplexSession] = set()

    @property
    def model(self) -> str:
        return "Haru-4B"

    @property
    def provider(self) -> str:
        return "haru-kit"

    def session(self) -> HaruDuplexSession:
        sess = HaruDuplexSession(self)
        self._sessions.add(sess)
        return sess

    async def aclose(self) -> None:
        for sess in list(self._sessions):
            await sess.aclose()
        self._sessions.clear()


class HaruDuplexSession(llm.DuplexSession):
    """One live full-duplex LiveKit session connected to Haru-4B."""

    def __init__(self, model: HaruModel) -> None:
        super().__init__(model)
        self._haru_model = model
        self._tools = llm.ToolContext.empty()
        self._instructions: str | None = None
        self._haru: HaruSession | None = None
        self._preconnect_audio: list[tuple[bytes, int]] = []
        self.last_stats: CallStats | None = None

    @property
    def tools(self) -> llm.ToolContext:
        return self._tools

    @property
    def audio_stream(self) -> AsyncIterable[llm.DuplexAudioFrame]:
        return self._stream_output()

    def push_audio(self, frame: rtc.AudioFrame) -> None:
        if self._closing:
            return
        mono = _rtc_frame_to_mono_pcm16(frame)
        if not mono:
            return
        if self._haru is not None:
            self._haru.push_audio(mono, sample_rate=int(frame.sample_rate), fmt="pcm16")
        else:
            # Keep at most ~0.5s of pre-connect frames while handshake finishes.
            self._preconnect_audio.append((mono, int(frame.sample_rate)))
            if len(self._preconnect_audio) > 20:
                self._preconnect_audio.pop(0)

    async def _update_instructions(self, instructions: str) -> None:
        text = instructions.strip()
        if text:
            self._instructions = text

    async def _append_items(self, items: list[llm.ChatItem]) -> None:
        _ = items

    async def _update_tools(self, tools: list[llm.Tool]) -> None:
        if tools:
            logger.warning("Haru-4B does not support tool calling; ignoring %d tool(s)", len(tools))

    def _update_options(self, *, tool_choice: NotGivenOr[llm.ToolChoice | None] = NOT_GIVEN) -> None:
        _ = tool_choice

    async def _stream_output(self) -> AsyncIterable[llm.DuplexAudioFrame]:
        await self._configured.wait()
        if self._closing:
            return

        chosen_card = self._haru_model.card
        if chosen_card is None and self._instructions:
            chosen_card = self._instructions

        session = HaruSession(
            self._haru_model.url,
            api_key=self._haru_model.api_key,
            card=chosen_card,
            voice=self._haru_model.voice,
            language=self._haru_model.language,
            library_dir=self._haru_model.library_dir,
            seed_registry=self._haru_model.seed_registry,
            guidance_scale=self._haru_model.guidance_scale,
            temperature=self._haru_model.temperature,
            top_k=self._haru_model.top_k,
            top_p=self._haru_model.top_p,
            speak_first=self._haru_model.speak_first,
            seed=self._haru_model.seed,
            preroll=self._haru_model.preroll,
            interrupt_policy=self._haru_model.interrupt_policy,
            record_dir=self._haru_model.record_dir,
            connect_timeout=self._haru_model.connect_timeout,
        )
        try:
            await session.connect(start_clock=True)
            self._haru = session
            self._report_connection_acquired(session.stats.connect_seconds)
            for mono, sr in self._preconnect_audio:
                session.push_audio(mono, sample_rate=sr, fmt="pcm16")
            self._preconnect_audio.clear()

            emitted_frames = 0
            async for chunk in session.iter_output():
                if self._closing:
                    break
                start_ms = round(emitted_frames * FRAME_SECONDS * 1000)
                end_ms = round((emitted_frames + 1) * FRAME_SECONDS * 1000)
                if chunk.text:
                    self.emit(
                        "transcript_delta",
                        llm.DuplexOutputTranscriptDelta(text=chunk.text, start_ms=start_ms, end_ms=end_ms),
                    )
                samples_per_ch = len(chunk.pcm16) // 2 or FRAME_SAMPLES
                rtc_frame = rtc.AudioFrame(
                    data=chunk.pcm16,
                    sample_rate=SAMPLE_RATE,
                    num_channels=1,
                    samples_per_channel=samples_per_ch,
                )
                yield llm.DuplexAudioFrame(frame=rtc_frame, start_ms=start_ms)
                emitted_frames += 1
        except HaruClientError as error:
            if not self._closing:
                self.emit(
                    "error",
                    llm.RealtimeModelError(
                        timestamp=time.time(),
                        label=self._duplex_model.label,
                        error=error,
                        recoverable=False,
                    ),
                )
        except Exception as error:  # noqa: BLE001
            if not self._closing:
                logger.exception("Haru LiveKit session failed")
                self.emit(
                    "error",
                    llm.RealtimeModelError(
                        timestamp=time.time(),
                        label=self._duplex_model.label,
                        error=error,
                        recoverable=False,
                    ),
                )
        finally:
            self._haru = None
            self.last_stats = await session.close()

    async def aclose(self) -> None:
        await super().aclose()
        self._haru_model._sessions.discard(self)
        if self._haru is not None:
            haru, self._haru = self._haru, None
            self.last_stats = await haru.close()


__all__ = ["HaruDuplexSession", "HaruModel"]
