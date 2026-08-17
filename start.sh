#!/bin/sh
set -eu
export PYTHONUNBUFFERED=1

port="${PORT:-8000}"

# Uvicorn worker processes. The title heuristics are plain Python, so real
# throughput comes from processes, not threads. Default to one per two vCPUs
# (Railway reports the container's limit here), clamped to a sane range.
if [ -z "${WEB_CONCURRENCY:-}" ]; then
  cpus=$(nproc 2>/dev/null || echo 2)
  workers=$((cpus / 2))
  [ "$workers" -lt 2 ] && workers=2
  [ "$workers" -gt 8 ] && workers=8
  WEB_CONCURRENCY="$workers"
fi
# app/config.py reads this to size each process's extraction pool.
export WEB_CONCURRENCY

exec uvicorn app.main:app \
  --host 0.0.0.0 \
  --port "$port" \
  --workers "$WEB_CONCURRENCY" \
  --proxy-headers \
  --forwarded-allow-ips='*' \
  --timeout-keep-alive 5 \
  --timeout-graceful-shutdown 30
