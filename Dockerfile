# TATHYON — FastAPI + OR-Tools + static console. One process, one worker (state is in-process).
FROM python:3.11-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 \
    PORT=8080 TATHYON_DATA_DIR=/tmp/tathyon-data TATHYON_AUTOLOAD_OSM=bastar_chhattisgarh,gaya_bihar TATHYON_AUTOLOAD_SAMPLE=1

WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY tathyon/ ./tathyon/
COPY api/ ./api/
COPY web/ ./web/
COPY reference/ ./reference/

RUN useradd --create-home --uid 10001 tathyon && mkdir -p /tmp/tathyon-data && chown tathyon /tmp/tathyon-data
USER tathyon

EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD python -c "import os,urllib.request;urllib.request.urlopen(f'http://127.0.0.1:{os.environ.get(\"PORT\",\"8080\")}/health',timeout=4)" || exit 1

# Secrets come from the environment (Secret Manager on Cloud Run). No .env is copied into the image.
CMD ["sh", "-c", "exec uvicorn api.main:app --host 0.0.0.0 --port ${PORT} --workers 1 --proxy-headers --forwarded-allow-ips='*'"]
