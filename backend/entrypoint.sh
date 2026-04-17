#!/bin/sh
set -e
echo "=================================="
echo "gammagamma entrypoint starting"
echo "=================================="
echo "timestamp:  $(date -u +%FT%TZ)"
echo "pwd:        $(pwd)"
echo "PORT:       ${PORT:-<unset>}"
echo "python:     $(command -v python || echo missing)"
echo "python-ver: $(python --version 2>&1)"
echo "uvicorn:    $(command -v uvicorn || echo missing)"
echo "files:      $(ls -la /app/app 2>&1 | head -5)"
echo "=================================="
exec python -u -m uvicorn app.main:app \
  --host :: \
  --port "${PORT:-8000}" \
  --log-level info \
  --no-access-log
