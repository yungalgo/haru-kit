"""Runs inside a way's env: the upstream web server (DemoServer) with an upstream backend and the haru-kit routes.

    python web_server.py --engine mlx|torch|vllm [--model REPO_OR_DIR] [--port 8998] ...

Imports only upstream packages and the standard library. Prints JSON lines
on stdout: {"event": "step", ...}, {"event": "ready", "url": ...}, {"event": "error", "what", "why", "fix"},
{"event": "idle_stop", "reason": "no_first_conversation" | "idle", "minutes": ...}. Logs go to stderr.

Routes added to the upstream application (the web app of haru-kit uses them):

    GET  /api/kit                     precision and its switch, measured step (Mac), ffmpeg, model_failed, registry
    POST /api/kit/precision           {"quantize": "8" | "none"}: reload the model (Mac way only)

Characters and voices live in the browser. The shared character list (registry) is read by the browser from its
own URL (GitHub Pages sends CORS headers).
"""

from __future__ import annotations

import argparse
import asyncio
import hmac
import json
import logging
import os
import signal
import sys
import time
import traceback
import urllib.parse
from pathlib import Path

logger = logging.getLogger("haru_kit.web_server")
COOKIE = "haru_kit_key"
REGISTRY = "https://yungalgo.github.io/haru-characters/"  # HARU_KIT_REGISTRY overrides it; "" turns it off


def emit(**event) -> None:
    print(json.dumps(event), flush=True)


def idle_stop_reason(active: int, opened: int, idle_seconds: float, idle_minutes: float,
                     first_idle_minutes: float) -> str | None:
    """Why the server stops now, or None. Before the first conversation it waits first_idle_minutes (the user may
    open the link later); after a conversation, idle_minutes with no conversation. idle_minutes 0: never."""
    if not idle_minutes or active:
        return None
    if opened == 0:
        return "no_first_conversation" if idle_seconds > (first_idle_minutes or idle_minutes) * 60 else None
    return "idle" if idle_seconds > idle_minutes * 60 else None


class Watched:
    """Wraps an upstream backend: reports a failed load, counts open conversations for the idle stop,
    and can swap in another backend (the precision switch)."""

    def __init__(self, backend, measure: bool = False):
        self.backend = backend
        self.active = 0
        self.opened = 0  # conversations since the start (the idle stop waits longer before the first one)
        self.last_active = time.monotonic()
        self.failed: BaseException | None = None
        self.swapping = False
        self.measure = measure  # Mac: time a few model steps after each load (the precision warning)
        self.measuring = False
        self.step_ms: float | None = None

    async def load(self) -> None:
        try:
            await self.backend.load()
        except BaseException as error:
            self.failed = error
            raise
        if self.measure:
            self.measuring = True
            try:
                self.step_ms = await self._measure_step()
                logger.info("model step: %.0f ms (a frame is 133 ms)", self.step_ms)
            except Exception:
                logger.exception("the model step could not be measured")
            finally:
                self.measuring = False

    async def _measure_step(self, frames: int = 12) -> float:
        """Median time of one model step, from a short silent conversation with a short noise voice."""
        import numpy as np
        from haru.backends import SessionSettings

        rng = np.random.default_rng(0)
        reference = (0.01 * rng.standard_normal(5 * 24000)).astype(np.float32)
        conversation = await self.backend.open(SessionSettings(system_prompt="Measure the step.", reference=reference))
        try:
            await conversation.start()
            silence = np.zeros(3200, dtype=np.float32)
            times = []
            for _ in range(frames):
                started = time.perf_counter()
                await conversation.step(silence)
                times.append(time.perf_counter() - started)
        finally:
            await conversation.close()
        return 1000 * float(np.median(times[2:]))

    def ready(self) -> bool:
        return not self.swapping and not self.measuring and self.backend.ready()

    async def open(self, settings):
        conversation = await self.backend.open(settings)
        self.active += 1
        self.last_active = time.monotonic()
        return _Counted(conversation, self)

    async def swap(self, backend) -> None:
        """Load ``backend`` in place of the current one (no conversation may be open)."""
        import gc

        self.swapping = True
        self.failed = None
        self.step_ms = None
        try:
            old, self.backend = self.backend, backend
            old.model = None  # free the old weights before the new ones load
            del old
            gc.collect()
            await self.load()
        except Exception:
            logger.exception("the model could not be reloaded")
        finally:
            self.swapping = False


class _Counted:
    def __init__(self, conversation, owner: Watched):
        self._conversation, self._owner, self._closed, self._stepped = conversation, owner, False, False

    async def start(self):
        return await self._conversation.start()

    async def step(self, user_audio):
        if not self._stepped:
            self._stepped = True
            self._owner.opened += 1
        self._owner.last_active = time.monotonic()
        return await self._conversation.step(user_audio)

    async def close(self) -> None:
        if not self._closed:
            self._closed = True
            self._owner.active -= 1
            if self._stepped:
                self._owner.last_active = time.monotonic()
        await self._conversation.close()


