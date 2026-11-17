#!/bin/bash
# Build the web app (haru_kit/apps/web) into haru_kit/static, the bundle that haru-kit ships.
# Needs Node 20 or newer. Commit haru_kit/static with the source change.
set -euo pipefail
cd "$(dirname "$0")/../haru_kit/apps/web"
npm ci --silent
npm run build
rm -rf ../../static
cp -R dist ../../static
echo "built haru_kit/static"
