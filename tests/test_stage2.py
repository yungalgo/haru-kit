"""Stage 2 tests: API server, 133.33 ms clock client, check command, Twilio/Telnyx helpers,
and Pipecat / LiveKit adapters (when those extras are installed)."""

from __future__ import annotations

import asyncio
import base64
import importlib.util
import wave
from dataclasses import dataclass
from unittest import mock

import aiohttp
import numpy as np
import pytest
from aiohttp import web

from haru_kit import card, cli, phone, serve
from haru_kit.api_server import ApiServer
from haru_kit.client import (
    FRAME_SAMPLES,
    SAMPLE_RATE,
    HaruClientError,
    HaruSession,
    IdleGate,
    ServerFullError,
    encode_voice_uri,
    f32_to_pcm16_bytes,
    realtime_url,
)


@dataclass
class FakeFrame:
    audio: np.ndarray
    text: str = ""
    done: bool = False


class FakeConversation:
    def __init__(self, settings, *, speech_amp: float = 0.15, idle_after: int | None = None):
        self.settings = settings
        self.speech_amp = speech_amp
        self.idle_after = idle_after
        self.steps = 0
        self.closed = False

    async def start(self) -> FakeFrame:
        t = np.arange(FRAME_SAMPLES, dtype=np.float32) / SAMPLE_RATE
        tone = (self.speech_amp * np.sin(2.0 * np.pi * 220.0 * t)).astype(np.float32)
        text = "Hello! " if self.settings.speak_first else ""
        audio = tone if self.settings.speak_first else np.zeros(FRAME_SAMPLES, dtype=np.float32)
        return FakeFrame(audio=audio, text=text)

    async def step(self, user_audio: np.ndarray) -> FakeFrame:
        assert user_audio.shape == (FRAME_SAMPLES,)
        self.steps += 1
        if self.idle_after is not None and self.steps > self.idle_after:
            # Tiny idle noise (~ -78 dBFS, peaks ~4 out of 32768) like Haru's codec floor
            idle = np.full(FRAME_SAMPLES, 0.00012, dtype=np.float32)
            return FakeFrame(audio=idle, text="")
        t = np.arange(FRAME_SAMPLES, dtype=np.float32) / SAMPLE_RATE
        tone = (self.speech_amp * np.sin(2.0 * np.pi * 330.0 * t)).astype(np.float32)
        return FakeFrame(audio=tone, text=f"w{self.steps} ")

    async def close(self) -> None:
        self.closed = True


class FakeBackend:
    def __init__(self, *, ready: bool = True, speech_amp: float = 0.15, idle_after: int | None = None):
        self._ready = ready
        self.speech_amp = speech_amp
        self.idle_after = idle_after
        self.opened_settings = []

    async def load(self) -> None:
        self._ready = True

    def ready(self) -> bool:
        return self._ready

    async def open(self, settings) -> FakeConversation:
        self.opened_settings.append(settings)
        return FakeConversation(settings, speech_amp=self.speech_amp, idle_after=self.idle_after)


async def _start_test_api_server(backend: FakeBackend, **kwargs) -> tuple[web.AppRunner, str, ApiServer]:
    api = ApiServer(backend, **kwargs)
    runner = web.AppRunner(api.build_app())
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    return runner, f"http://127.0.0.1:{port}", api


# ---------------------------------------------------- URL & voice encoding


def test_realtime_url_normalization():
    assert realtime_url("http://127.0.0.1:8000") == "ws://127.0.0.1:8000/v1/realtime?duplex=1"
    assert realtime_url("https://demo.modal.host/") == "wss://demo.modal.host/v1/realtime?duplex=1"
    assert realtime_url("ws://localhost:8999/v1/realtime?duplex=1") == "ws://localhost:8999/v1/realtime?duplex=1"


def test_voice_uri_flac_and_modal_size_cap():
    uri = encode_voice_uri(card.check_voice_path())
    assert uri is not None and uri.startswith("data:audio/flac;base64,")
    with pytest.raises(HaruClientError) as exc:
        encode_voice_uri(card.check_voice_path(), max_uri_bytes=100)
    assert exc.value.code == "voice_too_large"


