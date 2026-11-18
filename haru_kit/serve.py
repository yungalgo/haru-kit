"""``haru-kit serve`` and ``haru-kit check``: run and verify the ``/v1/realtime`` API server."""

from __future__ import annotations

import asyncio
import os
import secrets
import time

from rich.markup import escape

from . import modal, steps, ui, ways
from .detect import collect
from .procs import Failure
from .settings import Settings


def _validate_port(port: int) -> bool:
    if 1 <= port <= 65535:
        return True
    ui.error(
        "Invalid port.",
        f"Port must be between 1 and 65535 (got {port}).",
        "Use a valid port, for example: haru-kit serve --port 8000",
    )
    return False


def serve_cmd(args) -> int:
    port = args.port if args.port is not None else 8000
    if not _validate_port(port):
        return 1
    if args.max_calls is not None and args.max_calls < 1:
        ui.error("Invalid --max-calls.", "Must be at least 1.", "Example: haru-kit serve --max-calls 2")
        return 1

    facts = collect()
    settings = Settings.load()
    if args.quantize:
        settings.quantize = args.quantize
    if args.region:
        settings.modal_region = args.region

    ui.header()
    ui.facts(facts)
    all_ways = ways.evaluate(facts)
    if args.on:
        way = next(w for w in all_ways if w.key == args.on)
        if not way.possible:
            ui.error(
                f"Haru cannot serve with “{ways.SHORT[way.key]}” on this computer.",
                way.reason.capitalize() + ".",
                "Run haru-kit doctor to see the ways that work here.",
            )
            return 1
    else:
        way = ways.recommended(all_ways) if args.yes else ui.choose_way(all_ways)

    from . import envs, model

    status = {"env": envs.installed(way.key, facts), "model": model.downloaded()}
    items = ways.plan(way.key, "browser" if way.key == "mac" else None, status)
    gpu_type = (args.gpu or modal.GPU).upper()
    if way.key == "modal":
        region = modal.choose_region(settings.modal_region)
        items[-1].text = f"Start a {gpu_type} GPU API server"
        items[-1].note = (
            f"{modal.region_text(region, settings.modal_region)} · about ${modal.price(region, gpu_type):.2f} an hour"
        )
    else:
        items[-1].text = f"Start the API server on port {port}"

    time_note = (
        "about 2–12 minutes (loads Haru on the GPU)"
        if way.key == "modal"
        else ("about 15 minutes on a fast connection" if not status["model"] else None)
    )
    if not all(item.done for item in items if item.folder is not None) or way.key == "modal":
        if not ui.plan(items, ways.space(items), time_note, resumable=way.key != "modal", os_name=facts.os):
            return 1
        if not args.yes and not ui.confirm("Start API server now?", yes="Continue", no="Cancel"):
            ui.console.print("  Nothing was changed.")
            return 0

    try:
        if way.key == "modal":
            api_key = args.api_key or ("hk_" + secrets.token_urlsafe(24))
            max_calls = args.max_calls or 2
            started = modal.start_tunnel(
                facts,
                settings,
                args.verbose,
                ask=not args.yes,
                function="serve_gateway",
                key=api_key,
                options={"mode": "serve", "max_sessions": max_calls},
                gpu_override=gpu_type,
                retry_cmd="haru-kit serve --on modal",
            )
            if started is None:
                return 0
            url, key, where, region = started
            ws_url = url.replace("https://", "wss://", 1).replace("http://", "ws://", 1) + "/v1/realtime?duplex=1"
            open_by = time.strftime("%H:%M", time.localtime(time.time() + modal.FIRST_IDLE_MINUTES * 60))
            ui.console.print()
            ui.done("Haru API server is ready", escape(ws_url))
            ui.console.print(f"    API key:   [bold]{escape(key)}[/]")
            ui.console.print(f"    Capacity:  {max_calls} concurrent call(s) · GPU: {gpu_type} in {escape(where)} "
                             f"(~${modal.price(region, gpu_type):.2f}/hr)")
            ui.console.print(
                f"    Idle stop: start a call by {open_by}, or {modal.IDLE_MINUTES} min after the last call"
            )
            ui.console.print()
            ui.console.print("  Check from your terminal:")
            ui.console.print(f"    haru-kit check {escape(url)} --api-key {escape(key)}")
            ui.console.print("  Stop now:")
            ui.console.print("    haru-kit stop")
            ui.console.print()
            return 0

        # Local Mac or NVIDIA API server
        steps.install_env(way.key, facts, args.verbose)
        steps.download_model(way.key, args.verbose)
        engine = "mlx" if way.key == "mac" else "torch"
        srv_args = [
            "--engine", engine,
            "--host", args.host,
            "--port", str(port),
            "--max-sessions", str(args.max_calls or 1),
        ]
        if way.key == "mac":
            srv_args += ["--quantize", settings.quantize]
        if args.api_key:
            srv_args += ["--api-key", args.api_key]

        runtime, ws_url = steps.start_server(way.key, srv_args, args.verbose, script="api_server.py")
        ui.console.print()
        ui.done("Haru API server is ready", escape(ws_url))
        if args.api_key:
            ui.console.print(f"    API key: {escape(args.api_key)}")
        check_line = f"haru-kit check {escape(ws_url)}"
        if args.api_key:
            check_line += f" --api-key {escape(args.api_key)}"
        ui.console.print(f"    Test it: {check_line}")
        ui.console.print("    Press Ctrl+C here to stop Haru.")
        ui.console.print()
        steps.wait_until_stopped(runtime)
        return 0
    except Failure as error:
        ui.console.print()
        ui.error(error.what, error.why, error.fix)
        return 1
    except KeyboardInterrupt:
        ui.console.print("\n  Stopped.")
        return 130


