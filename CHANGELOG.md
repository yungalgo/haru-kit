# Changelog

## 0.1.0 (2026-11-18)

First public release of `haru-kit`.

### Added
- **Guided CLI (`haru-kit`, `start`, `stop`, `status`, `doctor`, `uninstall`):** detects hardware, checks disk space before downloading, creates isolated `uv` environments with exact upstream package versions (`haru-speech==0.1.1`, `haru-speech-mlx==0.1.0`), and runs Haru-4B without touching the user's Python environment.
- **Mac way (`--on mac --app` / `--browser`):** builds and installs `~/Applications/Haru.app` on Apple silicon Macs (macOS 13+, 16 GB+ RAM) or serves the browser app locally; defaults to int8 weights with an automatic step-time warning when BF16 is too slow.
- **Modal cloud GPU way (`--on modal`):** deploys vLLM + the web server in a single A10 container in the region nearest to the user (`--region auto|us|eu|ap|any`), connects straight via a Modal tunnel with an access key, caches compiled vLLM/FlashInfer/Triton/Inductor kernels in the user's Modal Volume for faster restarts, prompts after 3 minutes if a regional GPU is busy, and stops automatically when idle (15 minutes before the first conversation, 5 minutes after).
- **NVIDIA PC way (`--on nvidia`):** runs natively on Windows and Linux with CUDA 12+ and prints the SSH port-forwarding tunnel command when connected remotely. *(Note: tested in unit tests and on a Linux cloud GPU stand-in; not yet tested on physical Windows or Linux NVIDIA PCs.)*
- **Feature parity across Mac app and web app in 9 languages:** character library with pictures and per-language cards/voices, voice recording and multi-format audio import (stored as trimmed 24 kHz mono WAV), `.haru` pack import/export between apps, shared character registry (**Browse characters**), audio device selection, mute, time remaining, non-jumping transcript scroll, unsaved-conversation prompt, and two-channel WAV (caller left, Haru right) + TXT + video export.
- **API server, telephony, Pipecat, and LiveKit (`serve`, `check`, `phone`):**
  - `haru-kit serve` (`--on mac`, `--on nvidia`, `--on modal`) exposing `/v1/realtime?duplex=1` across all backends, plus `haru-kit check <url>` for real-time audio health and p50/p95 frame latency checks.
  - Shared 133.33 ms drift-free clock client (`haru_kit.client.HaruSession`) with FLAC voice encoding, pre-roll audio notices, `"native"` / `"mute"` interrupt policies, and two-channel 24 kHz WAV + transcript call recording.
  - First-class **Pipecat** (`haru_kit.pipecat.HaruLLMService`) and **LiveKit Agents** (`haru_kit.livekit.HaruModel` implementing `llm.DuplexModel`) adapters, plus runnable templates in `examples/`.
  - One-command **Twilio** and **Telnyx** phone integration (`haru-kit phone`, `haru-kit phone call <to>`, `haru-kit phone stop`) with co-located Pipecat bridge on Modal, webhook signature validation, and automatic number webhook/trunk snapshot & restore.
- **Agent skill (`SKILL.md`):** step-by-step instructions for coding agents (`npx skills add yungalgo/haru-kit`).