def test_idle_gate_holds_two_frames_then_zeroes():
    gate = IdleGate(threshold_dbfs=-65.0, hold_frames=2)
    loud = f32_to_pcm16_bytes(np.full(FRAME_SAMPLES, 0.1, dtype=np.float32))
    faint = f32_to_pcm16_bytes(np.full(FRAME_SAMPLES, 0.00012, dtype=np.float32))  # ~ -78 dBFS
    assert gate.apply(faint) == b"\x00" * len(faint)
    assert gate.apply(loud) == loud
    assert gate.apply(faint) == faint  # hold 1
    assert gate.apply(faint) == faint  # hold 2
    assert gate.apply(faint) == b"\x00" * len(faint)  # gated to exact silence


# ------------------------------------------- ApiServer & HaruSession clock


def test_client_clock_irregular_silence_burst_and_recording(tmp_path):
    async def run():
        backend = FakeBackend()
        runner, url, _ = await _start_test_api_server(backend)
        try:
            preroll = np.full(FRAME_SAMPLES, 0.05, dtype=np.float32)
            rec_dir = tmp_path / "recordings"
            session = HaruSession(
                url,
                card={"persona": "Friendly host.", "name": "Haru"},
                voice=card.check_voice_path(),
                seed_registry=False,
                speak_first=True,
                preroll=preroll,
                record_dir=rec_dir,
            )
            await session.connect(start_clock=True)

            # 1) Burst of 4 frames at once -> backlog exceeds 2 frames, oldest dropped
            burst = np.ones(FRAME_SAMPLES * 4, dtype=np.float32) * 0.02
            session.push_audio(burst, sample_rate=SAMPLE_RATE, fmt="f32")
            await asyncio.sleep(0.35)

            # 2) Irregular 20 ms chunks at 8 kHz (phone rate)
            chunk_8k = (np.ones(160, dtype=np.float32) * 0.03 * 32767).astype("<i2").tobytes()
            for _ in range(4):
                session.push_audio(chunk_8k, sample_rate=8000, fmt="pcm16")
                await asyncio.sleep(0.03)

            # 3) Stop sending audio completely -> clock pads silence and keeps receiving model frames
            await asyncio.sleep(0.35)

            received = []
            while not session._output_queue.empty():
                item = session._output_queue.get_nowait()
                if item is not None and not isinstance(item, Exception):
                    received.append(item)

            stats = await session.close()
            assert stats.frames_sent >= 4
            assert stats.frames_received >= 4
            assert stats.samples_dropped >= FRAME_SAMPLES  # burst trimmed to 2 frames
            assert stats.silence_samples_added > 0  # silence filled during pause
            assert any(c.is_preroll for c in received)
            assert "Hello!" in stats.transcript and "w1" in stats.transcript

            assert stats.recording_wav is not None and stats.recording_wav.is_file()
            with wave.open(str(stats.recording_wav), "rb") as wf:
                assert wf.getnchannels() == 2
                assert wf.getframerate() == SAMPLE_RATE
                assert wf.getnframes() >= FRAME_SAMPLES * 3
            assert stats.recording_txt is not None and stats.recording_txt.is_file()
        finally:
            await runner.cleanup()

    asyncio.run(run())


