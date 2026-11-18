# ruff: noqa: E501  (embedded JavaScript)
"""Web app: every own voice is stored as 24 kHz mono 16-bit WAV after the silence trim.

Runs the web app's real conversion (`toStoredAudio` in src/library/audio.ts, bundled by esbuild) in headless
Chrome (muted), with inputs made by ffmpeg from a test voice. The stored length must equal upstream
`prepare_reference(decode_audio(original))` within 480 samples (one 20 ms trim window: the browser and
ffmpeg/soxr resample and pad differently). Skipped when Chrome, Node, ffmpeg, esbuild, or a Python with the
upstream `haru_mlx` package is missing.
"""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from haru_kit.paths import env_python

ROOT = Path(__file__).resolve().parent.parent
AUDIO_TS = ROOT / "haru_kit" / "apps" / "web" / "src" / "library" / "audio.ts"
ESBUILD = ROOT / "haru_kit" / "apps" / "web" / "node_modules" / ".bin" / "esbuild"
SOURCE = ROOT / "tests" / "data" / "test-voice.flac"
CHROME = os.environ.get("CHROME", "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
TOLERANCE = 480

# Input name -> ffmpeg output options.
INPUTS = {
    "flac": [],
    "mp3": ["-c:a", "libmp3lame", "-b:a", "128k"],
    "m4a": ["-c:a", "aac", "-b:a", "128k"],
    "ogg": ["-c:a", "libopus", "-b:a", "48k"],  # Ogg Opus, the clips of the shared registry
    "opus": ["-c:a", "libopus", "-b:a", "48k"],
    "webm": ["-c:a", "libopus", "-b:a", "48k"],
    "wav": ["-ar", "48000", "-ac", "2"],  # another rate and two channels
}


def _upstream_python() -> str | None:
    candidates = [sys.executable, str(env_python("mac"))]
    for python in candidates:
        if Path(python).exists() and subprocess.run([python, "-c", "import haru_mlx.audio"],
                                                    capture_output=True).returncode == 0:
            return python
    return None


UPSTREAM = _upstream_python()
pytestmark = pytest.mark.skipif(
    not (Path(CHROME).exists() and shutil.which("node") and shutil.which("ffmpeg") and ESBUILD.exists() and UPSTREAM),
    reason="needs Chrome, Node, ffmpeg, the web app's node_modules and a Python with haru_mlx")

ENTRY = """
import { toStoredAudio } from %s;
export const convert = async (b64: string) => {
  const bytes = Uint8Array.from(atob(b64), c => c.charCodeAt(0));
  let stored;
  try {
    stored = await toStoredAudio(new Blob([bytes]));
  } catch (error) {
    return { error: String(error) };
  }
  const wav = new DataView(await stored.wav.arrayBuffer());
  const tag = (o: number) => String.fromCharCode(...new Uint8Array(wav.buffer, o, 4));
  return {
    riff: tag(0), wave: tag(8), format: wav.getUint16(20, true), channels: wav.getUint16(22, true),
    rate: wav.getUint32(24, true), bits: wav.getUint16(34, true), data: tag(36),
    frames: wav.getUint32(40, true) / 2, bytes: wav.byteLength, seconds: stored.seconds,
  };
};
"""

# Opens headless Chrome (muted, no keychain), runs the bundle in a blank page through the DevTools
# protocol, prints the result of convert() for each file, and closes Chrome.
DRIVER = r"""
import { spawn } from "node:child_process";
import { existsSync, readFileSync, mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
const [chromePath, bundlePath, ...files] = process.argv.slice(2);
const profile = mkdtempSync(join(tmpdir(), "haru-chrome-"));
const chrome = spawn(chromePath, ["--headless=new", "--mute-audio", "--use-mock-keychain", "--password-store=basic",
  "--no-first-run", "--no-default-browser-check", "--remote-debugging-port=0", `--user-data-dir=${profile}`, "about:blank"],
  { stdio: "ignore" });
try {
  const portFile = join(profile, "DevToolsActivePort");
  for (let i = 0; i < 300 && !existsSync(portFile); i++) await new Promise(r => setTimeout(r, 100));
  const port = readFileSync(portFile, "utf8").split("\n")[0];
  let page;
  for (let i = 0; i < 100 && !page; i++) {
    page = (await (await fetch(`http://127.0.0.1:${port}/json/list`)).json()).find(t => t.type === "page");
    if (!page) await new Promise(r => setTimeout(r, 100));
  }
  const ws = new WebSocket(page.webSocketDebuggerUrl);
  await new Promise((resolve, reject) => { ws.onopen = resolve; ws.onerror = reject; });
  let next = 1;
  const waiting = new Map();
  ws.onmessage = e => {
    const m = JSON.parse(e.data);
    if (waiting.has(m.id)) { waiting.get(m.id)(m); waiting.delete(m.id); }
  };
  const evaluate = expression => new Promise((resolve, reject) => {
    const id = next++;
    waiting.set(id, m => m.error || m.result.exceptionDetails ? reject(new Error(JSON.stringify(m))) : resolve(m.result.result.value));
    ws.send(JSON.stringify({ id, method: "Runtime.evaluate", params: { expression, awaitPromise: true, returnByValue: true } }));
  });
  await evaluate(readFileSync(bundlePath, "utf8"));
  const out = {};
  for (const file of files) {
    out[file] = await evaluate(`conv.convert(${JSON.stringify(readFileSync(file).toString("base64"))})`);
  }
  console.log(JSON.stringify(out));
  ws.close();
} finally {
  chrome.kill();
  await new Promise(r => chrome.once("exit", r));
  rmSync(profile, { recursive: true, force: true });
}
"""

UPSTREAM_LENGTHS = """
import json, sys
from pathlib import Path
from haru_mlx.audio import decode_audio, prepare_reference
print(json.dumps({f: int(prepare_reference(decode_audio(Path(f).read_bytes())).shape[0]) for f in sys.argv[1:]}))
"""


def test_every_audio_type_is_stored_as_24khz_mono_16bit_wav(tmp_path):
    files = []
    for ext, options in INPUTS.items():
        out = tmp_path / f"in.{ext}"
        subprocess.run(["ffmpeg", "-loglevel", "error", "-i", str(SOURCE), *options, str(out)], check=True)
        files.append(str(out))
    junk = tmp_path / "junk.mp3"
    junk.write_bytes(os.urandom(4096))  # not audio: the conversion must fail

    (tmp_path / "entry.ts").write_text(ENTRY % json.dumps(str(AUDIO_TS)))
    subprocess.run([str(ESBUILD), "entry.ts", "--bundle", "--format=iife", "--global-name=conv", "--outfile=entry.js",
                    "--log-level=error"], check=True, cwd=tmp_path)
    (tmp_path / "driver.mjs").write_text(DRIVER)
    run = subprocess.run(["node", "driver.mjs", CHROME, "entry.js", *files, str(junk)], capture_output=True, text=True,
                         cwd=tmp_path, timeout=180)
    assert run.returncode == 0, run.stderr
    stored = json.loads(run.stdout)
    expected = json.loads(subprocess.run([UPSTREAM, "-c", UPSTREAM_LENGTHS, *files], capture_output=True, text=True,
                                         check=True).stdout)

    assert "error" in stored[str(junk)]
    for file in files:
        wav = stored[file]
        assert "error" not in wav, (file, wav)
        assert (wav["riff"], wav["wave"], wav["data"]) == ("RIFF", "WAVE", "data"), file
        assert (wav["format"], wav["channels"], wav["rate"], wav["bits"]) == (1, 1, 24000, 16), file
        assert wav["bytes"] == 44 + 2 * wav["frames"], file
        assert wav["seconds"] == pytest.approx(wav["frames"] / 24000), file
        assert abs(wav["frames"] - expected[file]) <= TOLERANCE, (file, wav["frames"], expected[file])