async def _run_check(
    url: str,
    *,
    api_key: str | None,
    card: str | None,
    voice: str | None,
    language: str,
    seconds: float,
) -> tuple[int, dict]:
    from . import card as card_mod
    from .client import FRAME_SAMPLES, SAMPLE_RATE, HaruClientError, HaruSession
    from .runtime.api_server import decode_audio_bytes

    caller_path = card_mod.check_caller_path()
    caller_pcm = decode_audio_bytes(caller_path.read_bytes(), SAMPLE_RATE)
    target_samples = max(FRAME_SAMPLES * 4, int(round(seconds * SAMPLE_RATE)))
    if caller_pcm.size < target_samples:
        repeats = -(-target_samples // max(1, caller_pcm.size))
        import numpy as np

        caller_pcm = np.tile(caller_pcm, repeats)[:target_samples]
    else:
        caller_pcm = caller_pcm[:target_samples]

    session = HaruSession(
        url,
        api_key=api_key,
        card=card,
        voice=voice,
        language=language,
        speak_first=True,
        seed=0,
    )
    try:
        await session.connect(start_clock=False)
        # Feed caller clip in real-time 40 ms increments while the 133.33 ms clock runs.
        session.start_clock()
        step_samples = SAMPLE_RATE // 25  # 40 ms = 960 samples
        t0 = time.perf_counter()
        idx = 0
        chunks_drained = 0

        async def _drain() -> None:
            nonlocal chunks_drained
            async for _ in session.iter_output():
                chunks_drained += 1

        drain_task = asyncio.create_task(_drain())
        while idx < caller_pcm.size:
            piece = caller_pcm[idx : idx + step_samples]
            session.push_audio(piece, sample_rate=SAMPLE_RATE, fmt="f32")
            idx += step_samples
            due = t0 + (idx / SAMPLE_RATE) - time.perf_counter()
            if due > 0:
                await asyncio.sleep(due)
        # Give the final frame round-trip up to 1.0s to arrive.
        deadline = time.perf_counter() + 1.0
        while session.stats.frames_received < session.stats.frames_sent and time.perf_counter() < deadline:
            await asyncio.sleep(0.05)
        stats = await session.close()
        drain_task.cancel()
        return 0, {
            "frames_sent": stats.frames_sent,
            "frames_received": stats.frames_received,
            "p50_ms": stats.frame_ms_p50,
            "p95_ms": stats.frame_ms_p95,
            "connect_s": round(stats.connect_seconds, 2),
            "prompt_s": round(stats.prompt_seconds, 2),
            "transcript": stats.transcript,
        }
    except HaruClientError as error:
        await session.close()
        return 1, {"error": str(error), "code": error.code}


def check_cmd(args) -> int:
    api_key = args.api_key or os.environ.get("HARU_API_KEY")
    with ui.Step(f"Checking {args.url}"):
        code, info = asyncio.run(
            _run_check(
                args.url,
                api_key=api_key,
                card=args.card,
                voice=args.voice,
                language=args.lang,
                seconds=args.seconds,
            )
        )
    if code != 0 or info.get("frames_received", 0) == 0:
        why = info.get("error") or f"Sent {info.get('frames_sent', 0)} frames, received 0 audio frames."
        fix = "Check that `haru-kit serve` is running and that `--api-key` matches if authentication is enabled."
        ui.error("Check failed.", why, fix)
        return 1

    ui.done(
        "OK",
        f"{info['frames_sent']} frames sent, {info['frames_received']} received · "
        f"frame time {info['p50_ms']:.1f} ms median, {info['p95_ms']:.1f} ms p95",
    )
    spoken = info["transcript"] or "(listening / no words spoken in this short window)"
    ui.console.print(f"    Spoken: {escape(spoken)}")
    return 0
