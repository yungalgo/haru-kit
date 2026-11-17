# Haru for macOS (haru-kit)

Modified from [`higurashi-ai/Haru`](https://github.com/higurashi-ai/Haru) `haru_mlx/swift` (Apache-2.0; see `../UPSTREAM.md`).

`Haru.app` lets you talk with Haru on your Mac: it listens and speaks at the
same time, with backchannels and interruptions. The app does the audio with
Core Audio and runs the model in a Python process (`haru_mlx`, on MLX) that it
starts by itself.

## Requirements

- A Mac with Apple silicon, macOS 13 or later. The model is about 10.5 GB on
  disk; 16 GB of memory is the practical minimum.
- The Command Line Tools (for `swiftc`; Xcode itself is not needed).
- Wired headphones and a wired or USB microphone (see [Audio devices](#audio-devices)).

## Setup

Normally `haru-kit` builds and installs `~/Applications/Haru.app` automatically:

```bash
haru-kit start --on mac --app
```

To build manually from this folder (`haru_kit/apps/mac`) against an environment that has `haru-speech-mlx` installed:

```bash
xcode-select --install    # skip if `xcode-select -p` prints a path
PYTHON="$HOME/Library/Application Support/haru-kit/envs/mac/bin/python" ./build_app.sh
open build/Haru.app
```

This writes `build/Haru.app` and (unless `SKIP_CLI=1`) the command-line client `.build/haru-client`.

Allow microphone access when asked. The app starts the model server
(`python -m haru_mlx.native`); the first launch downloads the model
(`higurashi-ai/Haru-4B`) from Hugging Face.

Build the app on each Mac: it records the path of its Python when it is built,
so a copy moved to another Mac cannot start the server.

## Using the app

The globe menu at the top of the window sets the language. It is the language
of the app's text and the language that Haru speaks: the prompt headings, the
default voice, the library filter and the character titles follow it, and the
selected character changes to its version in this language. When the character
has no version in this language, the app tells you to write one or to choose
another character. At the first start, the language is the first language in
the system's list of preferred languages that the model speaks (one of its nine
languages), else English. The app remembers the choice. The conversation screen
has no globe menu: the language cannot change while Haru speaks.

1. **Character**: choose a character in the library, or make a new one. On
   the first start, the app adds every character of the shared list (the
   haru-characters registry) to your library; when the registry does not
   answer, the library stays empty and the app tries again at the next start.
   The editor shows the picture (click it to choose or replace it), the name
   in the library, the short description and the author, then the 7 card
   fields of the version in the current language, each with a short help text,
   and below them the text that the model reads. You can change, save, undo,
   duplicate and delete every character, and write a version for another
   language. A character must have a picture: Save stays off until it has one.
   The app asks before it discards unsaved changes. The tiles show a
   character's version and the date of its last save, if it has them.
   **Browse characters** opens the shared list: "Add to my library" downloads
   one, checks its size and SHA-256, and adds it with its voices; "Update"
   replaces it when the list has another version. The registry is
   `HARU_KIT_REGISTRY` (at launch), else the `registryURL` setting that
   haru-kit writes, else `https://yungalgo.github.io/haru-characters/`; an
   empty address hides the button and the first-start step.
2. **Voice**: choose a voice in the voice library (play it first), or add your
   own: record it with the microphone of Settings (read the text on the
   screen; at least 3 s, it stops by itself at 3 minutes; 10 to 30 s is
   enough) or upload a file (WAV, FLAC, MP3, M4A/AAC, Ogg, Opus, AIFF,
   CAF). The preview plays what the model gets: the
   audio with the silence at the start and the end removed, at most its last
   180 s. The app stores every added voice in one format: this audio as a WAV
   file (24 kHz, mono, 16-bit), also a voice from a `.haru` file. You can delete every voice. When the voice
   speaks another language than the text, the app warns (Start is not blocked).
3. **Import and export**: "Export the character" writes a `.haru` file (the
   character with all its versions, its picture and its voices); "Import a
   .haru file" reads one made by this app or the web app.
4. **Settings**: the microphone and the output (Automatic follows the devices
   you plug in or remove; a device you choose stays while it is there), the
   CFG scale, the margin (playback slack for a late model step), the precision
   (int8 by default: smaller and faster; or BF16, the published precision;
   switching reloads the model). The app keeps all settings between launches.
5. Click **Start conversation** and talk. A few seconds after your last edit
   of the card or the voice, the server reads them into the model in the
   background, and **Ready** appears next to the button; the conversation then
   starts as soon as you click. The screen shows your voice and the model's as two
   waveform lanes on one clock, the model's words as they are heard, the
   reply latency and the time left (60 minutes at most). **Mute** sends silence
   in place of your microphone.
6. Click **End conversation**. You can then save the conversation's audio
   (WAV: you on the left, the model on the right, as heard), the model's text
   (TXT) or a video of the screen's lanes and transcript (MP4). The app keeps
   the last conversation until the next one starts. If you did not save it,
   the app asks before **Start conversation** and before you quit: **Save…**
   stops so that you can save it, **Don't save** continues.

Your characters, voices and pictures are in
`~/Library/Application Support/haru-kit/library`. Quitting the app stops
the server it started. The server's output and each conversation's latency
are logged to `~/Library/Logs/Haru/app.log`.

## Audio devices

Use wired headphones and a wired or USB microphone: the devices' own latency
adds to the reply latency. Bluetooth headphones add 100 ms and more; the built-in microphone and speakers
of a MacBook add tens of milliseconds of processing. "Automatic" picks a wired or
USB microphone first, and the app warns when the chosen devices add a lot of latency.

## Tests

```bash
./Tests/run.sh       # logic tests: device rule, silence trim, ZIP, library and .haru import, saved WAV,
                     # the registry client against TEST_REGISTRY (default http://localhost:8970/; skipped
                     # when it does not answer; TEST_REGISTRY_UPDATE: a copy with a newer version), and
                     # clips of 11 audio types stored as WAV against upstream's trim (needs ffmpeg and
                     # PYTHON with haru_mlx)
./Tests/record.sh    # records 2 s from "BlackHole 2ch" (or the device you name) with the voice recorder
./Tests/screens.sh   # renders the screens in several states to /tmp/haru-screens (no model needed)
```

## Troubleshooting

| Symptom | Fix |
|---|---|
| `swiftc not found` when building | Install the Command Line Tools (step 1). |
| `haru_mlx is not installed` when building | Create the environment (step 2), or pass `PYTHON=`. |
| The app says the server could not start | The launch screen shows the server's output. Try `python -m haru_mlx.native` in a terminal. |
| No sound | Check the output in Settings: the app uses the device chosen there. |
| Choppy playback | Raise the margin, use wired devices, and close heavy apps. |
| "Unidentified developer" warning | Build the app on that Mac instead of copying it. |

## Command-line client

`.build/haru-client` talks to a server you start yourself, with the server's
default card and the voice clip you pass; on exit it prints the reply latency.

```bash
python -m haru_mlx.native            # in one terminal; wait for "ready on ..."
.build/haru-client --list-devices
.build/haru-client --voice voice.wav --input "MacBook Pro Microphone" --output "MacBook Pro Speakers"
```

Options: `--voice` (the voice the model speaks with), `--input`, `--output`
(device names, default: the system devices), `--buffer-ms` (2), `--margin-ms`
(15), `--seconds`, `--socket` (default: `haru.sock` in `$TMPDIR`, the server's
default).

## How it works

The app and the client do the audio I/O with Core Audio (HAL output units with
small device buffers) and talk to the model server over a Unix socket; the
messages are described at the top of `haru_mlx/native.py`. Input devices that
do not run at 48 kHz are resampled in the client; output runs at 48 kHz.

Both clients measure the reply latency on the audio clock, from the last
sample of each user frame at the microphone converter to the first sample of
the model frame that answers it at the speaker converter. Model frame 0 starts
playing when model frame 1 can follow it without a gap: after the input
buffer, the model step (measured by the server at start-up), the output buffer
and device latency, and the margin. When a frame arrives with more than the
margin queued, the client shortens it slightly (pitch unchanged) until
playback is back on schedule, so a late step costs a moment of silence instead
of permanent latency.