def test_api_server_auth_capacity_and_protocol_errors():
    async def run():
        backend = FakeBackend()
        runner, url, _ = await _start_test_api_server(backend, api_key="topsecret", max_sessions=1)
        try:
            # Missing / wrong key -> unauthorized
            with pytest.raises(HaruClientError) as bad_auth:
                await HaruSession(url, api_key="wrong", voice=card.check_voice_path(), seed_registry=False).connect()
            assert bad_auth.value.code == "unauthorized"

            # Open session 1 -> succeeds
            s1 = await HaruSession(
                url, api_key="topsecret", voice=card.check_voice_path(), seed_registry=False
            ).connect(start_clock=False)

            # Open session 2 while full -> ServerFullError (resource_exhausted)
            with pytest.raises(ServerFullError):
                await HaruSession(
                    url, api_key="topsecret", voice=card.check_voice_path(), seed_registry=False
                ).connect(start_clock=False)

            # Protocol error checks on raw socket after closing s1
            await s1.close()
            ws_url = realtime_url(url)
            async with aiohttp.ClientSession() as http:
                async with http.ws_connect(ws_url, headers={"Authorization": "Bearer topsecret"}) as ws:
                    ref_uri = encode_voice_uri(card.check_voice_path())
                    await ws.send_json({
                        "type": "session.update",
                        "session": {"instructions": "Card A", "ref_audio": ref_uri, "sample_rate_hz": SAMPLE_RATE},
                    })
                    ev1 = await ws.receive_json()
                    ev2 = await ws.receive_json()
                    assert ev1["type"] == "session.created" and ev2["type"] == "session.updated"

                    # Wrong sample rate -> bad_event, socket stays open
                    bad_pcm = base64.b64encode(np.zeros(FRAME_SAMPLES, dtype="<f4").tobytes()).decode("ascii")
                    await ws.send_json({
                        "type": "input_audio_buffer.append",
                        "audio": bad_pcm,
                        "format": "pcm_f32le",
                        "sample_rate_hz": 16000,
                    })
                    err1 = await ws.receive_json()
                    assert err1["type"] == "error" and err1["error"]["code"] == "bad_event"

                    # Second session.update with ref_audio -> ref_audio_update_unsupported
                    await ws.send_json({
                        "type": "session.update",
                        "session": {"instructions": "Card A", "ref_audio": ref_uri},
                    })
                    err2 = await ws.receive_json()
                    assert err2["error"]["code"] == "ref_audio_update_unsupported"

                    # Changed instructions -> instructions_update_unsupported
                    await ws.send_json({
                        "type": "session.update",
                        "session": {"instructions": "Card B"},
                    })
                    err3 = await ws.receive_json()
                    assert err3["error"]["code"] == "instructions_update_unsupported"

                    await ws.send_json({"type": "session.close"})
                    closed = await ws.receive_json()
                    assert closed["type"] == "session.closed"
        finally:
            await runner.cleanup()

    asyncio.run(run())


def test_mute_interrupt_policy():
    async def run():
        backend = FakeBackend(speech_amp=0.2, idle_after=3)
        runner, url, _ = await _start_test_api_server(backend)
        try:
            session = HaruSession(
                url,
                voice=card.check_voice_path(),
                seed_registry=False,
                interrupt_policy="mute",
            )
            await session.connect(start_clock=False)
            # Loud caller frame triggers phrase mute
            loud_caller = np.full(FRAME_SAMPLES, 0.25, dtype=np.float32)
            await session._send_frame(loud_caller, count_stat=True)
            c1 = await session.next_chunk()
            assert c1 is not None and c1.pcm16 == b"\x00" * len(c1.pcm16)
            await session.close()
        finally:
            await runner.cleanup()

    asyncio.run(run())


# --------------------------------------------------- CLI & phone utilities


def test_cli_stage2_parsers_and_check_command():
    parser = cli.build_parser()
    a1 = parser.parse_args(["serve", "--on", "mac", "--port", "8999"])
    assert a1.command == "serve" and a1.on == "mac" and a1.port == 8999

    a2 = parser.parse_args(["check", "ws://127.0.0.1:8000", "--api-key", "k1", "--seconds", "1.0"])
    assert a2.command == "check" and a2.api_key == "k1"

    a3 = parser.parse_args(["phone", "--telnyx", "--number", "+14155550100"])
    assert a3.command == "phone" and a3.telnyx is True and a3.phone_action is None

    a4 = parser.parse_args(["phone", "call", "+14155550199", "--twilio", "--from", "+14155550101"])
    assert a4.command == "phone" and a4.phone_action == "call" and a4.twilio is True and a4.to_number == "+14155550199"

    a5 = parser.parse_args(["phone", "stop"])
    assert a5.command == "phone" and a5.phone_action == "stop"

    # End-to-end _run_check against FakeBackend
    async def run_check():
        runner, url, _ = await _start_test_api_server(FakeBackend(), api_key="chk")
        try:
            code, info = await serve._run_check(
                url, api_key="chk", card="haru", voice=str(card.check_voice_path()), language="en", seconds=0.6
            )
            assert code == 0
            assert info["frames_received"] >= 3
            assert "w1" in info["transcript"]
        finally:
            await runner.cleanup()

    asyncio.run(run_check())


