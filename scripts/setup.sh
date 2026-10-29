#!/usr/bin/env bash
# E.A.G.L.E. の環境構築(Linux / macOS)。使い方: ./scripts/setup.sh [standard|high|light|tiny]
set -euo pipefail
cd "$(dirname "$0")/.."
PRESET="${1:-standard}"
PY="${PYTHON:-python3}"
[ -d .venv ] || "$PY" -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -e .
if command -v npm >/dev/null 2>&1; then
  (cd frontend && (npm ci || npm install) && npm run build)
else
  echo "Node.js(npm) が見つかりません。frontend で npm run build を実行してください" >&2
fi
.venv/bin/python -m eagle setup-vlm --preset "$PRESET"
echo "完了: .venv/bin/python -m eagle server で Webアプリ版を起動できます"
