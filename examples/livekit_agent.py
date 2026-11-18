"""LiveKit Agents full-duplex worker and SIP dialer for Haru-4B.

Setup:
    uv pip install "haru-kit[livekit]"

1. Talk locally with your microphone (no LiveKit server needed):
    HARU_URL=ws://127.0.0.1:8000 python examples/livekit_agent.py console

2. Run as a LiveKit Cloud or self-hosted worker (for WebRTC rooms & Twilio/Telnyx SIP trunks):
    export LIVEKIT_URL="wss://..."
    export LIVEKIT_API_KEY="..."
    export LIVEKIT_API_SECRET="..."
    export HARU_URL="ws://127.0.0.1:8000"   # or wss://...modal.host
    python examples/livekit_agent.py dev

3. Dial an outbound phone call over a LiveKit SIP trunk (Twilio or Telnyx):
    python examples/livekit_agent.py dial +14155550199 --trunk $LIVEKIT_TELNYX_OUTBOUND_TRUNK_ID
"""

from __future__ import annotations

import argparse
import asyncio
import os
import secrets
import sys

from livekit import api
from livekit.agents import Agent, AgentServer, AgentSession, JobContext, cli

from haru_kit.livekit import HaruModel

AGENT_NAME = os.environ.get("LIVEKIT_AGENT_NAME", "haru-agent")
server = AgentServer()


@server.rtc_session(agent_name=AGENT_NAME)
async def entrypoint(ctx: JobContext) -> None:
    speak_first = os.environ.get("HARU_SPEAK_FIRST", "0") == "1"
    session = AgentSession(
        llm=HaruModel(
            url=os.environ.get("HARU_URL", "ws://127.0.0.1:8000"),
            api_key=os.environ.get("HARU_API_KEY"),
            character=os.environ.get("HARU_CHARACTER", "haru"),
            language=os.environ.get("HARU_LANG", "en"),
            speak_first=speak_first,
            record_dir=os.environ.get("HARU_RECORD_DIR"),
        ),
        aec_warmup_duration=None,  # keep first 3s unmuted for full-duplex speech
    )
    await session.start(agent=Agent(instructions=""), room=ctx.room)


async def _dial_outbound(to_number: str, trunk_id: str, from_number: str | None) -> None:
    room_name = f"haru-call-{secrets.token_hex(4)}"
    lk = api.LiveKitAPI()
    try:
        await lk.agent_dispatch.create_dispatch(
            api.CreateAgentDispatchRequest(agent_name=AGENT_NAME, room=room_name)
        )
        req_kwargs: dict = {
            "sip_trunk_id": trunk_id,
            "sip_call_to": to_number,
            "room_name": room_name,
            "participant_identity": f"phone-{to_number}",
            "participant_name": to_number,
            "wait_until_answered": True,
        }
        if from_number:
            req_kwargs["sip_number"] = from_number
        part = await lk.sip.create_sip_participant(api.CreateSIPParticipantRequest(**req_kwargs))
        print(f"Connected {to_number} into LiveKit room {room_name} (call_id={part.sip_call_id})")
    finally:
        await lk.aclose()


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "dial":
        parser = argparse.ArgumentParser(prog="livekit_agent.py dial")
        parser.add_argument("to_number", help="E.164 phone number to call")
        parser.add_argument("--trunk", required=True, help="LiveKit outbound SIP trunk ID (ST_...)")
        parser.add_argument("--from", dest="from_number", default=None, help="optional caller ID number")
        dial_args = parser.parse_args(sys.argv[2:])
        asyncio.run(_dial_outbound(dial_args.to_number, dial_args.trunk, dial_args.from_number))
    else:
        cli.run_app(server)