def test_twilio_signature_and_carrier_snapshot_restore():
    # Official Twilio documentation test vector
    url = "https://mycompany.com/myapp.php?foo=1&bar=2"
    params = {
        "CallSid": "CA1234567890ABCDE",
        "Caller": "+12349013030",
        "Digits": "1234",
        "From": "+12349013030",
        "To": "+18005551212",
    }
    token = "12345"
    sig = phone.compute_twilio_signature(url, params, token)
    assert sig == "0/KCTR6DLpKmkAf8muzZqo1nDgQ="
    assert phone.verify_twilio_signature(url, params, sig, token)
    assert not phone.verify_twilio_signature(url, params, "bad", token)

    # Verify Twilio snapshot & restore preserves trunk_sid
    calls = []
    tw = phone.TwilioClient("AC123", "tok")
    with mock.patch.object(tw, "_request", side_effect=lambda m, p, f=None: calls.append((m, p, f)) or {}):
        info = phone.PhoneNumberInfo(
            provider="twilio",
            id="PN1",
            phone_number="+14155550102",
            raw={"voice_url": "", "voice_method": "POST", "trunk_sid": "TK999"},
        )
        snap = tw.configure_webhook(info, "https://gw.test/twilio/voice")
        assert snap["trunk_sid"] == "TK999"
        tw.restore_number(snap)
    assert calls[0] == (
        "POST",
        "/IncomingPhoneNumbers/PN1.json",
        {"VoiceUrl": "https://gw.test/twilio/voice", "VoiceMethod": "POST", "TrunkSid": ""},
    )
    assert calls[1] == ("POST", "/IncomingPhoneNumbers/PN1.json", {"TrunkSid": "TK999"})


def test_status_cmd_displays_active_phone_state(tmp_path, monkeypatch):
    import io
    import json

    from rich.console import Console

    from haru_kit import cli, paths, ui
    from haru_kit.detect import Facts

    monkeypatch.setattr(paths, "home_dir", lambda *_a, **_kw: tmp_path)
    monkeypatch.setattr(paths, "hub_cache", lambda *_a, **_kw: tmp_path / "hf")
    monkeypatch.setattr(paths, "app_path", lambda *_a, **_kw: tmp_path / "Haru.app")
    fake_facts = Facts(
        os="mac", arch="arm64", chip="Apple M3", memory_gb=32.0,
        modal_login=False, uv="/usr/bin/uv", free_gb={"home": 100.0, "hub": 100.0},
    )
    monkeypatch.setattr(cli, "collect", lambda: fake_facts)

    buf = io.StringIO()
    monkeypatch.setattr(ui, "console", Console(file=buf, force_terminal=False, width=120))

    # 1) Without phone_state.json -> no Phone line
    assert cli.status_cmd(None) == 0
    assert "Phone:" not in buf.getvalue()

    # 2) With inbound phone_state.json -> prints number, provider, and stop & restore hint
    (tmp_path / "phone_state.json").write_text(
        json.dumps({
            "provider": "telnyx",
            "phone_number": "+14155550100",
            "snapshot": {"provider": "telnyx", "id": "123", "phone_number": "+14155550100"},
        }),
        encoding="utf-8",
    )
    buf2 = io.StringIO()
    monkeypatch.setattr(ui, "console", Console(file=buf2, force_terminal=False, width=120))
    assert cli.status_cmd(None) == 0
    out = buf2.getvalue()
    assert "Phone: +14155550100 (telnyx, inbound active)" in out
    assert "haru-kit phone stop" in out


