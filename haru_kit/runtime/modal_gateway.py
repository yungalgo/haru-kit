"""Gateway running inside the Modal container alongside ``vllm serve`` (port 8000).

Exposes on ``0.0.0.0:8998``:
- ``GET  /api/ready`` and ``GET /health``: container readiness
- ``WS   /v1/realtime``: Bearer-authenticated proxy to ``ws://127.0.0.1:8000/v1/realtime?duplex=1``
- ``POST /twilio/voice`` and ``WS /twilio/ws``: TwiML webhook + co-located Pipecat bridge
- ``POST /telnyx/voice`` and ``WS /telnyx/ws``: TeXML webhook + co-located Pipecat bridge

Tracks active realtime and phone sessions and stops the container cleanly after idle timeout
so the GPU is never billed when unused.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import hmac
import json
import os
import sys
import time
from urllib.parse import parse_qsl, urlencode
from xml.sax.saxutils import escape as xml_escape

import aiohttp
import uvicorn
from fastapi import FastAPI, Request, Response, WebSocket, WebSocketDisconnect

# Ensure `/haru_kit_pkg` (copied into the container image) is importable.
if "/haru_kit_pkg" not in sys.path:
    sys.path.insert(0, "/haru_kit_pkg")


def emit(**event: object) -> None:
    print(json.dumps(event), flush=True)


def compute_twilio_signature(url: str, params: dict[str, str], auth_token: str) -> str:
    payload = url + "".join(f"{k}{params[k]}" for k in sorted(params))
    digest = hmac.new(auth_token.encode("utf-8"), payload.encode("utf-8"), hashlib.sha1).digest()
    return base64.b64encode(digest).decode("ascii")


def verify_twilio_signature(url: str, params: dict[str, str], signature: str, auth_token: str) -> bool:
    if not signature or not auth_token:
        return False
    expected = compute_twilio_signature(url, params, auth_token)
    return hmac.compare_digest(signature.encode("utf-8"), expected.encode("utf-8"))


def public_url_of(request: Request) -> str:
    proto = request.headers.get("x-forwarded-proto") or request.url.scheme or "https"
    host = request.headers.get("x-forwarded-host") or request.headers.get("host") or request.url.netloc
    query = f"?{request.url.query}" if request.url.query else ""
    return f"{proto}://{host}{request.url.path}{query}"


def ws_base_of(request: Request) -> str:
    proto = request.headers.get("x-forwarded-proto") or request.url.scheme or "https"
    ws_scheme = "wss" if proto == "https" else "ws"
    host = request.headers.get("x-forwarded-host") or request.headers.get("host") or request.url.netloc
    return f"{ws_scheme}://{host}"


class SessionTracker:
    def __init__(self, max_sessions: int) -> None:
        self.max_sessions = max(1, max_sessions)
        self.active = 0
        self.opened = 0
        self.ready_since: float | None = None
        self.idle_since: float = time.monotonic()

    def enter(self) -> None:
        self.active += 1
        self.opened += 1

    def leave(self) -> None:
        self.active = max(0, self.active - 1)
        if self.active == 0:
            self.idle_since = time.monotonic()

    def idle_reason(
        self,
        now: float,
        *,
        idle_minutes: float,
        first_idle_minutes: float,
        load_timeout_minutes: float,
        started_at: float,
    ) -> str | None:
        if self.ready_since is None:
            if load_timeout_minutes > 0 and now - started_at >= load_timeout_minutes * 60:
                return "load_timeout"
            return None
        if self.active > 0:
            return None
        if self.opened == 0:
            if first_idle_minutes > 0 and now - self.ready_since >= first_idle_minutes * 60:
                return "no_first_conversation"
            return None
        if idle_minutes > 0 and now - self.idle_since >= idle_minutes * 60:
            return "idle"
        return None


def build_gateway(args: argparse.Namespace, tracker: SessionTracker) -> FastAPI:
    app = FastAPI(openapi_url=None, docs_url=None, redoc_url=None)
    vllm_http = args.vllm_url.rstrip("/")
    vllm_ws = vllm_http.replace("http", "ws", 1) + "/v1/realtime?duplex=1"
    api_key = args.api_key or ""
    bridge_token = args.bridge_token or api_key
    twilio_sid = os.environ.get("TWILIO_ACCOUNT_SID") or None
    twilio_token = os.environ.get("TWILIO_AUTH_TOKEN") or None
    telnyx_key = os.environ.get("TELNYX_API_KEY") or None

    async def vllm_ready() -> bool:
        try:
            async with aiohttp.ClientSession() as http:
                async with http.get(f"{vllm_http}/health", timeout=aiohttp.ClientTimeout(total=3)) as resp:
                    if resp.status == 200:
                        if tracker.ready_since is None:
                            tracker.ready_since = time.monotonic()
                            tracker.idle_since = tracker.ready_since
                        return True
        except aiohttp.ClientError:
            pass
        except TimeoutError:
            pass
        return False

    def check_bearer(auth_header: str) -> bool:
        if not api_key:
            return True
        if not auth_header.startswith("Bearer "):
            return False
        got = auth_header[len("Bearer ") :].strip()
        return hmac.compare_digest(got.encode("utf-8"), api_key.encode("utf-8"))

    def check_token(got: str) -> bool:
        if not bridge_token:
            return True
        return hmac.compare_digest(got.encode("utf-8"), bridge_token.encode("utf-8"))

    @app.get("/api/ready")
    async def handle_ready() -> dict[str, bool]:
        return {"ready": await vllm_ready()}

    @app.get("/health")
    async def handle_health() -> Response:
        if await vllm_ready():
            return Response(content='{"status":"ok"}', media_type="application/json", status_code=200)
        return Response(content='{"status":"loading"}', media_type="application/json", status_code=503)

    @app.websocket("/v1/realtime")
    async def proxy_realtime(ws: WebSocket) -> None:
        auth = ws.headers.get("authorization", "")
        if not check_bearer(auth):
            await ws.close(code=1008, reason="Unauthorized")
            return
        await ws.accept()
        if tracker.active >= tracker.max_sessions:
            err_msg = {"type": "error", "error": {"code": "resource_exhausted", "message": "server is at capacity"}}
            await ws.send_text(json.dumps(err_msg))
            await ws.close()
            return

        tracker.enter()
        query = ws.url.query or "duplex=1"
        upstream_url = f"{vllm_http.replace('http', 'ws', 1)}/v1/realtime?{query}"
        try:
            async with aiohttp.ClientSession() as http:
                async with http.ws_connect(upstream_url, max_msg_size=0, heartbeat=20) as upstream:

                    async def c2u() -> None:
                        try:
                            while True:
                                msg = await ws.receive()
                                if msg.get("type") == "websocket.disconnect":
                                    break
                                if msg.get("text") is not None:
                                    await upstream.send_str(msg["text"])
                                elif msg.get("bytes") is not None:
                                    await upstream.send_bytes(msg["bytes"])
                        except WebSocketDisconnect:
                            pass
                        finally:
                            if not upstream.closed:
                                await upstream.close()

                    async def u2c() -> None:
                        async for msg in upstream:
                            if msg.type == aiohttp.WSMsgType.TEXT:
                                await ws.send_text(msg.data)
                            elif msg.type == aiohttp.WSMsgType.BINARY:
                                await ws.send_bytes(msg.data)
                            elif msg.type in (
                                aiohttp.WSMsgType.CLOSE,
                                aiohttp.WSMsgType.CLOSED,
                                aiohttp.WSMsgType.ERROR,
                            ):
                                break

                    tasks = [asyncio.create_task(c2u()), asyncio.create_task(u2c())]
                    _, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                    for t in pending:
                        t.cancel()
        except Exception:  # noqa: BLE001
            pass
        finally:
            tracker.leave()
            try:
                await ws.close()
            except Exception:  # noqa: BLE001
                pass

    @app.post("/twilio/voice")
    async def twilio_voice(request: Request) -> Response:
        raw_body = (await request.body()).decode("utf-8", "replace")
        params = dict(parse_qsl(raw_body, keep_blank_values=True))
        q_token = request.query_params.get("token", "")
        sig = request.headers.get("x-twilio-signature", "")
        if twilio_token:
            valid_sig = verify_twilio_signature(public_url_of(request), params, sig, twilio_token)
            if not valid_sig and not (q_token and check_token(q_token)):
                return Response(content="Forbidden", status_code=403)
        elif bridge_token and not check_token(q_token):
            return Response(content="Forbidden", status_code=403)

        if tracker.active >= tracker.max_sessions or not await vllm_ready():
            busy_xml = (
                '<?xml version="1.0" encoding="UTF-8"?>'
                "<Response><Say>Sorry, the line is busy right now. Please try again shortly.</Say><Hangup/></Response>"
            )
            return Response(content=busy_xml, media_type="application/xml")

        speak_first = "1" if request.query_params.get("speak_first") == "1" else "0"
        stream_url = xml_escape(f"{ws_base_of(request)}/twilio/ws")
        twiml = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            "<Response><Connect>"
            f'<Stream url="{stream_url}">'
            f'<Parameter name="bridge_token" value="{xml_escape(bridge_token)}"/>'
            f'<Parameter name="speak_first" value="{speak_first}"/>'
            "</Stream>"
            "</Connect></Response>"
        )
        return Response(content=twiml, media_type="application/xml")

    @app.post("/telnyx/voice")
    @app.get("/telnyx/voice")
    async def telnyx_voice(request: Request) -> Response:
        q_token = request.query_params.get("token", "")
        if bridge_token and not check_token(q_token):
            return Response(content="Forbidden", status_code=403)

        if tracker.active >= tracker.max_sessions or not await vllm_ready():
            busy_xml = (
                '<?xml version="1.0" encoding="UTF-8"?>'
                "<Response><Say>Sorry, the line is busy right now. Please try again shortly.</Say><Hangup/></Response>"
            )
            return Response(content=busy_xml, media_type="application/xml")

        speak_first = "1" if request.query_params.get("speak_first") == "1" else "0"
        qs = urlencode({"token": bridge_token, "speak_first": speak_first})
        stream_url = xml_escape(f"{ws_base_of(request)}/telnyx/ws?{qs}")
        texml = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            "<Response><Connect>"
            f'<Stream url="{stream_url}" bidirectionalMode="rtp"/>'
            "</Connect></Response>"
        )
        return Response(content=texml, media_type="application/xml")

    async def _run_pipecat_call(ws: WebSocket, provider: str) -> None:
        from pipecat.pipeline.pipeline import Pipeline
        from pipecat.pipeline.runner import WorkerRunner
        from pipecat.runner.utils import parse_telephony_websocket
        from pipecat.serializers.telnyx import TelnyxFrameSerializer
        from pipecat.serializers.twilio import TwilioFrameSerializer
        from pipecat.transports.websocket.fastapi import FastAPIWebsocketParams, FastAPIWebsocketTransport

        from haru_kit.pipecat import HaruLLMService, create_haru_worker

        _, call_data = await parse_telephony_websocket(ws)
        speak_first = bool(args.speak_first)

        if provider == "twilio":
            body = call_data.get("body") or {}
            if bridge_token and not check_token(str(body.get("bridge_token") or "")):
                await ws.close(code=1008)
                return
            if body.get("speak_first") == "1":
                speak_first = True
            call_sid = call_data.get("call_id")
            can_hangup = bool(twilio_sid and twilio_token and call_sid)
            serializer = TwilioFrameSerializer(
                stream_sid=call_data["stream_id"],
                call_sid=call_sid,
                account_sid=twilio_sid if can_hangup else None,
                auth_token=twilio_token if can_hangup else None,
                params=TwilioFrameSerializer.InputParams(auto_hang_up=can_hangup),
            )
        else:
            if request_speak := ws.query_params.get("speak_first"):
                if request_speak == "1":
                    speak_first = True
            call_ctrl_id = call_data.get("call_id")
            outbound_enc = call_data.get("outbound_encoding") or "PCMU"
            can_hangup = bool(telnyx_key and call_ctrl_id)
            serializer = TelnyxFrameSerializer(
                stream_id=call_data["stream_id"],
                outbound_encoding=outbound_enc,
                inbound_encoding="PCMU",
                call_control_id=call_ctrl_id if can_hangup else None,
                api_key=telnyx_key if can_hangup else None,
                params=TelnyxFrameSerializer.InputParams(auto_hang_up=can_hangup),
            )

        transport = FastAPIWebsocketTransport(
            websocket=ws,
            params=FastAPIWebsocketParams(
                audio_in_enabled=True,
                audio_out_enabled=True,
                add_wav_header=False,
                serializer=serializer,
            ),
        )
        haru = HaruLLMService(
            url=vllm_ws,
            card=args.card_file or args.character,
            voice=args.voice_file,
            language=args.language,
            speak_first=speak_first,
            preroll=args.preroll_file,
            record_dir=args.record_dir,
            seed_registry=True,
        )
        pipeline = Pipeline([transport.input(), haru, transport.output()])
        worker = create_haru_worker(pipeline)

        @transport.event_handler("on_client_disconnected")
        async def _on_disconnect(_transport, _client) -> None:
            await worker.cancel()

        runner = WorkerRunner(handle_sigint=False)
        await runner.add_workers(worker)
        await runner.run()

    @app.websocket("/twilio/ws")
    async def twilio_ws(ws: WebSocket) -> None:
        await ws.accept()
        if tracker.active >= tracker.max_sessions:
            await ws.close()
            return
        tracker.enter()
        try:
            await _run_pipecat_call(ws, "twilio")
        finally:
            tracker.leave()

    @app.websocket("/telnyx/ws")
    async def telnyx_ws(ws: WebSocket) -> None:
        q_token = ws.query_params.get("token", "")
        if bridge_token and not check_token(q_token):
            await ws.close(code=1008)
            return
        await ws.accept()
        if tracker.active >= tracker.max_sessions:
            await ws.close()
            return
        tracker.enter()
        try:
            await _run_pipecat_call(ws, "telnyx")
        finally:
            tracker.leave()

    return app


async def _run_gateway(args: argparse.Namespace) -> int:
    tracker = SessionTracker(max_sessions=args.max_sessions)
    app = build_gateway(args, tracker)
    config = uvicorn.Config(app, host=args.host, port=args.port, log_level="warning", ws_max_size=16 * 1024 * 1024)
    server = uvicorn.Server(config)

    started_at = time.monotonic()

    async def _watch_idle() -> None:
        while not server.should_exit:
            await asyncio.sleep(5)
            reason = tracker.idle_reason(
                time.monotonic(),
                idle_minutes=args.idle_minutes,
                first_idle_minutes=args.first_idle_minutes,
                load_timeout_minutes=args.load_timeout_minutes,
                started_at=started_at,
            )
            if reason:
                emit(event="idle_stop", reason=reason)
                server.should_exit = True
                return

    watcher = asyncio.create_task(_watch_idle())
    try:
        await server.serve()
    finally:
        watcher.cancel()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--vllm-url", default="http://127.0.0.1:8000")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8998)
    parser.add_argument("--max-sessions", type=int, default=2)
    parser.add_argument("--api-key", default=os.environ.get("HARU_API_KEY", ""))
    parser.add_argument("--bridge-token", default=os.environ.get("HARU_BRIDGE_TOKEN", ""))
    parser.add_argument("--character", default="haru")
    parser.add_argument("--language", default="en")
    parser.add_argument("--card-file", default=None)
    parser.add_argument("--voice-file", default=None)
    parser.add_argument("--preroll-file", default=None)
    parser.add_argument("--record-dir", default=None)
    parser.add_argument("--speak-first", action="store_true")
    parser.add_argument("--idle-minutes", type=float, default=5.0)
    parser.add_argument("--first-idle-minutes", type=float, default=15.0)
    parser.add_argument("--load-timeout-minutes", type=float, default=25.0)
    args = parser.parse_args()
    return asyncio.run(_run_gateway(args))


if __name__ == "__main__":
    sys.exit(main())
