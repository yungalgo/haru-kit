# ruff: noqa: E501  (embedded Swift and TypeScript programs)
"""A .haru file written by the web app opens in the Mac app, and the opposite, with the same card and voice audio.

Uses the real code of both apps: the web app's zip and library modules (Node 22.6+ strips the types) and the
Mac app's LibraryStore, HaruFile, Zip and audio conversion (swiftc). The Mac app stores an imported voice as a
24 kHz mono 16-bit WAV after the silence trim; the web app then reads those exact bytes.
Skipped when a tool is missing.
"""

import base64
import hashlib
import io
import json
import shutil
import subprocess
import sys
import wave
from pathlib import Path

import pytest
from test_card_parity import _node_strips_types

from haru_kit.paths import env_python

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "haru_kit" / "apps" / "web" / "src" / "library"
MAC = ROOT / "haru_kit" / "apps" / "mac" / "App"
DATA = ROOT / "tests" / "data"
ESBUILD = ROOT / "haru_kit" / "apps" / "web" / "node_modules" / ".bin" / "esbuild"  # from `npm ci` (vite)
CHAR_ID, VOICE_ID = "u-0123456789ab", "u-ba9876543210"
# The stored voice's sample count may differ from upstream's by one 20 ms window of the trim.
TOLERANCE = 480


def upstream_reference_samples(path: Path) -> int | None:
    """len(prepare_reference(decode_audio(...))) of upstream haru_mlx: in this Python or haru-kit's Mac env."""
    code = ("import sys; from haru_mlx.audio import decode_audio, prepare_reference; "
            "print(len(prepare_reference(decode_audio(open(sys.argv[1], 'rb').read()))))")
    for python in (Path(sys.executable), env_python("mac")):
        if python.exists():
            result = subprocess.run([str(python), "-c", code, str(path)], capture_output=True, text=True)
            if result.returncode == 0:
                return int(result.stdout)
    return None


pytestmark = pytest.mark.skipif(not (_node_strips_types() and shutil.which("swiftc") and ESBUILD.exists()),
                                reason="needs Node, swiftc and the web app's node_modules")


def node(tmp: Path, code: str) -> str:
    """Run a script that imports the web app's TypeScript (bundled by esbuild: the app imports without extensions)."""
    name = hashlib.md5(code.encode()).hexdigest()[:8]
    (tmp / f"{name}.ts").write_text(code)
    bundle = subprocess.run([str(ESBUILD), f"{name}.ts", "--bundle", "--platform=node", "--format=esm",
                             f"--outfile={name}.mjs", "--log-level=error"], capture_output=True, text=True, cwd=tmp)
    assert bundle.returncode == 0, bundle.stderr
    result = subprocess.run(["node", f"{name}.mjs"], capture_output=True, text=True, cwd=tmp)
    assert result.returncode == 0, result.stderr
    return result.stdout