# -------------------------------------- Real Pipecat & LiveKit SDK checks


@pytest.mark.skipif(importlib.util.find_spec("pipecat") is None, reason="pipecat-ai not installed in this env")
def test_pipecat_adapter_pipeline_and_idle_gate():
    from pipecat.frames.frames import EndFrame, InputAudioRawFrame, LLMTextFrame, SpeechOutputAudioRawFrame
    from pipecat.pipeline.pipeline import Pipeline
    from pipecat.pipeline.runner import WorkerRunner
    from pipecat.processors.frame_processor import FrameProcessor

    from haru_kit.pipecat import HaruLLMService, create_haru_worker

    collected_audio: list[SpeechOutputAudioRawFrame] = []
    collected_text: list[str] = []

    class Collector(FrameProcessor):
        async def process_frame(self, frame, direction):
            await super().process_frame(frame, direction)
            if isinstance(frame, SpeechOutputAudioRawFrame):
                collected_audio.append(frame)
            elif isinstance(frame, LLMTextFrame):
                collected_text.append(frame.text)
            await self.push_frame(frame, direction)

    async def run():
        backend = FakeBackend(speech_amp=0.15, idle_after=1)
        runner, url, _ = await _start_test_api_server(backend)
        try:
            haru = HaruLLMService(
                url=url,
                voice=card.check_voice_path(),
                seed_registry=False,
                speak_first=False,
            )
            meta = haru.service_metadata_frame()
            assert meta.is_realtime_service is True
            assert meta.user_turn_strategies is not None
            assert meta.user_turn_strategies.enable_interruptions is False

            collector = Collector()
            worker = create_haru_worker(Pipeline([haru, collector]))

            async def feed():
                await asyncio.sleep(0.15)
                pcm = np.zeros(FRAME_SAMPLES, dtype="<i2").tobytes()
                await worker.queue_frame(InputAudioRawFrame(audio=pcm, sample_rate=SAMPLE_RATE, num_channels=1))
                await asyncio.sleep(0.75)  # 1 loud frame + 2 hold frames + >=1 zeroed idle frame
                await worker.queue_frame(EndFrame())

            asyncio.create_task(feed())
            wrunner = WorkerRunner(handle_sigint=False)
            await wrunner.add_workers(worker)
            await wrunner.run()

            assert len(collected_audio) >= 4
            assert any(f.audio != b"\x00" * len(f.audio) for f in collected_audio)
            assert collected_audio[-1].audio == b"\x00" * len(collected_audio[-1].audio)
            assert "w1 " in "".join(collected_text)
        finally:
            await runner.cleanup()

    asyncio.run(run())


@pytest.mark.skipif(importlib.util.find_spec("livekit") is None, reason="livekit-agents not installed in this env")
def test_livekit_duplex_model_and_adapter():
    from livekit import rtc
    from livekit.agents import llm

    from haru_kit.livekit import HaruModel

    async def run():
        backend = FakeBackend(speech_amp=0.15)
        runner, url, _ = await _start_test_api_server(backend)
        try:
            model = HaruModel(url=url, voice=card.check_voice_path(), seed_registry=False, speak_first=True)
            adapter = llm.DuplexRealtimeAdapter(model)
            assert adapter.capabilities.turn_detection is True
            assert adapter.capabilities.supports_overlapping_speech is True

            duplex = model.session()
            transcripts: list[str] = []
            duplex.on("transcript_delta", lambda ev: transcripts.append(ev.text))

            await duplex._update_session(
                instructions="You are Haru on a LiveKit call.",
                tools=[],
            )
            # Push 50 ms frames at 24 kHz as LiveKit does
            frame_50ms = rtc.AudioFrame(
                data=b"\x00" * (1200 * 2),
                sample_rate=24000,
                num_channels=1,
                samples_per_channel=1200,
            )
            duplex.push_audio(frame_50ms)

            got_frames = []
            async for item in duplex.audio_stream:
                got_frames.append(item)
                duplex.push_audio(frame_50ms)
                if len(got_frames) >= 3:
                    break

            await duplex.aclose()
            await model.aclose()

            assert len(got_frames) >= 3
            assert got_frames[0].frame.sample_rate == SAMPLE_RATE
            assert "Hello!" in "".join(transcripts)
            assert backend.opened_settings[0].system_prompt == "You are Haru on a LiveKit call."
        finally:
            await runner.cleanup()

    asyncio.run(run())


