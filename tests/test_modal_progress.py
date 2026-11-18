"""The Modal way: Modal's scheduling log lines, vLLM's loading stages, the wait decision, the idle stop, its texts."""

from __future__ import annotations

import importlib.util
import sys
import time
from unittest import mock

import pytest

from haru_kit import modal, paths, ui

WAITING = ("Function 'haru' (fu-FqINKqzCR199M0EPjpH038) is waiting to be scheduled on a GPU_A10G worker. "
           "We are actively working on acquiring more capacity for your workload.. "
           "Relaxing requirements (region=ap) may lead to faster scheduling")


@pytest.mark.parametrize("line, text", [
    (WAITING, "No A10 GPU free in Asia-Pacific yet; Modal is looking for one"),
    (WAITING.replace("region=ap", "region=eu"), "No A10 GPU free in Europe yet; Modal is looking for one"),
    ("Function 'haru' (fu-1) is waiting to be scheduled on a GPU_L40S worker.",
     "No L40S GPU free yet; Modal is looking for one"),
    ("Function 'haru' (fu-1) hit a new limit.", "hit a new limit."),
    ("x " * 60, ("x " * 35)[:69] + "…"),
])
def test_schedule_text(line, text):
    assert modal.schedule_text(line) == text


@pytest.mark.parametrize("waited, asked_at, ask, action", [
    (60, 0, True, None),
    (180, 0, True, "ask"),
    (300, 180, True, None),
    (360, 180, True, "ask"),
    (1000, 900, True, None),  # in a terminal there is no time limit: the user decides
    (600, 0, False, None),
    (900, 0, False, "timeout"),
])
def test_wait_action(waited, asked_at, ask, action):
    assert modal.wait_action(waited, asked_at, ask) == action


@pytest.fixture(scope="module")
def modal_app():
    """runtime/modal_app.py without the modal package (it runs in the Modal env and in the container)."""
    spec = importlib.util.spec_from_file_location("modal_app_under_test", paths.runtime_script("modal_app.py"))
    module = importlib.util.module_from_spec(spec)
    with mock.patch.dict(sys.modules, {"modal": mock.MagicMock()}):
        spec.loader.exec_module(module)
    return module


VLLM_LINES = [  # from a real start on Modal (A10, 2026-11-18), in order
    ("(APIServer pid=5) INFO 11-18 00:46:53 [omni_base.py:211] [DuplexOmni] Initializing with model "
     "higurashi-ai/Haru-4B", "engine"),
    ("(StageEngineCoreProc_stage0_replica0 pid=170) INFO 11-18 00:47:43 [core.py:133] Initializing a V1 LLM engine "
     "(v0.32.0) with config: compilation_config={\"mode\": 3, \"cudagraph_mode\": [2, 1]}", "engine"),
    ("(StageEngineCoreProc_stage0_replica0 pid=170) INFO 11-18 00:47:48 [gpu_model_runner.py:5039] "
     "Starting to load model higurashi-ai/Haru-4B...", "load"),
    ("Loading safetensors checkpoint shards:   0% Completed | 0/1 [00:00<?, ?it/s]", "load"),
    ("(StageEngineCoreProc_stage0_replica0 pid=170) INFO 11-18 00:48:19 [default_loader.py:501] "
     "Loading weights took 28.64 seconds", "load"),
    ("(StageEngineCoreProc_stage0_replica0 pid=170) INFO 11-18 00:48:22 [monitor.py:40] [backbone] "
     "Starting torch.compile", "compile"),
    ("(StageEngineCoreProc_stage0_replica0 pid=170) INFO 11-18 00:48:28 [backends.py:1166] [backbone] "
     "Dynamo bytecode transform time: 5.46 s", "compile"),
    ("INFO 11-18 01:03:12 [default_loader.py:268] Loading weights took 2.10 seconds", "compile"),  # never back
    ("INFO 11-18 01:05:01 [api_server.py:1971] Starting vLLM API server 0 on http://127.0.0.1:8000", "warmup"),
    ("INFO:     Application startup complete.", "warmup"),
]


def test_vllm_stages(modal_app):
    assert modal_app.STAGES == tuple(modal.STAGES)  # the CLI shows each stage that the container reports
    stage = modal_app.STAGES[0]
    for line, expected in VLLM_LINES:
        stage = modal_app.vllm_stage(line, stage)
        assert stage == expected, line


def test_cache_name(modal_app):
    versions = {"vllm": "0.32.0", "vllm-omni": "0.32.0", "torch": "2.13.0", "triton": "3.7.1",
                "flashinfer-python": "0.7.0.post1"}
    a10 = modal_app.cache_name("NVIDIA A10", versions)
    assert a10 == ("nvidia-a10_vllm-0.32.0_vllm-omni-0.32.0_torch-2.13.0_triton-3.7.1_"
                   "flashinfer-python-0.7.0.post1.tar")
    # another GPU type or another version: another archive (a cache never meets another GPU or version)
    assert modal_app.cache_name("NVIDIA L40S", versions) != a10
    assert modal_app.cache_name("NVIDIA A10", {**versions, "vllm": "0.33.0"}) != a10
    # the slug before the first "_" only matches its own GPU (old archives of A10 are removed, not of A10G)
    assert modal_app.cache_name("NVIDIA A10G", versions).split("_", 1)[0] == "nvidia-a10g"


