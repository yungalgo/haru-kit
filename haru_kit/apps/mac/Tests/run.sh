#!/bin/bash
# Build and run the standalone logic tests of the Mac app (no Xcode).
#   ./Tests/run.sh             # PYTHON=/path/to/python: one with haru_mlx, for the conversion test's upstream trim
#   TEST_REGISTRY=http://localhost:8970/   the served registry for the registry test (this is the default)
#   TEST_REGISTRY_UPDATE=http://localhost:8972/   optional: the same registry with a newer version of a character
set -euo pipefail
cd "$(dirname "$0")/.."
out=$(mktemp -d)
trap 'rm -rf "$out"' EXIT
run() {
    local name=$1; shift
    swiftc -parse-as-library "$@" Tests/Check.swift "Tests/$name.swift" -o "$out/$name"
    "$out/$name"
}
run DeviceChoiceTests App/DeviceChoice.swift
run TrimTests App/Trim.swift
run ZipTests App/Zip.swift App/Strings.swift App/Strings.gen.swift
run LibraryTests App/Library.swift App/HaruFile.swift App/Zip.swift App/Card.swift App/AudioFiles.swift App/Trim.swift \
    App/Strings.swift App/Strings.gen.swift Tests/Fixture.swift
# Tests that need the whole app (all of App/ but its entry point).
run AppTests -target arm64-apple-macos13.0 Shared/*.swift $(ls App/*.swift | grep -v HaruApp.swift)

# the registry client, download, checksum, import and update against a served registry.
export TEST_REGISTRY=${TEST_REGISTRY:-http://localhost:8970/}
if curl -sf -o /dev/null "${TEST_REGISTRY}index.json"; then
    run RegistryTests App/Registry.swift App/Library.swift App/HaruFile.swift App/Zip.swift App/Card.swift \
        App/AudioFiles.swift App/Trim.swift App/Strings.swift App/Strings.gen.swift
else
    echo "RegistryTests: skipped (no registry at $TEST_REGISTRY)"
fi

# clips of many types (ffmpeg) are stored as 24 kHz mono 16-bit WAV, against upstream
# prepare_reference(decode_audio(...)) (a Python with haru_mlx).
python=""
for candidate in "${PYTHON:-}" "$(cd .. && pwd)/.venv/bin/python" \
                 "$HOME/Library/Application Support/haru-kit/envs/mac/bin/python"; do
    if [ -n "$candidate" ] && "$candidate" -c "import haru_mlx.audio" >/dev/null 2>&1; then python=$candidate; break; fi
done
if ! command -v ffmpeg >/dev/null || [ -z "$python" ]; then
    echo "ConvertTests: skipped (needs ffmpeg, and PYTHON with haru_mlx)"
    exit 0
fi
# 6 s of a tone that swells like speech, with 1.5 s of silence before it and 2 s after, so
# that the trim cuts; 44.1 kHz stereo, so that the conversion mixes the channels and resamples.
ffmpeg -loglevel error -y -f lavfi -i "aevalsrc=0.3*sin(2*PI*220*t)*(0.6+0.4*sin(2*PI*3*t)):s=24000:d=6" \
    -af "adelay=1500:all=1,apad=pad_dur=2" -ar 44100 -ac 2 "$out/clip.wav"
encode() { ffmpeg -loglevel error -y -i "$out/clip.wav" "${@:2}" "$out/$1"; }
encode clip.flac
encode clip.mp3 -c:a libmp3lame -b:a 128k
encode clip.m4a -c:a aac -b:a 128k
encode clip.aac -c:a aac -b:a 128k -f adts
encode clip.ogg -c:a libopus -b:a 48k
encode clip.opus -c:a libopus -b:a 48k
encode clip.vorbis.ogg -c:a libvorbis
encode clip.aiff
encode clip.caf
encode clip.webm -c:a libopus -b:a 48k
clips=$("$python" - "$out"/clip.* <<'EOF'
import sys
from haru_mlx.audio import decode_audio, prepare_reference
for path in sys.argv[1:]:
    # AVAudioFile cannot read WebM: the app says error.unreadable_audio ("-").
    count = "-" if path.endswith(".webm") else len(prepare_reference(decode_audio(open(path, "rb").read())))
    print(path, count)
EOF
)
CONVERT_CLIPS=$clips run ConvertTests App/Library.swift App/HaruFile.swift App/Zip.swift \
    App/Card.swift App/AudioFiles.swift App/Trim.swift App/Strings.swift App/Strings.gen.swift
