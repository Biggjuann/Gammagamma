FROM python:3.11-slim
WORKDIR /app
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
CMD ["sh", "-c", "echo \"gg: container up, PORT=${PORT:-unset}\" && exec python -u -m uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --log-level info"]
