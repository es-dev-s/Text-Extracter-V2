#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
export PYTHONPATH="$(pwd)"
HOST="${OCR_HOST:-127.0.0.1}"
PORT="${OCR_PORT:-8000}"
exec ./venv/bin/uvicorn app.main:app \
  --host "$HOST" \
  --port "$PORT" \
  --reload \
  --reload-dir ./app
