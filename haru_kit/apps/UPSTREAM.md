# Upstream copies

| Folder | Upstream | Base commit | License |
| --- | --- | --- | --- |
| `mac/` | [`higurashi-ai/Haru`](https://github.com/higurashi-ai/Haru) `haru_mlx/swift/` | `7fe6b76` (Release haru-speech and haru-speech-vllm 0.1.1) | Apache-2.0 |
| `web/` | [`higurashi-ai/Haru`](https://github.com/higurashi-ai/Haru) `client/` | `7fe6b76` (same) | MIT (`web/LICENSE`) |

Both copies started from commit `7fe6b76` of `higurashi-ai/Haru` and have been extended for `haru-kit` with:

- Shared 9-language UI string tables (`strings/*.json` → `scripts/gen_strings.py`).
- Character and voice libraries with language versions, pictures, `.haru` ZIP import/export, and the shared character registry (**Browse characters**).
- Settings parity (audio device selection and follow, int8 default precision with BF16 step-time warning, persistent settings) and live-view improvements (mute, time remaining, non-jumping scroll, unsaved-conversation prompt, stereo WAV and TXT saving).

The web app ships built in `haru_kit/static/` (`scripts/build-web.sh`, Node 20 or newer), so users do not need Node.js; `haru-kit`'s web server serves it via `ServerOptions(static_dir=...)`. The Mac app is built on the user's Mac from `haru_kit/apps/mac/` into `~/Applications/Haru.app`.