def live_active(watched: Watched, server) -> int:
    """Open conversations that are in a live call (excluding a pre-warmed prompt not yet connected)."""
    prepared = 1 if getattr(server, "_prepared", None) is not None else 0
    return max(0, watched.active - prepared)


def make_backend(args):
    if args.engine == "mlx":
        from haru_mlx.backends import LocalBackend

        return LocalBackend(args.model, quantize=None if args.quantize == "none" else int(args.quantize))
    if args.engine == "torch":
        from haru.backends import LocalBackend

        codec = args.codec_backend
        if codec == "auto":
            codec = "torch"
            if sys.platform.startswith("linux"):
                try:
                    import triton  # noqa: F401

                    codec = "triton"
                except ImportError:
                    pass
        logger.info("audio codec: %s", codec)
        return LocalBackend(args.model, codec_backend=codec)
    from haru.backends import VllmBackend

    return VllmBackend(args.vllm_url)


def add_kit_routes(app, server, watched: Watched, args) -> None:
    """The kit routes for status and Mac precision switching."""
    from aiohttp import web

    state = {"quantize": args.quantize}

    def kit_info() -> dict:
        import shutil

        mlx = args.engine == "mlx"
        return {
            "precision": ("int8" if state["quantize"] == "8" else "bf16") if mlx else None,
            "precision_switch": mlx,
            "ffmpeg": shutil.which("ffmpeg") is not None,  # needed for uploads other than WAV, FLAC, Ogg
            "model_failed": watched.failed is not None and not watched.ready(),
            "step_ms": round(watched.step_ms) if watched.step_ms else None,  # Mac: measured model step
            "registry": args.registry or None,  # the shared character list (the web app's "Browse")
        }

    async def get_kit(_request):
        return web.json_response(kit_info(), headers={"Cache-Control": "no-store"})

    async def set_precision(request):
        if args.engine != "mlx":
            return web.json_response({"code": "precision_unsupported", "error": "Precision is fixed on a GPU."},
                                     status=400)
        try:
            quantize = (await request.json()).get("quantize")
        except (ValueError, AttributeError):
            quantize = None
        if quantize not in ("8", "none"):
            return web.json_response({"code": "invalid_json", "error": "quantize must be \"8\" or \"none\"."},
                                     status=400)
        if watched.swapping or live_active(watched, server):
            return web.json_response({"code": "busy", "error": "A conversation is running."}, status=409)
        if quantize != state["quantize"]:
            watched.swapping = True
            try:
                await _discard_prepared(server)
                state["quantize"] = quantize
                args.quantize = quantize
                asyncio.get_running_loop().create_task(watched.swap(make_backend(args)))
            except BaseException:
                watched.swapping = False
                raise
        return web.json_response(kit_info())

    app.router.add_get("/api/kit", get_kit)
    app.router.add_post("/api/kit/precision", set_precision)


async def _discard_prepared(server) -> None:
    """Drop the conversation whose prompt the server already ran (``DemoServer._discard_prepared``, haru-speech
    0.1.1; the only private upstream name haru-kit uses)."""
    await server._discard_prepared()


def origin_middleware():
    """Require same-origin ``Origin`` on state-changing requests and WebSockets (blocks drive-by localhost/CSRF)."""
    from aiohttp import web

    @web.middleware
    async def check(request, handler):
        is_ws = request.headers.get("Upgrade", "").lower() == "websocket"
        if request.method not in ("GET", "HEAD", "OPTIONS") or is_ws:
            origin = request.headers.get("Origin", "")
            host = urllib.parse.urlparse(origin).netloc.lower() if origin else ""
            if not host or host != request.host.lower():
                return web.Response(status=403, text="403: cross-origin request blocked.")
        return await handler(request)

    return check


def key_middleware(key: str, force_secure: bool = False):
    """``?key=K`` once sets a cookie; every other request needs the cookie. Else 403."""
    from aiohttp import web

    key_bytes = key.encode("utf-8")

    @web.middleware
    async def check(request, handler):
        if request.path == "/api/ready":  # haru-kit waits on it; it tells nothing about the user
            return await handler(request)
        given = request.query.get("key")
        if given is not None and hmac.compare_digest(given.encode("utf-8", "replace"), key_bytes):
            query = {k: v for k, v in request.query.items() if k != "key"}
            response = web.HTTPFound(request.rel_url.with_query(query))
            proto = request.headers.get("X-Forwarded-Proto", "").split(",")[0].strip().lower()
            secure = force_secure or request.secure or proto == "https"
            response.set_cookie(COOKIE, key, httponly=True, secure=secure, samesite="Lax", max_age=24 * 3600)
            raise response
        cookie = request.cookies.get(COOKIE, "")
        if hmac.compare_digest(cookie.encode("utf-8", "replace"), key_bytes):
            return await handler(request)
        return web.Response(status=403, text="403: this link needs its access key. Use the full link from haru-kit.")

    return check


