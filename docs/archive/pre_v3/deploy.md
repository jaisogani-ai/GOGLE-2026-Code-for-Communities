# TATHYON Local Prototype Run Guide

This guide runs the prototype locally. The repository is **not production-ready**: trusted authentication, authorized health-system sources, monitoring, backups, security review, retention controls and operating procedures are not configured. Current APIs return unavailable for consequential actions. Do not expose it publicly or load real health records into a public host.

> **Security Mandate**: Never commit or hardcode `GEMINI_API_KEY`. API keys must always be supplied at deploy time via environment variables or secret managers.
> The generated benchmark is only for offline engineering evaluation. It is not field data, pilot evidence, or served as operational truth.

---

## Deployment is disabled

`deploy/render.yaml` and `deploy/cloudrun.yaml` are intentionally disabled. Do not expose this prototype publicly: authentication, authorized data connections, security review, monitoring, backups, retention policy and operational procedures are not configured. Do not place Gemini credentials in this service; raw health document processing is disabled.

### Local / On-Premise Docker

```bash
# 1. Build local container
docker build -t tathyon:latest .

# 2. Run locally with durable local SQLite storage; health data remains local
docker volume create tathyon-data
docker run --rm -p 127.0.0.1:8000:8000 -v tathyon-data:/app/data \
    -e TATHYON_DB=1 \
    tathyon:latest

# 3. Verify health and open UI
curl -s http://127.0.0.1:8000/health | jq .
open http://127.0.0.1:8000/
```

---

## Deployment Verification Checklist

Local checks establish that the software starts. They do not establish production readiness or health-system integration:
1. `GET /health` returns status `ok` and reports the configured data environment accurately.
2. `/model/status` must report `NO_APPROVED_REAL_MODEL` until a documented real-data model is reviewed.
3. Opening `/` must show operational data is not connected and model unavailable.
4. Do not publicly deploy or load sensitive health records until all authentication, data authorization, security, monitoring, backup/restore and human procedure requirements are reviewed and implemented.
