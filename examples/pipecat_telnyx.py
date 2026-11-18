"""Demo Telnyx TeXML Media Streams webhook & WebSocket bridge for Haru-4B using Pipecat.

Setup:
    uv pip install "haru-kit[pipecat]" fastapi "uvicorn[standard]"

Run:
    export HARU_URL="wss://...modal.host"
    export HARU_API_KEY="hk_..."
    export TELNYX_API_KEY="KEY..."
    export TELNYX_WEBHOOK_TOKEN="choose-a-secret-token"
    uvicorn examples.pipecat_telnyx:app --host 0.0.0.0 --port 8765

Point your Telnyx TeXML application's Voice URL to:
    https://<your-public-host>/telnyx/voice?token=$TELNYX_WEBHOOK_TOKEN
"""

from __future__ import annotations

import hmac
import os
from urllib.parse import urlencode
from xml.sax.saxutils import escape as xml_escape

from fastapi import FastAPI, Request, Response, WebSocket
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import WorkerRunner
from pipecat.runner.utils import parse_telephony_websocket
from pipecat.serializers.telnyx import TelnyxFrameSerializer
from pipecat.transports.websocket.fastapi import FastAPIWebsocketParams, FastAPIWebsocketTransport

from haru_kit.pipecat import HaruLLMService, create_haru_worker

app = FastAPI()


def _valid_token(got: str) -> bool:
    expected = os.environ.get("TELNYX_WEBHOOK_TOKEN", "")
    if not expected:
        return False
    return hmac.compare_digest(got.encode("utf-8"), expected.encode("utf-8"))


@app.post("/telnyx/voice")
@app.get("/telnyx/voice")
async def telnyx_voice(request: Request) -> Response:
    token = request.query_params.get("token", "")
    if not _valid_token(token):
        return Response(content="Forbidden", status_code=403)

    proto = "wss" if (request.headers.get("x-forwarded-proto") or request.url.scheme) == "https" else "ws"
    host = request.headers.get("x-forwarded-host") or request.headers.get("host") or request.url.netloc
    ws_url = xml_escape(f"{proto}://{host}/telnyx/ws?{urlencode({'token': token})}")
    texml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<Response><Connect><Stream url="{ws_url}" bidirectionalMode="rtp"/></Connect></Response>'
    )
    return Response(content=texml, media_type="application/xml")


@app.websocket("/telnyx/ws")
async def telnyx_ws(ws: WebSocket) -> None:
    if not _valid_token(ws.query_params.get("token", "")):
        await ws.close(code=1008)
        return
    await ws.accept()
    _, call_data = await parse_telephony_websocket(ws)

    api_key = os.environ.get("TELNYX_API_KEY")
    call_ctrl_id = call_data.get("call_id")
    outbound_enc = call_data.get("outbound_encoding") or "PCMU"
    can_hangup = bool(api_key and call_ctrl_id)
    serializer = TelnyxFrameSerializer(
        stream_id=call_data["stream_id"],
        outbound_encoding=outbound_enc,
        inbound_encoding="PCMU",
        call_control_id=call_ctrl_id if can_hangup else None,
        api_key=api_key if can_hangup else None,
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