async def serve(args) -> int:
    from aiohttp import web
    from haru.server import DemoServer, ServerOptions

    emit(event="step", name="load", text="Loading the model")
    watched = Watched(make_backend(args), measure=args.engine == "mlx")
    options = ServerOptions(
        network_profile=args.network_profile,
        max_sessions=args.max_sessions,
        static_dir=Path(args.static) if args.static else None,
    )
    server = DemoServer(watched, options)
    app = server.build_app()
    add_kit_routes(app, server, watched, args)
    app.middlewares.insert(0, origin_middleware())
    if args.access_key:
        app.middlewares.insert(0, key_middleware(args.access_key, force_secure=args.network_profile == "internet"))

    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    try:
        site = web.TCPSite(runner, args.host, args.port)
        try:
            await site.start()
        except OSError as error:
            emit(event="error", what=f"Port {args.port} is in use.", why=str(error),
                 fix=f"Stop the other program, or use another port: haru-kit start --port {args.port + 1}")
            return 1
        host = "localhost" if args.host in ("127.0.0.1", "0.0.0.0", "::") else args.host
        url = f"http://{host}:{args.port}"
        load_started = time.monotonic()
        while not watched.ready():
            if watched.failed is not None:
                error = watched.failed
                emit(event="error", what="The model could not be loaded.",
                     why=f"{type(error).__name__}: {str(error).splitlines()[0][:300] if str(error) else ''}",
                     fix="Read the log, then run haru-kit doctor.")
                return 1
            if args.load_timeout_minutes and time.monotonic() - load_started > args.load_timeout_minutes * 60:
                emit(event="error", what="The model did not load in time.",
                     why=f"Waited more than {args.load_timeout_minutes:g} minutes.",
                     fix="Read the log, then try again.")
                return 1
            await asyncio.sleep(0.5)
        emit(event="ready", url=url)
        watched.last_active = time.monotonic()
        while True:
            await asyncio.sleep(5)
            reason = idle_stop_reason(live_active(watched, server), watched.opened,
                                      time.monotonic() - watched.last_active, args.idle_minutes,
                                      args.first_idle_minutes)
            if reason:
                await _discard_prepared(server)
                minutes = args.idle_minutes if reason == "idle" else args.first_idle_minutes or args.idle_minutes
                emit(event="idle_stop", reason=reason, minutes=minutes)
                return 0
    finally:
        await runner.cleanup()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--engine", choices=("mlx", "torch", "vllm"), required=True)
    parser.add_argument("--model", default="higurashi-ai/Haru-4B")
    parser.add_argument("--quantize", default="8", choices=("8", "none"))
    parser.add_argument("--codec-backend", default="auto", choices=("auto", "torch", "triton"))
    parser.add_argument("--vllm-url", default="http://127.0.0.1:8000")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8998)
    parser.add_argument("--static", default=str(Path(__file__).resolve().parent.parent / "static"),
                        help="built web app (default: haru-kit's own build)")
    parser.add_argument("--network-profile", default="local", choices=("local", "internet"))
    parser.add_argument("--max-sessions", type=int, default=1)
    parser.add_argument("--access-key")
    parser.add_argument("--idle-minutes", type=float, default=0, help="stop after a conversation and this long "
                        "with no conversation; 0: never")
    parser.add_argument("--first-idle-minutes", type=float, default=0,
                        help="stop if no conversation starts this long after ready (default: --idle-minutes)")
    parser.add_argument("--load-timeout-minutes", type=float, default=0,
                        help="stop if the model is not ready within this many minutes; 0: wait forever")
    parser.add_argument("--registry", default=os.environ.get("HARU_KIT_REGISTRY", REGISTRY),
                        help="URL of the shared character list (index.json under it); empty: none")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, stream=sys.stderr, format="%(asctime)s %(name)s: %(message)s")
    if hasattr(signal, "SIGBREAK"):  # Windows: haru-kit stops us with CTRL_BREAK_EVENT

        def on_break(_sig, _frame):
            raise KeyboardInterrupt

        signal.signal(signal.SIGBREAK, on_break)
    try:
        return asyncio.run(serve(args))
    except KeyboardInterrupt:
        return 0
    except Exception as error:
        traceback.print_exc()
        emit(event="error", what="The Haru server stopped.", why=f"{type(error).__name__}: {error}"[:300],
             fix="Read the log, then run haru-kit doctor.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
