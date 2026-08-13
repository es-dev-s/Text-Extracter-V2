#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
export PYTHONPATH="$(pwd)"
export PYTHONUNBUFFERED=1

HOST="${OCR_HOST:-127.0.0.1}"
PORT="${PORT:-${OCR_PORT:-8000}}"
ENV="${ENVIRONMENT:-development}"

if [[ -x ./venv/bin/uvicorn ]]; then
  UVICORN="./venv/bin/uvicorn"
else
  UVICORN="uvicorn"
fi

if [[ "$ENV" == "production" || "$ENV" == "prod" ]]; then
  exec "$UVICORN" app.main:app \
    --host 0.0.0.0 \
    --port "$PORT" \
    --proxy-headers \
    --forwarded-allow-ips='*'
fi

exec "$UVICORN" app.main:app \
  --host "$HOST" \
  --port "$PORT" \
  --reload \
  --reload-dir ./app \
  --reload-dir ./web
