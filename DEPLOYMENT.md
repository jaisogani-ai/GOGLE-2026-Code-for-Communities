# Deployment

## Decision: Google Cloud Run, region `asia-south1` (Mumbai)

| Need | Cloud Run | Render | Firebase Hosting / App Hosting | Netlify |
|---|---|---|---|---|
| Python/FastAPI + OR-Tools (native wheels) in Docker | Yes | Yes | App Hosting targets Node frameworks; Hosting is static | Static/functions; not a fit |
| Secrets | Secret Manager | Env vars | Secret Manager | Env vars |
| Google AI / Maps project alignment, India region | Yes (asia-south1) | Singapore/other | — | — |
| One service serves API + UI | Yes | Yes | No (split) | No |

One container serves the API and the static console, so a second hosting platform adds nothing. Render is an
acceptable fallback (same Dockerfile, `PORT` honoured). Firebase/Netlify are not used.

**Constraint:** workspace state is held in one process. Deploy with `--max-instances 1`. The container filesystem
is ephemeral, so the SQLite ledger survives restarts of the *process* only while the instance lives; keep
`--min-instances 1` for a demo. A pilot needs a managed database (Cloud SQL/Firestore) behind `EventStore`.

## Steps (run on a machine with `gcloud` authenticated to your project)

```bash
PROJECT=your-project-id
gcloud config set project $PROJECT
gcloud services enable run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com secretmanager.googleapis.com
```

```bash
printf '%s' "$GEMINI_API_KEY" | gcloud secrets create gemini-api-key --data-file=-
```

```bash
printf '%s' "$GOOGLE_MAPS_SERVER_KEY" | gcloud secrets create maps-server-key --data-file=-
```

```bash
python3 -c "import secrets;print(secrets.token_hex(32),end='')" | gcloud secrets create tathyon-session-secret --data-file=-
```

```bash
gcloud run deploy tathyon --source . --region asia-south1 --allow-unauthenticated --max-instances 1 --min-instances 1 --memory 1Gi --cpu 1 --timeout 120 --set-secrets GEMINI_API_KEY=gemini-api-key:latest,GOOGLE_MAPS_SERVER_KEY=maps-server-key:latest,TATHYON_SESSION_SECRET=tathyon-session-secret:latest --set-env-vars GOOGLE_MAPS_BROWSER_KEY=YOUR_REFERRER_RESTRICTED_BROWSER_KEY,TATHYON_DB=1,TATHYON_DEMO_LOGIN=1
```

Give the Cloud Run service account `roles/secretmanager.secretAccessor` on the three secrets if the deploy reports
a permission error. Then add the service URL (`https://tathyon-…a.run.app/*`) to the browser key's HTTP referrer
restrictions, and enable **Map Tiles API** on the project if you want 3D.

## Verify after deploy

```bash
URL=$(gcloud run services describe tathyon --region asia-south1 --format='value(status.url)')
```

```bash
curl -s $URL/health
```

```bash
curl -s $URL/api/system/status
```

Then in a browser: Data intake → load a district → load sample → Trust queue → Allocation → Approval → Outcome →
Audit → Ask agents (English and Hindi) → Map (routes provider GOOGLE; 3D state shown).

## Health, logs, rollback

- Health: `GET /health` (ledger chain status; `status: degraded` if the chain is broken). Container HEALTHCHECK uses it.
- Logs: `gcloud run services logs read tathyon --region asia-south1`
- Rollback: `gcloud run revisions list --service tathyon --region asia-south1`, then
  `gcloud run services update-traffic tathyon --region asia-south1 --to-revisions REVISION=100`

## Environment variables

See `.env.example`. Missing `GEMINI_API_KEY` → agents run DETERMINISTIC (labelled). Missing Maps keys → OpenStreetMap
basemap and straight-line routes (labelled). `GET /api/system/status` reports each as `NOT_CONFIGURED`.
