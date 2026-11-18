# haru-kit

Talk to [Haru-4B](https://huggingface.co/higurashi-ai/Haru-4B), an open-source full-duplex voice model: it listens and talks at the same time, like a person on a phone call. It is trained in 9 languages: English, Japanese, Chinese, Korean, Spanish, French, German, Portuguese and Russian.

- **Talk to Haru** on a Mac, on a Windows or Linux PC with an NVIDIA GPU, or on any other computer through a cloud GPU. One command sets it up.
- **Make characters and voices** in those 9 languages: who Haru is, how it speaks, and with which voice.
- **Share characters** in the [character registry](https://github.com/yungalgo/haru-characters), and add the characters of other people with one click.
- **Call Haru on a real phone, or plug it into Pipecat & LiveKit:** connect a **Twilio** or **Telnyx** phone number in one command (`haru-kit phone`), or drop Haru-4B into **Pipecat** and **LiveKit Agents** (cloud or self-hosted).

## Quick start

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh          # install uv (Windows: see below)
uv tool install haru-kit
haru-kit
```

`haru-kit` looks at your computer, recommends a way, shows what it will download and where, and asks before it starts. Next time, `haru-kit start` repeats your choice. Use wired headphones: with speakers, Haru can hear itself.

- **Windows:** install `uv` with `powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"`.
- **Upgrade later:** `uv tool upgrade haru-kit` (or install the latest from `main`: `uv tool install git+https://github.com/yungalgo/haru-kit`).

**With a coding agent** (Claude Code, Codex, Cursor, …), ask it:

> Read https://github.com/yungalgo/haru-kit/blob/main/SKILL.md and set up Haru on this computer.

To keep [`SKILL.md`](SKILL.md) as a skill in your agents: `npx skills add yungalgo/haru-kit`.

## Ways to run Haru

| Your computer | Command | First start | Next starts | Cost |
| --- | --- | --- | --- | --- |
| Mac with Apple silicon, 16 GB or more | `haru-kit start --on mac --app` (Mac app) or `--browser` | about 15 min (10.5 GB model) + 6 min to build the app | seconds | free |
| PC with an NVIDIA GPU, 12 GB or more (Windows or Linux) — **not tested in practice yet** | `haru-kit start --on nvidia` | about 15–20 min (10.5 GB model, 7 GB engine) | about 1 min | free |
| Any other computer (Intel Mac, 8 GB Mac, other GPUs) | `haru-kit start --on modal` | about 5–15 min | about 2–5 min | about $1.27 an hour while it runs |

- **Mac:** the Mac app has the lowest delay. Haru starts in int8 (faster, smaller); choose BF16, the published precision, in Settings. On a slower Mac BF16 can stutter, and the app warns you.
- **NVIDIA PC:** talk in the browser. Windows runs natively (no WSL). Over SSH, haru-kit prints a tunnel command such as `ssh -L 8998:localhost:8998 you@gpu-box`: run it on your laptop, then open `http://localhost:8998` there. **Not tested in practice yet:** neither Windows nor Linux has run on a real NVIDIA PC (only unit tests, and a Linux check on a cloud GPU). Please [report](https://github.com/yungalgo/haru-kit/issues) what happens.
- **Cloud ([Modal](https://modal.com)):** you get a private link that works in any browser, also on a phone. You need a Modal account (haru-kit helps you log in; Modal gives $30 of use each month). If you do not start a conversation within 15 minutes after Haru is ready (the screen shows the time), it stops by itself; after a conversation, it stops after 5 minutes with no conversation. `haru-kit stop` stops it at once.
  - **Region:** the delay depends on the distance between you and the GPU. haru-kit starts the GPU in the region nearest to you (North America, Europe, or Asia-Pacific), which it reads from your computer's time zone (nothing is sent anywhere), and connects your browser straight to it. Modal charges 1.15× for a chosen region: about $1.27 an hour instead of $1.10. To change it: `haru-kit start --on modal --region us|eu|ap`, or `--region any` (the cheapest: any region, but possibly far away). haru-kit remembers your choice.

## Characters and voices

The Mac app and the browser have the same features:

- **Characters:** choose one from the library, or make your own: a picture, and for each language a card (who Haru is, your relationship, the situation) and a voice. The app shows the exact text that the model reads.
- **Voices:** record your own (10 to 30 seconds is enough, up to 3 minutes) or upload an audio file. Haru speaks a language best with a voice in that language; the app warns when they differ.
- **Shared characters:** **Browse characters** adds characters from the [character registry](https://github.com/yungalgo/haru-characters). To share yours, export it and upload it there.
- **Moving them:** export a character or a voice as a `.haru` file, and import it in the other app.

## Phone calls, Pipecat, and LiveKit

### 1. Connect a phone number in one command (`haru-kit phone`)

Connect a **Twilio** or **Telnyx** number to Haru-4B (with a co-located Pipecat bridge inside the same Modal GPU container, so there is zero network hop between the phone bridge and the model):

```bash
haru-kit phone                                         # guided setup (Twilio or Telnyx)
haru-kit phone --telnyx --number +14155550100          # inbound calls -> Haru
haru-kit phone --twilio --buy                          # buy a ~$1/mo number and wire it
haru-kit phone call +15551234567 --telnyx              # place an outbound call (Haru speaks first)
haru-kit phone stop                                    # stop the GPU & restore previous number settings
```

### 2. Run the WebSocket API server (`haru-kit serve` & `check`)

Start Haru's `/v1/realtime?duplex=1` WebSocket API server locally on a Mac / NVIDIA GPU or in the cloud on Modal, and verify latency & audio from any machine:

```bash
haru-kit serve --on mac                                # ws://127.0.0.1:8000/v1/realtime?duplex=1
haru-kit serve --on modal                              # wss://...modal.host/v1/realtime?duplex=1 + Bearer key
haru-kit check wss://...modal.host --api-key hk_...    # streams caller audio and prints p50/p95 + transcript
```

### 3. Pipecat (`uv add "haru-kit[pipecat]"`)

Works self-hosted or on **Pipecat Cloud**, with **Twilio**, **Telnyx**, WebRTC, or local audio. Replaces VAD + STT + LLM + TTS in one full-duplex service (disables framework interruptions automatically and applies a `-65 dBFS` idle gate):

```python
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import WorkerRunner
from haru_kit.pipecat import HaruLLMService, create_haru_worker

haru = HaruLLMService(
    url="wss://...modal.host",           # or ws://127.0.0.1:8000 on Mac
    api_key="hk_...",
    character="haru",                    # library character ID, dict, file path, or raw card text
    language="en",
    record_dir="./calls",                # optional 2-channel 24 kHz WAV + transcript
)
worker = create_haru_worker(Pipeline([transport.input(), haru, transport.output()]))
```

Runnable demos in [`examples/`](examples): [`pipecat_local.py`](examples/pipecat_local.py), [`pipecat_twilio.py`](examples/pipecat_twilio.py), and [`pipecat_telnyx.py`](examples/pipecat_telnyx.py).

### 4. LiveKit Agents (`uv add "haru-kit[livekit]"`)

Implements LiveKit's native full-duplex `llm.DuplexModel` interface. Works on **LiveKit Cloud** or self-hosted `livekit-server` + `livekit-sip`, with **Twilio** or **Telnyx** SIP trunks (or native LiveKit phone numbers):

```python
from livekit.agents import Agent, AgentSession
from haru_kit.livekit import HaruModel

session = AgentSession(
    llm=HaruModel(url="wss://...modal.host", api_key="hk_...", character="haru", language="en"),
    aec_warmup_duration=None,            # keeps the first 3s unmuted for full-duplex speech
)
await session.start(agent=Agent(instructions=""), room=ctx.room)
```

See [`examples/livekit_agent.py`](examples/livekit_agent.py) (`console` mic mode, `dev` room worker, and `dial +1... --trunk ST_...` outbound SIP helper).

## Commands

| Command | What it does |
| --- | --- |
| `haru-kit` | The guide: check this computer, choose a way, start |
| `haru-kit start` | Start again with your last choice (`--on mac\|nvidia\|modal`, `--app`, `--browser`, `--port N`, `--quantize 8\|none`, `--region auto\|us\|eu\|ap\|any`) |
| `haru-kit serve` | Start the `/v1/realtime?duplex=1` WebSocket API server (`--on mac\|nvidia\|modal`, `--api-key`, `--max-calls`) |
| `haru-kit check <url>` | Run a real-time audio health check against any Haru API server URL |
| `haru-kit phone` | Connect a Twilio or Telnyx phone number to Haru (`phone`, `phone call <to>`, `phone stop`) |
| `haru-kit stop` | Stop Haru on Modal (and restore any phone number connected via `haru-kit phone`). Local runs stop with Ctrl+C, or when you quit the app. |
| `haru-kit status` | What is installed, where, how large, and active Modal / phone state |
| `haru-kit doctor` | Check this computer and each way, with the fix for each problem |
| `haru-kit uninstall` | Remove what haru-kit installed. It asks before deleting your characters/voices or the model. |

Common flags: `--yes` (accept the recommendation), `--verbose`, `--version`.

## About Haru-4B

Haru-4B (by Higurashi AI, Apache 2.0) is a **pretrained base model** for full-duplex speech: every 133 ms it hears a slice of your audio and speaks a slice of its own. It was trained on over 13 million hours of two-person conversations to continue a conversation the way a person would. It has **not been post-trained** (no instruction tuning): it plays the person on its card, and it does not follow instructions like an assistant. So expect this:

- It talks when the line is silent.
- It is slow to stop when you interrupt it (1 to 3 seconds).
- It invents facts, such as prices and phone numbers.
- It can read its card aloud, or repeat the words of its voice clip.

Higurashi AI releases it "for research" and as "a foundation for further fine-tuning". Their [announcement](https://blog.higurashi.ai/melte/haru4b/) says that they are fine-tuning Haru on two-channel conversation recordings, and that they plan to release a more detailed technical report and the training code. As of November 16, 2026, neither is released: the [Haru repository](https://github.com/higurashi-ai/Haru) has inference code only, and their [own post](https://x.com/HigurashiAI/status/2121046072441336139) calls it an unstable model before fine-tuning.

## For developers

### What haru-kit adds to Haru-4B

Haru-4B ships the model, a web demo and a Mac app for developers. Both apps can already talk, pick a voice file, record a voice, and edit a card. haru-kit adds what is around that:

| | Haru-4B alone | With haru-kit |
| --- | --- | --- |
| **Mac with Apple silicon** | Clone the repo, set up Python, build the Mac app by hand. | One command installs everything and opens the Mac app or the browser. |
| **PC with an NVIDIA GPU** | Clone the repo, install CUDA PyTorch and the server by hand (Linux; Windows is not described). Over SSH, find the tunnel yourself. | One command, on Linux and natively on Windows. Over SSH, it prints the tunnel command. |
| **Any other computer** | No way to run it, unless you rent a GPU server and set it up yourself. | A private link on a cloud GPU that stops by itself. |
| **First conversation** | No voice ships. You must find a voice clip first. | Adds shared characters with voices in the 9 languages at first launch. Press Start. |
| **Characters** | One example card. You can edit it, but not keep several characters. | A library: many characters, each with a picture and a version for each language. |
| **Voices** | One voice at a time. The web demo forgets uploads when the server restarts; the Mac app keeps none. | A voice library: every voice is kept, with a name and a language. |
| **Sharing** | None. | `.haru` files and shared characters (**Browse characters**). |
| **During and after a call** | No mute; the text jumps to the end. Save audio or video. | Mute, time left, text that stays where you read. Also save two-channel audio (you left, Haru right) and the text. |
| **Web app vs. Mac app** | Different features and settings. | The same in both, with screens in the 9 languages. |
| **Phone calls & agent frameworks** | Raw vLLM WebSocket only; no Mac API server, clock client, or telephony integrations. | `haru-kit serve` (Mac, NVIDIA, Modal), `haru-kit check`, 133 ms clock client, **Pipecat** & **LiveKit** plugins, and 1-command **Twilio** / **Telnyx** phone calls (`haru-kit phone`). |
| **When something fails** | A spinner or a Python error. | What happened, why, and what to do. `haru-kit doctor` checks everything. |

### How it works

- The `haru-kit` CLI imports no ML engine and installs nothing into your global Python (`uv tool install haru-kit` includes only lightweight WebSocket/audio client libraries; `[pipecat]` and `[livekit]` are optional extras for agent code).
- Each way gets its own `uv` environment with exact upstream versions (`haru-speech==0.1.1`, `haru-speech-mlx==0.1.0`, `modal==1.6.3`). The upstream model packages run unchanged.
- The scripts in `haru_kit/runtime/` run inside those environments: `web_server.py` (browser app), `api_server.py` (`/v1/realtime?duplex=1` for Mac and NVIDIA), `download.py`, and `modal_app.py` / `modal_gateway.py` (vLLM + web server or API/Pipecat phone gateway in one GPU container in the chosen region, reached through a direct Modal tunnel with saved compiled kernels, authentication, and idle auto-stop).
- `haru_kit/apps/web` and `haru_kit/apps/mac` are our copies of the upstream web app and Mac app (`haru_kit/apps/UPSTREAM.md`). The web app ships built in `haru_kit/static`; the Mac app is built on the user's Mac.
- Characters come from the shared registry (`https://yungalgo.github.io/haru-characters/`). At the first start, each app adds all of them to your library. A library has `characters/`, `voices/` and `images/`; a `.haru` file is a ZIP of the same files.

### Where the files go

| What | macOS | Windows | Linux |
| --- | --- | --- | --- |
| Engines, settings, logs | `~/Library/Application Support/haru-kit` | `%LOCALAPPDATA%\haru-kit` | `~/.local/share/haru-kit` |
| Model (10.5 GB) | Hugging Face cache (`$HF_HOME`, default `~/.cache/huggingface`) | same | same |
| Mac app | `~/Applications/Haru.app` | – | – |
| Your characters and voices | Mac app: `~/Library/Application Support/haru-kit/library`; browser: the browser | the browser | the browser |

To keep the model on another disk, set `HF_HOME` before you run haru-kit.

### Running in production

haru-kit is for one person who starts Haru, talks, and stops it. For many users, use the upstream parts directly:

- Run upstream `vllm serve` with `haru_vllm` on GPU servers near your users. Set the conversations for each GPU in the deploy config (`max_sessions`, `max_num_seqs`; 2 in the standard file).
- Put a load balancer in front that sends each new conversation to a server with a free slot. A full server refuses a conversation (`resource_exhausted`); it has no queue.
- Browser users: the upstream web server with `--backend vllm`, HTTPS, and a TURN server for WebRTC.
- Phone & voice-agent users (**Pipecat Cloud**, **LiveKit Cloud**, or self-hosted workers): host your lightweight Python agent on Pipecat Cloud or LiveKit Cloud (neither hosts a 4B GPU model) and point `HARU_URL` at your GPU server (`vllm serve` or `haru-kit serve --on modal`) in the nearest region so each 133 ms frame has minimal network RTT.
- Monitor the frame time (p95 well below 133 ms) and the reply latency.

### Development

```bash
uv venv --python 3.12 && uv pip install -e ".[dev]"
.venv/bin/ruff check haru_kit tests scripts examples && .venv/bin/pytest -q
scripts/build-web.sh              # rebuild the web app into haru_kit/static (Node 20 or newer)
python3 scripts/gen_strings.py    # screen text of both apps, from haru_kit/apps/strings/<language>.json
haru_kit/apps/mac/Tests/run.sh    # logic tests of the Mac app
```

### Releases

Versions are `MAJOR.MINOR.PATCH` (`haru_kit/__init__.py`). A release is a Git tag `vMAJOR.MINOR.PATCH` on `main` (for example `v0.1.0`), with the same number in `__init__.py`, published automatically to PyPI and GitHub Releases.

## Credits & License

Licensed under the [Apache License 2.0](LICENSE). Built on top of [`higurashi-ai/Haru`](https://github.com/higurashi-ai/Haru) ([technical blog](https://blog.higurashi.ai/melte/haru4b/)). Independent open-source project; not affiliated with or endorsed by Higurashi AI. See [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md) for upstream notices.
