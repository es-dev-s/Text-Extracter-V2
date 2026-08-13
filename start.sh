#!/bin/sh
set -eu
export PYTHONUNBUFFERED=1
port="${PORT:-8000}"
# Railway: one worker keeps RSS small. Raise WEB_CONCURRENCY only on larger plans.
workers="${WEB_CONCURRENCY:-1}"
exec uvicorn app.main:app \
  --host 0.0.0.0 \
  --port "$port" \
  --workers "$workers" \
  --proxy-headers \
  --forwarded-allow-ips='*' \
  --timeout-keep-alive 5
