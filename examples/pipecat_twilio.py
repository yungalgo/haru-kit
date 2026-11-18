"""Demo Twilio Media Streams webhook & WebSocket bridge for Haru-4B using Pipecat.

Verifies ``X-Twilio-Signature`` on ``POST /twilio/voice`` and bridges 8 kHz phone audio
to Haru-4B over ``HaruLLMService``.

Setup:
    uv pip install "haru-kit[pipecat]" fastapi "uvicorn[standard]"

Run:
    export HARU_URL="wss://...modal.host"
    export HARU_API_KEY="hk_..."
    export TWILIO_ACCOUNT_SID="AC..."
    export TWILIO_AUTH_TOKEN="..."
    uvicorn examples.pipecat_twilio:app --host 0.0.0.0 --port 8765
"""

from __future__ import annotations

import hmac
import os
import secrets
from urllib.parse import parse_qsl
from xml.sax.saxutils import escape as xml_escape

from fastapi import FastAPI, Request, Response, WebSocket
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import WorkerRunner
from pipecat.runner.utils import parse_telephony_websocket
from pipecat.serializers.twilio import TwilioFrameSerializer
from pipecat.transports.websocket.fastapi import FastAPIWebsocketParams, FastAPIWebsocketTransport

from haru_kit.phone import verify_twilio_signature
from haru_kit.pipecat import HaruLLMService, create_haru_worker

app = FastAPI()
BRIDGE_TOKEN = os.environ.get("BRIDGE_TOKEN") or secrets.token_urlsafe(24)


def _public_url(request: Request) -> str:
    proto = request.headers.get("x-forwarded-proto") or request.url.scheme
    host = request.headers.get("x-forwarded-host") or request.headers.get("host") or request.url.netloc
    query = f"?{request.url.query}" if request.url.query else ""
    return f"{proto}://{host}{request.url.path}{query}"


@app.post("/twilio/voice")
async def twilio_voice(request: Request) -> Response:
    auth_token = os.environ.get("TWILIO_AUTH_TOKEN", "")
    if not auth_token:
        return Response(content="TWILIO_AUTH_TOKEN is not set", status_code=500)
    raw_body = (await request.body()).decode("utf-8", "replace")
    params = dict(parse_qsl(raw_body, keep_blank_values=True))
    sig = request.headers.get("x-twilio-signature", "")
    if not verify_twilio_signature(_public_url(request), params, sig, auth_token):
        return Response(content="Invalid Twilio signature", status_code=403)

    proto = "wss" if (request.headers.get("x-forwarded-proto") or request.url.scheme) == "https" else "ws"
    host = request.headers.get("x-forwarded-host") or request.headers.get("host") or request.url.netloc
    ws_url = xml_escape(f"{proto}://{host}/twilio/ws")
    twiml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<Response><Connect><Stream url="{ws_url}">'
        f'<Parameter name="bridge_token" value="{xml_escape(BRIDGE_TOKEN)}"/>'
        "</Stream></Connect></Response>"
    )
    return Response(content=twiml, media_type="application/xml")


@app.websocket("/twilio/ws")
async def twilio_ws(ws: WebSocket) -> None:
    await ws.accept()
    _, call_data = await parse_telephony_websocket(ws)
    body = call_data.get("body") or {}
    got_token = str(body.get("bridge_token") or "")
    if not hmac.compare_digest(got_token.encode("utf-8"), BRIDGE_TOKEN.encode("utf-8")):
        await ws.close(code=1008)
        return

    sid = os.environ.get("TWILIO_ACCOUNT_SID")
    tok = os.environ.get("TWILIO_AUTH_TOKEN")
    call_sid = call_data.get("call_id")
    can_hangup = bool(sid and tok and call_sid)
    serializer = TwilioFrameSerializer(
        stream_sid=call_data["stream_id"],
        call_sid=call_sid,
        account_sid=sid if can_hangup else None,
        auth_token=tok if can_hangup else None,
        params=TwilioFrameSerializer.InputParams(auto_hang_up=can_hangup),
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
        url=os.environ.get("HARU_URL", "ws://127.0.0.1:8000"),
        api_key=os.environ.get("HARU_API_KEY"),
        character=os.environ.get("HARU_CHARACTER", "haru"),
        language=os.environ.get("HARU_LANG", "en"),
        record_dir=os.environ.get("HARU_RECORD_DIR"),
    )
    worker = create_haru_worker(Pipeline([transport.input(), haru, transport.output()]))

    @transport.event_handler("on_client_disconnected")
    async def _on_disconnect(_transport, _client) -> None:
        await worker.cancel()

    runner = WorkerRunner(handle_sigint=False)
    await runner.add_workers(worker)
    await runner.run()
