"""Pipecat adapter for Haru-4B (`pip install "haru-kit[pipecat]"`).

Connects a Pipecat pipeline (local microphone, Twilio Media Streams, Telnyx Media Streams,
or WebRTC) to any Haru ``/v1/realtime?duplex=1`` server over :class:`~haru_kit.client.HaruSession`.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import numpy as np

try:
    from pipecat.frames.frames import (
        CancelFrame,
        EndFrame,
        ErrorFrame,
        Frame,
        InputAudioRawFrame,
        LLMServiceMetadataFrame,
        LLMTextFrame,
        SpeechOutputAudioRawFrame,
        StartFrame,
    )
    from pipecat.pipeline.base_pipeline import BasePipeline
    from pipecat.pipeline.worker import PipelineParams, PipelineWorker
    from pipecat.processors.frame_processor import FrameDirection
    from pipecat.services.llm_service import LLMService, LLMSettings
    from pipecat.turns.user_turn_strategies import ExternalUserTurnStrategies
except ImportError as error:  # pragma: no cover - surfaced clearly to users missing the extra
    raise ImportError(
        'Pipecat support requires the "pipecat" extra: uv pip install "haru-kit[pipecat]"'
    ) from error

from .client import SAMPLE_RATE, CallStats, HaruClientError, HaruSession, IdleGate, InterruptPolicy, ServerFullError

logger = logging.getLogger(__name__)


def haru_pipeline_params(**kwargs: Any) -> PipelineParams:
    """Recommended 24 kHz full-duplex :class:`PipelineParams` for Haru pipelines.

    Twilio and Telnyx frame serializers automatically convert 8 kHz phone audio to and
    from 24 kHz when these pipeline sample rates are set, with no extra resampler needed.
    """
    defaults: dict[str, Any] = {
        "audio_in_sample_rate": SAMPLE_RATE,
        "audio_out_sample_rate": SAMPLE_RATE,
        "allow_interruptions": False,
    }
    defaults.update(kwargs)
    return PipelineParams(**defaults)


def create_haru_worker(pipeline: BasePipeline, **kwargs: Any) -> PipelineWorker:
    """Create a :class:`PipelineWorker` configured for continuous full-duplex Haru audio."""
    params = kwargs.pop("params", None) or haru_pipeline_params()
    kwargs.setdefault("cancel_on_idle_timeout", False)
    return PipelineWorker(pipeline, params=params, **kwargs)


def _mono_pcm16(audio: bytes, num_channels: int) -> bytes:
    if num_channels <= 1 or not audio:
        return audio
    samples = np.frombuffer(audio, dtype="<i2")
    frames = samples.size // num_channels
    if frames == 0:
        return b""
    mono = samples[: frames * num_channels].reshape(frames, num_channels).mean(axis=1)
    return np.clip(np.round(mono), -32768, 32767).astype("<i2").tobytes()


class HaruLLMService(LLMService):
    """Full-duplex speech-to-speech Pipecat service backed by Haru-4B."""

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
        busy_audio: str | Path | bytes | np.ndarray | None = None,
        interrupt_policy: InterruptPolicy = "native",
        idle_gate_dbfs: float = -65.0,
        idle_gate_hold_frames: int = 2,
        record_dir: str | Path | None = None,
        connect_timeout: float = 180.0,
        **kwargs: Any,
    ) -> None:
        kwargs.setdefault(
            "settings",
            LLMSettings(
                model="Haru-4B",
                system_instruction=None,
                temperature=temperature,
                max_tokens=None,
                top_p=top_p,
                top_k=top_k,
                frequency_penalty=None,
                presence_penalty=None,
                seed=seed,
                filter_incomplete_user_turns=False,
                user_turn_completion_config=None,
            ),
        )
        super().__init__(**kwargs)
        self._url = url
        self._api_key = api_key
        self._card = card if card is not None else character
        self._voice = voice
        self._language = language
        self._library_dir = library_dir
        self._seed_registry = seed_registry
        self._guidance_scale = guidance_scale
        self._temperature = temperature
        self._top_k = top_k
        self._top_p = top_p
        self._speak_first = speak_first
        self._seed = seed
        self._preroll = preroll
        self._busy_audio = busy_audio
        self._interrupt_policy: InterruptPolicy = interrupt_policy
        self._idle_gate_dbfs = idle_gate_dbfs
        self._idle_gate_hold_frames = idle_gate_hold_frames
        self._record_dir = record_dir
        self._connect_timeout = connect_timeout

        self._session: HaruSession | None = None
        self._pump_task: asyncio.Task[None] | None = None
        self._gate = IdleGate(threshold_dbfs=idle_gate_dbfs, hold_frames=idle_gate_hold_frames)
        self.last_stats: CallStats | None = None

    def service_metadata_frame(self) -> LLMServiceMetadataFrame:
        """Advertise Haru as a continuous realtime service with framework interruptions disabled."""
        return LLMServiceMetadataFrame(
            service_name=self.name,
            is_realtime_service=True,
            user_turn_strategies=ExternalUserTurnStrategies(enable_interruptions=False),
        )

    async def start(self, frame: StartFrame) -> None:
        await super().start(frame)
        await self._connect_and_pump()

    async def stop(self, frame: EndFrame) -> None:
        await self._disconnect()
        await super().stop(frame)

    async def cancel(self, frame: CancelFrame) -> None:
        await self._disconnect()
        await super().cancel(frame)

    async def _connect_and_pump(self) -> None:
        if self._session is not None:
            return
        self._gate = IdleGate(threshold_dbfs=self._idle_gate_dbfs, hold_frames=self._idle_gate_hold_frames)
        session = HaruSession(
            self._url,
            api_key=self._api_key,
            card=self._card,
            voice=self._voice,
            language=self._language,
            library_dir=self._library_dir,
            seed_registry=self._seed_registry,
            guidance_scale=self._guidance_scale,
            temperature=self._temperature,
            top_k=self._top_k,
            top_p=self._top_p,
            speak_first=self._speak_first,
            seed=self._seed,
            preroll=self._preroll,
            interrupt_policy=self._interrupt_policy,
            record_dir=self._record_dir,
            connect_timeout=self._connect_timeout,
        )
        try:
            await session.connect(start_clock=True)
        except ServerFullError as error:
            if self._busy_audio is not None:
                for chunk in HaruSession._load_preroll_chunks(self._busy_audio):
                    await self.push_frame(
                        SpeechOutputAudioRawFrame(audio=chunk.pcm16, sample_rate=SAMPLE_RATE, num_channels=1)
                    )
            await self.push_error(ErrorFrame(error=str(error), fatal=True))
            return
        except HaruClientError as error:
            await self.push_error(ErrorFrame(error=str(error), fatal=True))
            return

        self._session = session
        self._pump_task = self.create_task(self._pump_output(session))

    async def _pump_output(self, session: HaruSession) -> None:
        try:
            async for chunk in session.iter_output():
                pcm16 = chunk.pcm16 if chunk.is_preroll else self._gate.apply(chunk.pcm16)
                await self.push_frame(
                    SpeechOutputAudioRawFrame(audio=pcm16, sample_rate=SAMPLE_RATE, num_channels=1)
                )
                if chunk.text:
                    await self.push_frame(LLMTextFrame(text=chunk.text))
        except asyncio.CancelledError:
            return
        except HaruClientError as error:
            await self.push_error(ErrorFrame(error=str(error), fatal=True))
        except Exception as error:  # noqa: BLE001
            logger.exception("Haru Pipecat output stream failed")
            await self.push_error(ErrorFrame(error=str(error), fatal=True))

    async def _disconnect(self) -> None:
        if self._pump_task is not None:
            await self.cancel_task(self._pump_task)
            self._pump_task = None
        if self._session is not None:
            session, self._session = self._session, None
            self.last_stats = await session.close()

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        if isinstance(frame, InputAudioRawFrame) and self._session is not None:
            mono = _mono_pcm16(frame.audio, getattr(frame, "num_channels", 1))
            self._session.push_audio(mono, sample_rate=frame.sample_rate, fmt="pcm16")
        await self.push_frame(frame, direction)


# Alias matching SPEC.md §3.2 (`from haru_kit.pipecat import HaruService`)
HaruService = HaruLLMService

__all__ = ["HaruLLMService", "HaruService", "IdleGate", "create_haru_worker", "haru_pipeline_params"]
