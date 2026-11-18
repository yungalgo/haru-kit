#!/bin/bash
# record 2 s from a named input device with the app's VoiceRecorder.
#   ./Tests/record.sh ["BlackHole 2ch"]    # a silent virtual device by default
set -euo pipefail
cd "$(dirname "$0")/.."
out=$(mktemp -d)
trap 'rm -rf "$out"' EXIT
swiftc -parse-as-library -target arm64-apple-macos13.0 Shared/*.swift App/VoiceRecorder.swift App/AudioFiles.swift \
    App/Strings.swift App/Strings.gen.swift Tests/Check.swift Tests/RecordTests.swift -o "$out/RecordTests" \
    -framework AudioToolbox -framework CoreAudio -framework AVFoundation
"$out/RecordTests" "$@"
