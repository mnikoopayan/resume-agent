#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
VENV="$ROOT/.venv-mac"

python3 -m venv "$VENV"
source "$VENV/bin/activate"
python -m pip install --upgrade pip
pip install -r "$ROOT/requirements.txt"

echo
echo "macOS setup complete."
echo "Activate with: source $VENV/bin/activate"
echo "Run server with: SERVER_PORT=8011 python $ROOT/main.py server"