@pytest.mark.parametrize("clip", ["flac", "ogg"])
def test_haru_file_round_trip_between_the_apps(tmp_path, clip):
    haru = json.loads((DATA / "haru.character.json").read_text(encoding="utf-8"))
    version = {**haru["versions"]["ja"], "voice": VOICE_ID}
    fields = {"author": "Tester", "version": "2.1", "updated": "2026-01-02"}  # optional fields must survive
    character = {"format": 1, "id": CHAR_ID, "image": "", **fields, "versions": {"ja": version}}
    aiko = json.loads((DATA / "test-voice.voice.json").read_text(encoding="utf-8"))
    voice = {**aiko, "id": VOICE_ID, "name": "Test", "file": f"{VOICE_ID}.{clip}"}
    source = DATA / "test-voice.flac"
    if clip == "ogg":  # Ogg Opus, the planned format of the shared character registry
        if not shutil.which("ffmpeg"):
            pytest.skip("ffmpeg not found")
        subprocess.run(["ffmpeg", "-loglevel", "error", "-i", str(source), "-c:a", "libopus", "-b:a", "48k",
                        str(tmp_path / "audio.ogg")], check=True)
    else:
        shutil.copyfile(source, tmp_path / "audio.flac")

    # 1. The web app writes a .haru file.
    node(tmp_path, f"""
import {{ writeZip }} from {json.dumps(str(WEB / "zip.ts"))};
import {{ characterFile, voiceFile }} from {json.dumps(str(WEB / "library.ts"))};
import {{ readFileSync, writeFileSync }} from "node:fs";
const enc = s => new TextEncoder().encode(JSON.stringify(s, null, 2) + "\\n");
const c = {json.dumps(character)}, v = {json.dumps(voice)};
const blob = writeZip([
  {{ name: "haru.json", data: enc({{ format: 1, kind: "haru-kit", characters: [c.id], voices: [v.id] }}) }},
  {{ name: `characters/${{c.id}}.character.json`, data: enc(characterFile(c)) }},
  {{ name: `voices/${{v.id}}.voice.json`, data: enc(voiceFile(v)) }},
  {{ name: `voices/${{v.file}}`, data: new Uint8Array(readFileSync({json.dumps(f"audio.{clip}")})) }},
]);
writeFileSync("web.haru", new Uint8Array(await blob.arrayBuffer()));
""")

    # 2. The Mac app imports it, then exports the same character.
    root = tmp_path / "root"
    root.mkdir()
    main = tmp_path / "main.swift"
    main.write_text(f"""
import Foundation
@main struct Cross {{
    static func main() throws {{
        let store = LibraryStore(root: URL(fileURLWithPath: {json.dumps(str(root))}))
        let result = try store.importHaru(try unzip(URL(fileURLWithPath: {json.dumps(str(tmp_path / "web.haru"))}))) {{ _ in true }}
        store.reload()
        let character = store.character(result.characters[0])!
        let version = character.versions["ja"]!
        let voice = store.voice(version.voice)!
        let audio = try Data(contentsOf: voice.audioURL)
        try storeZip(try store.exportCharacter(character.id)).write(to: URL(fileURLWithPath: {json.dumps(str(tmp_path / "mac.haru"))}))
        let out: [String: Any] = ["id": character.id, "card": version.card.json, "voice": voice.id, "file": voice.file,
                                  "audio": audio.base64EncodedString()]
        print(String(data: try JSONSerialization.data(withJSONObject: out), encoding: .utf8)!)
    }}
}}
""")
    sources = [MAC / name for name in ("Library.swift", "HaruFile.swift", "Zip.swift", "Card.swift", "AudioFiles.swift",
                                       "Trim.swift", "Strings.swift", "Strings.gen.swift")]
    binary = tmp_path / "cross"
    build = subprocess.run(["swiftc", "-parse-as-library", *map(str, sources), str(main), "-o", str(binary)],
                           capture_output=True, text=True)
    assert build.returncode == 0, build.stderr
    mac = json.loads(subprocess.run([str(binary)], capture_output=True, text=True, check=True).stdout)
    assert mac["id"] == CHAR_ID and mac["voice"] == VOICE_ID
    assert mac["card"] == version["card"]
    assert mac["file"] == f"{VOICE_ID}.wav"
    stored = base64.b64decode(mac["audio"])
    with wave.open(io.BytesIO(stored)) as w:
        assert (w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getcomptype()) == (1, 2, 24_000, "NONE")
        frames = w.getnframes()

    # 3. The web app reads the Mac app's file.
    out = json.loads(node(tmp_path, f"""
import {{ readZip }} from {json.dumps(str(WEB / "zip.ts"))};
import {{ parseCharacter, parseVoice, audioFileName }} from {json.dumps(str(WEB / "library.ts"))};
import {{ readFileSync }} from "node:fs";
const buf = readFileSync("mac.haru");
const files = new Map((await readZip(buf.buffer.slice(buf.byteOffset, buf.byteOffset + buf.byteLength))).map(e => [e.name, e.data]));
const dec = n => JSON.parse(new TextDecoder().decode(files.get(n)));
const index = dec("haru.json");
const c = parseCharacter(dec(`characters/${{index.characters[0]}}.character.json`));
const v = parseVoice(dec(`voices/${{index.voices[0]}}.voice.json`));
const audio = Buffer.from(files.get(`voices/${{audioFileName(v)}}`)).toString("base64");
console.log(JSON.stringify({{ id: c.id, card: c.versions.ja.card, voice: v.id, audio, author: c.author, version: c.version, updated: c.updated }}));
"""))
    assert out["id"] == CHAR_ID and out["voice"] == VOICE_ID
    assert out["card"] == version["card"]
    assert base64.b64decode(out["audio"]) == stored
    assert {k: out[k] for k in fields} == fields

    expected = upstream_reference_samples(tmp_path / f"audio.{clip}")
    if expected is None:
        pytest.skip("the sample count check needs haru_mlx (haru-kit's Mac env)")
    assert abs(frames - expected) <= TOLERANCE, (frames, expected)
