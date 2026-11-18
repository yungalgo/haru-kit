# Examples

Ready-to-run full-duplex voice agents powered by **Haru-4B** (`haru_kit.pipecat` and `haru_kit.livekit`).

> **Just want to connect a phone number?** You don't need to host any webhook server yourself. Run `haru-kit phone --twilio` or `haru-kit phone --telnyx` and `haru-kit` deploys a co-located phone gateway inside the Modal GPU container automatically. Use the scripts below when you are embedding Haru into your own Pipecat or LiveKit application.

## Which file should I start from?

| Script | Framework | Transport | Local or Cloud |
|---|---|---|---|
| [`pipecat_local.py`](./pipecat_local.py) | Pipecat (`HaruLLMService`) | Computer mic & headphones (`LocalAudioTransport`) | Local (`--on mac` / `--on nvidia`) or Modal (`--on modal`) |
| [`pipecat_twilio.py`](./pipecat_twilio.py) | Pipecat (`HaruLLMService`) | Twilio Media Streams (`<Connect><Stream>`) + `X-Twilio-Signature` check | Self-hosted FastAPI or Pipecat Cloud |
| [`pipecat_telnyx.py`](./pipecat_telnyx.py) | Pipecat (`HaruLLMService`) | Telnyx TeXML Media Streams (`bidirectionalMode="rtp"`) | Self-hosted FastAPI or Pipecat Cloud |
| [`livekit_agent.py`](./livekit_agent.py) | LiveKit Agents (`HaruModel`) | Local mic (`console`), WebRTC rooms (`dev`), & Twilio/Telnyx SIP trunks (`dial`) | Local, self-hosted LiveKit, or LiveKit Cloud |

---

## 1. Start a Haru-4B Realtime Server

Every example connects to a `haru-kit serve` endpoint over `ws://` or `wss://`:

```bash
# Local Apple Silicon Mac (ws://127.0.0.1:8000)
haru-kit serve --on mac

# Local Linux / Windows with NVIDIA GPU (ws://127.0.0.1:8000)
haru-kit serve --on nvidia

# Serverless GPU on Modal (prints wss://...modal.host + Bearer API key)
haru-kit serve --on modal
```

Verify latency and audio round-trip before wiring your app:

```bash
haru-kit check ws://127.0.0.1:8000
```

---

## 2. Common Environment Variables

All four scripts read the same Haru connection variables:

| Variable | Default | Description |
|---|---|---|
| `HARU_URL` | `ws://127.0.0.1:8000` | Base URL or full `/v1/realtime` WebSocket URL (`ws://` or `wss://`) |
| `HARU_API_KEY` | *(unset)* | Bearer API key printed by `haru-kit serve --on modal` |
| `HARU_CHARACTER` | `haru` | Character ID from `~/.haru-kit/library/` or the Haru character registry (`haru`, `kai`, ...) |
| `HARU_LANG` | `en` | Language code (`en`, `ja`, `zh`, `ko`, `de`, `fr`, `es`, `pt`, `ru`) |
| `HARU_RECORD_DIR` | *(unset)* | Optional folder to save 2-channel 24 kHz `.wav` + `.txt` call recordings |

---

## 3. Running Each Example

### Local Microphone with Pipecat (`pipecat_local.py`)

```bash
# macOS requires PortAudio for PyAudio: brew install portaudio
uv pip install "haru-kit[pipecat]" "pipecat-ai[local]"

HARU_URL=ws://127.0.0.1:8000 python examples/pipecat_local.py
```

### Custom Twilio Webhook Server (`pipecat_twilio.py`)

```bash
uv pip install "haru-kit[pipecat]" fastapi "uvicorn[standard]"

export HARU_URL="wss://your-endpoint.modal.host"
export HARU_API_KEY="hk_..."
export TWILIO_ACCOUNT_SID="AC..."
export TWILIO_AUTH_TOKEN="..."

uvicorn examples.pipecat_twilio:app --host 0.0.0.0 --port 8765
```

Point your Twilio number's **A call comes in** webhook (`HTTP POST`) to `https://<your-public-host>/twilio/voice`.

### Custom Telnyx TeXML Server (`pipecat_telnyx.py`)

```bash
uv pip install "haru-kit[pipecat]" fastapi "uvicorn[standard]"

export HARU_URL="wss://your-endpoint.modal.host"
export HARU_API_KEY="hk_..."
export TELNYX_API_KEY="KEY..."
export TELNYX_WEBHOOK_TOKEN="choose-a-secret-token"

uvicorn examples.pipecat_telnyx:app --host 0.0.0.0 --port 8765
```

Point your Telnyx TeXML application's Voice URL to `https://<your-public-host>/telnyx/voice?token=$TELNYX_WEBHOOK_TOKEN`.

### LiveKit WebRTC Rooms & Twilio/Telnyx SIP Trunks (`livekit_agent.py`)

```bash
uv pip install "haru-kit[livekit]"

# A) Local console mic test (no LiveKit server needed)
HARU_URL=ws://127.0.0.1:8000 python examples/livekit_agent.py console

# B) Connect worker to LiveKit Cloud or self-hosted LiveKit
export LIVEKIT_URL="wss://your-project.livekit.cloud"
export LIVEKIT_API_KEY="..."
export LIVEKIT_API_SECRET="..."
export HARU_URL="wss://your-endpoint.modal.host"
export HARU_API_KEY="hk_..."
python examples/livekit_agent.py dev

# C) Place an outbound SIP call (via a Twilio or Telnyx SIP trunk attached to LiveKit)
python examples/livekit_agent.py dial +14155550199 --trunk ST_xxxxxxxxxxxx
```
