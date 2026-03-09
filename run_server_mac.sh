#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
VENV="$ROOT/.venv-mac"
PORT="${SERVER_PORT:-8011}"

if [ ! -d "$VENV" ]; then
  echo "Missing $VENV"
  echo "Run ./setup_mac.sh first"
  exit 1
fi

source "$VENV/bin/activate"
cd "$ROOT"
SERVER_PORT="$PORT" python main.py server