@pytest.mark.skipif(
    importlib.util.find_spec("pipecat") is None or importlib.util.find_spec("fastapi") is None,
    reason="pipecat-ai and fastapi required",
)
def test_modal_gateway_webhooks_and_twilio_stream_bridge(monkeypatch):
    import json
    from types import SimpleNamespace

    from fastapi.testclient import TestClient

    from haru_kit.runtime import modal_gateway

    monkeypatch.setenv("TWILIO_ACCOUNT_SID", "AC_test")
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", "tw_secret")

    async def run():
        backend = FakeBackend(speech_amp=0.2)
        runner, upstream_url, _ = await _start_test_api_server(backend)
        try:
            args = SimpleNamespace(
                vllm_url=upstream_url,
                api_key="hk_secret",
                bridge_token="br_secret",
                character="haru",
                language="en",
                card_file=None,
                voice_file=str(card.check_voice_path()),
                preroll_file=None,
                record_dir=None,
                speak_first=True,
                max_sessions=1,
            )
            tracker = modal_gateway.SessionTracker(max_sessions=1)
            app = modal_gateway.build_gateway(args, tracker)

            def sync_checks() -> None:
                with TestClient(app) as tc:
                    assert tc.get("/api/ready").json() == {"ready": True}
                    # Reject unsigned Twilio webhook
                    assert tc.post("/twilio/voice", data={"CallSid": "CA1"}).status_code == 403
                    # Accept validly signed Twilio webhook
                    form = {"CallSid": "CA1", "From": "+15550001"}
                    sig = modal_gateway.compute_twilio_signature("http://testserver/twilio/voice", form, "tw_secret")
                    resp = tc.post("/twilio/voice", data=form, headers={"X-Twilio-Signature": sig})
                    assert resp.status_code == 200 and "<Stream url=" in resp.text and "br_secret" in resp.text

                    # Telnyx webhook token check
                    assert tc.post("/telnyx/voice").status_code == 403
                    tx = tc.post("/telnyx/voice?token=br_secret")
                    assert tx.status_code == 200 and 'bidirectionalMode="rtp"' in tx.text

                    # Busy response when tracker is full
                    tracker.active = 1
                    busy = tc.post("/telnyx/voice?token=br_secret")
                    assert "busy" in busy.text.lower()
                    tracker.active = 0

                    # Full simulated Twilio Media Streams WebSocket call!
                    with tc.websocket_connect("/twilio/ws") as ws:
                        ws.send_text(json.dumps({"event": "connected", "protocol": "Call", "version": "1.0.0"}))
                        ws.send_text(
                            json.dumps({
                                "event": "start",
                                "sequenceNumber": "1",
                                "start": {
                                    "streamSid": "MZ123",
                                    "callSid": "CA123",
                                    "customParameters": {"bridge_token": "br_secret", "speak_first": "1"},
                                },
                                "streamSid": "MZ123",
                            })
                        )
                        # Send 20ms of 8kHz mulaw silence (0xFF)
                        ulaw_20ms = base64.b64encode(b"\xff" * 160).decode("ascii")
                        for _ in range(8):
                            ws.send_text(
                                json.dumps({"event": "media", "streamSid": "MZ123", "media": {"payload": ulaw_20ms}})
                            )
                        # Receive outbound Twilio media frame from Haru!
                        got_media = False
                        for _ in range(10):
                            reply = json.loads(ws.receive_text())
                            if reply.get("event") == "media" and reply.get("media", {}).get("payload"):
                                got_media = True
                                break
                        assert got_media, "Expected Twilio media frames back from Haru bridge"

            await asyncio.to_thread(sync_checks)
        finally:
            await runner.cleanup()

    asyncio.run(run())
