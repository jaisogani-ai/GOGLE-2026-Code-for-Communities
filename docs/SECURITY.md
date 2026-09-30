# Security

Status: **prototype controls in place; not production-ready** (no identity provider, single process, no external
security review).

| Area | Control | Where |
|---|---|---|
| Authentication | HMAC-signed session tokens (8 h). **Demo identities only**; no password or IdP. Disable with `TATHYON_DEMO_LOGIN=0`. Production: replace `issue_demo_session` with OIDC. | `api/security.py` |
| Authorization / RBAC | Role comes from the signed token, never the request body. Approve/reject/break-glass/release: district medical officer. Dispatch/delay: DMO or logistics. Counts: field verifier who is not staff of that facility. Receipts: in-charge of the **receiving** facility, not the dispatcher. Uploads: data officer/DMO. | `api/main.py`, `tathyon/workspace.py` |
| Separation of duties | Custodian cannot count own stock (registry custodian id + facility scope); automated identities cannot attest; dispatcher cannot confirm receipt. | `workspace.py`, `store.py` |
| Replay / duplicates | Plan decisions are final (`PLAN_NOT_PENDING`); receipts on closed shipments refused; client event ids idempotent. | `workspace.py`, `store.py` |
| Audit tampering | SHA-256 hash chain; `/health` and Audit view verify it; tampering is detected (tamper-**evident**, not tamper-proof: an attacker with DB write access can rewrite and re-chain). | `store.py` |
| Uploads | 2 MB file / 20,000 rows / 200-char cells; filename path traversal refused; formula-injection cells quarantined; quarantined raw values escaped; CSV export escapes formula prefixes. | `intake_pipeline.py`, `api/main.py` |
| Request size / rate | 6 MB body limit (413); per-IP limits: 120 writes/min, 30 agent runs/min, 30 logins/min. In-memory (single instance). | `api/security.py` |
| Headers | CSP (self + cdnjs + Google Maps/Fonts; `unsafe-eval` required by Maps JS; `blob:` workers for Cesium), `frame-ancestors 'none'`, nosniff, referrer policy, permissions policy, `no-store` on API. | `api/security.py` |
| CORS | Same-origin by default; opt-in `TATHYON_CORS_ORIGINS`. | `api/main.py` |
| Secrets | `.env` git- and docker-ignored, `chmod 600`; never logged, never on the ledger. Only `GOOGLE_MAPS_BROWSER_KEY` reaches the browser (Google requires it client-side). Gemini and Routes keys stay server-side. Agent output redacts key-shaped strings. | `api/env.py`, `agents/base.py` |
| Prompt injection / agent abuse | See `docs/AGENTS.md`. | `agents/` |
| XSS | UI renders all data with `textContent` (no `innerHTML` of data). | `web/app.js` |

## Required before any real deployment

1. **Rotate** the Gemini and Maps keys that were pasted into a chat session during development.
2. Split Maps keys: browser key restricted to your domain (HTTP referrers) + Maps JavaScript API + Map Tiles API;
   server key restricted to Routes API. Set quotas.
3. OIDC identity provider with state role mapping; remove demo login.
4. Move the ledger to a managed database with backups (Cloud SQL / Firestore); append-only permissions.
5. Security review, dependency scanning (`pip-audit`), logging/alerting, data-protection review for facility data.
