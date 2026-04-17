#!/bin/sh
echo "=================================="
echo "gammagamma entrypoint starting"
echo "=================================="
echo "timestamp:  $(date -u +%FT%TZ 2>/dev/null || date)"
echo "pwd:        $(pwd)"
echo "PORT:       ${PORT:-<unset>}"
echo "python:     $(command -v python || echo missing)"
echo "python-ver: $(python --version 2>&1)"
echo "uvicorn:    $(command -v uvicorn || echo missing)"
echo "app dir:"
ls -la /app/app 2>&1 | head -8 || true
echo "=================================="
exec python -u -m uvicorn app.main:app \
  --host :: \
  --port "${PORT:-8000}" \
  --log-level info \
  --no-access-log