@pytest.fixture(scope="module")
def web_server():
    spec = importlib.util.spec_from_file_location("web_server_under_test", paths.runtime_script("web_server.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("active, opened, idle_seconds, reason", [
    (0, 0, 6 * 60, None),  # the link is not opened yet: wait longer than the 5 minutes
    (0, 0, 14 * 60, None),
    (0, 0, 15 * 60 + 5, "no_first_conversation"),
    (1, 1, 60 * 60, None),  # a conversation is open
    (0, 1, 4 * 60, None),
    (0, 1, 5 * 60 + 5, "idle"),  # after a conversation: 5 minutes
    (0, 3, 5 * 60 + 5, "idle"),
])
def test_idle_stop_reason(web_server, active, opened, idle_seconds, reason):
    assert web_server.idle_stop_reason(active, opened, idle_seconds, 5, 15) == reason


def test_idle_stop_off_and_default(web_server):
    assert web_server.idle_stop_reason(0, 0, 10 ** 6, 0, 15) is None  # --idle-minutes 0: never
    assert web_server.idle_stop_reason(0, 0, 5 * 60 + 5, 5, 0) == "no_first_conversation"  # default: idle minutes


def test_idle_minutes_match(modal_app):
    assert (modal_app.IDLE_MINUTES, modal_app.FIRST_IDLE_MINUTES) == (modal.IDLE_MINUTES, modal.FIRST_IDLE_MINUTES)


def test_stopped_text():
    at = time.mktime((2026, 11, 18, 10, 50, 58, 0, 0, -1))  # local time
    assert modal.stopped_text({"at": at, "reason": "no_first_conversation"}) == (
        "Haru stopped by itself at 10:50 (no conversation). Start again: haru-kit start --on modal")
    assert modal.stopped_text({"at": at, "reason": "idle"}) == (
        "Haru stopped by itself at 10:50 (5 minutes with no conversation). Start again: haru-kit start --on modal")


def test_ready_modal_text():
    with ui.console.capture() as captured:
        ui.ready_modal("https://x.modal.host/?key=k", "A10", 1.1, "eu-west", "11:00", 5)
    text = captured.get()
    assert "If you do not start a conversation by 11:00, Haru stops by itself." in text
    assert "After a conversation, it stops after 5 minutes with no conversation." in text


def test_prepared_session_does_not_block_idle_stop(web_server):
    import asyncio

    class FakeConv:
        closed = False
        async def start(self): return None
        async def step(self, audio): return audio
        async def close(self): self.closed = True

    class FakeBackend:
        def ready(self): return True
        async def open(self, _settings): return FakeConv()

    class FakeServer:
        _prepared = None

    async def scenario():
        watched = web_server.Watched(FakeBackend())
        server = FakeServer()
        # Simulate POST /api/session preparing a prompt without /api/chat starting yet.
        conv = await watched.open(None)
        server._prepared = ("s1", conv, None)
        assert watched.active == 1 and watched.opened == 0
        assert web_server.live_active(watched, server) == 0
        assert web_server.idle_stop_reason(web_server.live_active(watched, server), watched.opened, 16 * 60, 5, 15) == (
            "no_first_conversation"
        )
        # Once /api/chat consumes _prepared and steps, it counts as a live call and increments opened.
        server._prepared = None
        assert web_server.live_active(watched, server) == 1
        await conv.step(b"")
        assert watched.opened == 1
        await conv.close()
        assert watched.active == 0 and web_server.live_active(watched, server) == 0
        assert web_server.idle_stop_reason(0, watched.opened, 6 * 60, 5, 15) == "idle"

    asyncio.run(scenario())


def test_key_and_origin_middlewares_non_ascii_and_csrf(web_server):
    import asyncio
    from types import SimpleNamespace

    class FakeHTTPFound(Exception):
        def __init__(self, location):
            self.location = location
            self.cookies = {}
        def set_cookie(self, name, value, **kw):
            self.cookies[name] = (value, kw)

    class FakeResponse:
        def __init__(self, status=200, text=""):
            self.status = status
            self.text = text

    fake_web = SimpleNamespace(middleware=lambda fn: fn, HTTPFound=FakeHTTPFound, Response=FakeResponse)
    with mock.patch.dict(sys.modules, {"aiohttp": SimpleNamespace(web=fake_web), "aiohttp.web": fake_web}):
        key_mw = web_server.key_middleware("secret-key", force_secure=True)
        origin_mw = web_server.origin_middleware()

    async def ok_handler(_req):
        return FakeResponse(200, "ok")

    class FakeUrl:
        def with_query(self, q): return f"/?{q}"

    def req(method="GET", path="/", query=None, cookies=None, headers=None, host="localhost:8998"):
        return SimpleNamespace(
            method=method, path=path, query=query or {}, cookies=cookies or {},
            headers=headers or {}, host=host, secure=False, rel_url=FakeUrl(),
        )

    async def checks():
        # Non-ASCII key / cookie returns 403 instead of raising TypeError 500.
        res = await key_mw(req(query={"key": "…wrong"}), ok_handler)
        assert res.status == 403
        res2 = await key_mw(req(cookies={"haru_kit_key": "한글"}), ok_handler)
        assert res2.status == 403
        # Valid key redirects and sets Secure + HttpOnly cookie.
        with pytest.raises(FakeHTTPFound) as found:
            await key_mw(req(query={"key": "secret-key"}), ok_handler)
        assert found.value.cookies["haru_kit_key"][1]["secure"] is True
        # Cross-origin POST and WebSocket upgrade are blocked; same-origin allowed.
        assert (await origin_mw(req(method="POST", path="/api/session"), ok_handler)).status == 403
        assert (await origin_mw(
            req(method="POST", headers={"Origin": "https://evil.example"}), ok_handler
        )).status == 403
        assert (await origin_mw(
            req(method="GET", headers={"Upgrade": "websocket", "Origin": "https://evil.example"}), ok_handler
        )).status == 403
        assert (await origin_mw(
            req(method="POST", headers={"Origin": "http://localhost:8998"}), ok_handler
        )).status == 200

    asyncio.run(checks())
