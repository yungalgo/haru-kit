#!/bin/bash
# Render the app's screens in several states to PNG files, without the model server.
#   PYTHON=<python with haru_mlx> ./Tests/screens.sh [output dir]   (default /tmp/haru-screens)
set -euo pipefail
cd "$(dirname "$0")/.."
PYTHON="${PYTHON:-$HOME/Library/Application Support/haru-kit/envs/mac/bin/python}"
out="${1:-/tmp/haru-screens}"
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
"$PYTHON" - "$work/assets.json" <<'EOF'
import json, sys
from haru_mlx.native import bundled_assets
assets = bundled_assets()
assets.update(default_language="en", step_ms=96.0, precision="bf16", model="higurashi-ai/Haru-4B")
json.dump(assets, open(sys.argv[1], "w"), ensure_ascii=False)
EOF
apps=$(ls App/*.swift | grep -v HaruApp.swift)
swiftc -target arm64-apple-macos13.0 -parse-as-library Shared/*.swift $apps Tests/Screens.swift -o "$work/Screens" \
    -framework AudioToolbox -framework CoreAudio -framework SwiftUI -framework AppKit -framework AVFoundation \
    -framework Accelerate -framework CoreMedia -framework CoreVideo 2>&1 | grep -E "error" || true
"$work/Screens" "$work/assets.json" "$out"
