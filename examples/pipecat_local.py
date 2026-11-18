"""Talk to Haru-4B from your computer's microphone and headphones using Pipecat.

Setup:
    # macOS also needs PortAudio for PyAudio: brew install portaudio
    uv pip install "haru-kit[pipecat]" "pipecat-ai[local]"

Run:
    # Terminal 1: haru-kit serve --on mac   (or --on modal)
    HARU_URL=ws://127.0.0.1:8000 python examples/pipecat_local.py
"""

from __future__ import annotations

import asyncio
import os

from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import WorkerRunner
from pipecat.transports.local.audio import LocalAudioTransport, LocalAudioTransportParams

from haru_kit.pipecat import HaruLLMService, create_haru_worker


async def main() -> None:
    transport = LocalAudioTransport(
        LocalAudioTransportParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
            audio_in_sample_rate=24000,
            audio_out_sample_rate=24000,
        )
    )
    haru = HaruLLMService(
        url=os.environ.get("HARU_URL", "ws://127.0.0.1:8000"),
        api_key=os.environ.get("HARU_API_KEY"),
        character=os.environ.get("HARU_CHARACTER", "haru"),
        language=os.environ.get("HARU_LANG", "en"),
        record_dir=os.environ.get("HARU_RECORD_DIR"),
    )
    worker = create_haru_worker(Pipeline([transport.input(), haru, transport.output()]))
    runner = WorkerRunner()
    await runner.add_workers(worker)
    await runner.run()


if __name__ == "__main__":
    asyncio.run(main())
