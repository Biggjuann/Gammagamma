FROM python:3.11-slim
WORKDIR /app
ENV PYTHONUNBUFFERED=1
CMD ["sh", "-c", "echo '=== DIAG START ==='; echo \"PORT=${PORT:-unset}\"; echo \"HOSTNAME=$(hostname)\"; echo \"PWD=$(pwd)\"; env | sort | head -20; echo '=== sleeping 1h so container stays alive ==='; sleep 3600"]
