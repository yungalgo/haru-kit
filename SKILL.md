---
name: haru-kit
description: Install, start, serve, phone-connect, check, update and fix Haru-4B (an open full-duplex voice model) with haru-kit on Mac, NVIDIA PC, or Modal cloud GPU; connect Twilio/Telnyx phone numbers or Pipecat/LiveKit voice agents; and share characters in haru-characters.
---

# Set up Haru with haru-kit

haru-kit installs and starts Haru-4B. You run the commands; **the user talks to Haru** in the Mac app or a browser. Some steps need the user's hands (below): tell them exactly what to do and wait.

## 1. Install and check

```bash
uv --version || curl -LsSf https://astral.sh/uv/install.sh | sh
export PATH="$HOME/.local/bin:$PATH"   # ensure uv and haru-kit are on PATH in non-login shells
# Windows (PowerShell): powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
uv tool install haru-kit               # latest from main: uv tool install git+https://github.com/yungalgo/haru-kit
haru-kit --version
haru-kit doctor
```

`haru-kit doctor` prints, for each way, ✓ or ✗ with the exact fix. Choose the way:

| Computer | Way | Needs |
| --- | --- | --- |
| Mac with Apple silicon, 16 GB or more, macOS 13+ | `mac`: `--app` (lowest delay) or `--browser` | about 14 GB free disk; for `--app`, Apple's Command Line Tools |
| Windows or Linux PC with an NVIDIA GPU of 12 GB or more | `nvidia` (browser) | about 20 GB free disk; a driver with CUDA 12 or later |
| Anything else (Intel Mac, 8 GB Mac, other GPUs) | `modal` (a private link) | a Modal account; costs about $1.27 an hour while it runs ($1.10 with `--region any`) |

## 2. Ask before cost

Before you start, tell the user what will happen and get a yes:
- a 10.5 GB model download and about 1–7 GB of engine (Mac, NVIDIA);
- cloud cost (Modal: about $1.27 an hour in the nearest region, or $1.10 with `--region any`; it stops after 15 minutes if no conversation starts, and 5 minutes after a conversation ends; Modal gives $30 free each month).

## 3. Start

```bash
haru-kit start --on mac --app --yes      # or: --on mac --browser | --on nvidia | --on modal   (add --verbose for logs)
```

- The first start takes about 15 minutes on a Mac or NVIDIA PC (download and install), about 6 more minutes to build the Mac app, and about 5–15 minutes on Modal (later Modal starts with saved kernels take about 2–5 minutes). Later local starts: seconds to a minute.
- **Browser and NVIDIA runs keep running until Ctrl+C.** Run the command in the background (or a separate terminal), and wait for the line `Haru is ready`. Check with `curl -s http://localhost:8998/api/ready` → `{"ready": true}`.
- **Mac app**: the command builds and opens `~/Applications/Haru.app`, then returns. The app keeps running.
- **Modal**: the command prints a link with `?key=…`. Give the user the whole link: it is private (anyone with it can use the GPU).
- **Modal region**: haru-kit starts the GPU in the region nearest to the user (from the computer's time zone; 1.15× price = $1.27/hr). If the user is elsewhere than the computer's time zone, or wants the cheapest: `--region us|eu|ap|any`.
- **NVIDIA over SSH**: haru-kit prints `ssh -L 8998:localhost:8998 user@host`. The user runs it on their laptop, then opens `http://localhost:8998` there.

Tell the user: use wired headphones (with speakers, Haru hears itself).

## 4. Steps that need the user

| Step | When | What the user does |
| --- | --- | --- |
| Modal login | first `--on modal`, when `doctor` says not logged in | haru-kit runs `modal setup`: a browser window opens on the user's computer, and the user logs in there. Tell the user before you start. |
| Command Line Tools | first `--app`, when they are missing | haru-kit opens Apple's install window; the user clicks Install (about 5 minutes), then you run the command again. |
| Microphone permission | first call in the Mac app or browser | the user clicks Allow. |
| Talking | always | the user presses Start in the app and talks. You cannot test the conversation for them. |

## 5. Stop, check, update, remove

```bash
haru-kit stop                                               # Modal: stop now (cost stops)
haru-kit status                                             # what is installed, where, sizes; Modal running or not
uv tool upgrade haru-kit                                    # update (the Mac app rebuilds on next --app)
haru-kit uninstall                                          # asks before deleting characters/voices or the model; --yes keeps both
```

## 6. Phone calls, API server, Pipecat, and LiveKit

```bash
haru-kit serve --on mac --yes                               # local /v1/realtime?duplex=1 on port 8000
haru-kit serve --on modal --yes                             # cloud GPU /v1/realtime?duplex=1 + Bearer key
haru-kit check ws://127.0.0.1:8000                          # verify audio frames & latency
haru-kit phone --telnyx --number +1...                      # wire a Telnyx or --twilio number to Haru on Modal
haru-kit phone call +1... --twilio --from +1...             # place an outbound call (Haru speaks first)
haru-kit phone stop                                         # stop Modal GPU & restore number webhook/trunk
```

For Python voice-agent projects:
- **Pipecat:** `uv add "haru-kit[pipecat]"` → `from haru_kit.pipecat import HaruLLMService, create_haru_worker`
- **LiveKit Agents:** `uv add "haru-kit[livekit]"` → `from haru_kit.livekit import HaruModel` (`AgentSession(llm=HaruModel(...), aec_warmup_duration=None)`)

## 7. Characters (for the user)

Characters, voices and settings are in the apps (Mac app or browser), not in the command line. Point the user to **Browse characters** (shared characters) and to the character editor. To share a character: in the app, fill in Author, export it (**Export the character (.haru)**), then upload the `.haru` file to `submissions/` of https://github.com/yungalgo/haru-characters (**Add file → Upload files** on GitHub). A check comments on the pull request.

## When something fails

- Every error says what happened, why, and what to do: follow the printed fix first.
- Logs: `<haru-kit folder>/logs/<way>.log`. macOS: `~/Library/Application Support/haru-kit/logs/`; Windows: `%LOCALAPPDATA%\haru-kit\logs\`; Linux: `~/.local/share/haru-kit/logs/`. Mac app: `~/Library/Logs/Haru/app.log`.
- Port in use: `haru-kit start --port 8999`.
- Not enough disk: free space, or set `HF_HOME` to a folder on another disk before starting.
- Haru stutters on a Mac: in Settings choose **int8** (the app warns when BF16 is too slow).
- Do not install the engines into the user's own Python: haru-kit makes its own environments.
