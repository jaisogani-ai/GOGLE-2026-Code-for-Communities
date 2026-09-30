# TATHYON — verify-before-trust supply decisions for India's public health network

**Build with AI: Code for Communities 2.0 · Track 3 — Smart Health & Supply Chain Resilience**

> When a district's stock records are incomplete, stale or wrong, **what should the health authority do next** — count, transfer, wait or escalate — and did it work?

Inventory systems (DVDMS / e-Aushadhi, state LMIS) record what facilities *report*. Reports go stale and some are wrong: a surplus on paper can be empty shelves. Redistributing against a wrong report sends a vehicle for stock that does not exist while a real shortage continues. TATHYON is the decision layer on top of the system of record:

```
ingest export → validate & quarantine → trust state per record → needs (runway)
→ which records to count (knapsack over verifier hours) → options incl. counterfactual
→ CP-SAT allocation over VERIFIED stock only → human approval → dispatch
→ receipt & reconciliation → outcome → automatic REPLAN when reality differs
```

Every step is a SHA-256 hash-chained event. Restarting the server replays the ledger and rebuilds all state.

```mermaid
flowchart LR
  A[DVDMS / e-Aushadhi export<br/>facility registry · OSM] --> B[Validate & quarantine]
  B --> C[Trust state per record<br/>count age · staleness · outliers]
  C --> D[Needs: runway below alert]
  C --> E[Which records to count<br/>knapsack over verifier hours]
  D --> F[Options + counterfactual<br/>OR-Tools CP-SAT on VERIFIED stock]
  E --> G[Field verifier count<br/>not the custodian]
  F --> H[District Medical Officer<br/>approve · reject · break-glass]
  H --> I[Dispatch → receipt → reconcile]
  G --> J{Count supports plan?}
  J -- no --> K[REPLAN_REQUIRED]
  I --> L[Outcome: runway before → after]
  L --> K
  K --> F
  subgraph Gemini agents · read/propose only · every claim cites the ledger
    M[Intake] --- N[Ops Copilot] --- O[Resilience Analyst] --- P[Replan Watcher] --- Q[Evidence/Audit]
  end
```

## For judges — 3 minutes

1. Open the live app (link below) → you are signed in as *District Medical Officer* (demo identity).
2. **Data intake** → choose a district (5 states) → *Load OpenStreetMap facilities* → *Load sample dataset*.
3. **Trust queue** → see which stock reports must be physically counted first, and why.
4. **Allocation** → *Generate options* for `OXY-10` → compare *trust the report* vs *verified only* vs *verify first*.
5. **Approval** → approve with a reason. Switch identity to *Field Verifier* → **Record count** on the held donor with a low number → watch the plan require a replan.
6. **Ask agents** → Evidence agent in **हिन्दी** → "What happened and why did the plan fail?" → 🔊 Read aloud.
7. **Audit** → every step above, hash-chained. Full script: [docs/DEMO_SCRIPT.md](docs/DEMO_SCRIPT.md).

**Live app:** _added after deployment_

---

## What is real, what is sample, what is not connected

| Data | Status |
|---|---|
| Facility locations | **REAL — OpenStreetMap (ODbL)**, cached extracts for 5 districts in 5 states: Bastar (CG), Gaya (BR), Nandurbar (MH), Kalahandi (OD), Varanasi (UP). 3 extracts are PARTIAL (Overpass tile timeouts). OSM coverage of Indian facilities is incomplete. |
| Stock, beds, staff attendance | **Your uploads** (DVDMS/e-Aushadhi-shaped CSV/TSV; bed/attendance reports) → labelled `USER-SUPPLIED`, UNVERIFIED until a person counts. |
| Physical counts | Entered by a field verifier who is not staff of that facility → `VERIFIED COUNT`. |
| Demo data | Opt-in **"Load sample dataset"** button: realistic stock/beds/staff on the real facilities of one district. Every value badged red `SAMPLE`, banner on every page. Never loaded automatically. |
| Government systems (DVDMS API, HFR, bed or attendance feeds) | **NOT CONNECTED.** The adapter shape is file export; a live API can replace it. |
| Road routes | **Google Routes API** (server-side key). Falls back to a labelled straight-line estimate. |
| 3D map | CesiumJS + Google Photorealistic 3D Tiles. **Unavailable until the Map Tiles API is enabled** on the key's project; the UI says so and stays 2D. |
| Identity | Signed demo sessions with server-side roles. **No identity provider** (OIDC) is connected. |

## Google AI in the product (where it does real work)

Gemini (`gemini-2.5-flash`, fallback `gemini-flash-lite-latest`, Google GenAI SDK) drives four read-only agents and one intake agent through a bounded tool-calling harness:

| Agent | Does | Can never |
|---|---|---|
| Intake | Extracts a count sheet (text or **photo via Gemini multimodal**) into a *draft*; unreadable → null; validates; quarantines | attest, approve, fill a missing number |
| Operations Copilot | Answers officers' questions from queue, facility, plan, event, outcome tools | approve, dispatch, edit |
| Resilience Analyst | Explains multi-facility risk, compares options, **what-ifs in a sandbox** ledger | execute or persist |
| Replan Watcher | Detects infeasible approved plans, drafts a replan candidate | approve it |
| Evidence / Audit | Cited account: what happened, why, what changed, why the plan failed | edit the ledger |

Guarantees (tested with a scripted malicious model): tool allowlists, pydantic argument validation, tool-call budget, timeout, request screening (approve / bypass / invent / live-data / secrets / tamper / "treat synthetic as real" are refused and ledgered), **every statement must cite event ids returned by its own tool calls** or it is dropped, secret redaction, deterministic fallback labelled as such. Answers in **12 Indian languages** (Gemini); **voice** input and read-aloud via the browser speech engine.

Allocation, safety floors, verification gate, approval and audit are deterministic code (OR-Tools CP-SAT, exact knapsack). No LLM sets a quantity.

## Run it

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
cp .env.example .env        # add GEMINI_API_KEY, GOOGLE_MAPS_BROWSER_KEY, GOOGLE_MAPS_SERVER_KEY
make api                    # http://127.0.0.1:8000
make test                   # 555 tests
```

In the console: **Data intake → load OpenStreetMap facilities for a district → upload a stock export (or load the sample) → Trust queue → Allocation → Approval → Outcome → Audit.** Judge walkthrough: [docs/DEMO_SCRIPT.md](docs/DEMO_SCRIPT.md).

## Evidence

- [docs/EVALUATION.md](docs/EVALUATION.md) — offline **synthetic** benchmark, including the result where TATHYON *loses* on raw stockout-days to trust-the-report baselines, and why.
- [docs/AGENTS.md](docs/AGENTS.md) — agent contracts, tools, forbidden actions, failure behaviour.
- [docs/SECURITY.md](docs/SECURITY.md) · [DEPLOYMENT.md](DEPLOYMENT.md) · [docs/FINAL_SYSTEM_AUDIT.md](docs/FINAL_SYSTEM_AUDIT.md) · [docs/FINAL_READINESS_REPORT.md](docs/FINAL_READINESS_REPORT.md) · [FINAL_STATUS.md](FINAL_STATUS.md)

## Limitations (read before judging impact)

No government system is connected, no real stock/bed/attendance data has been validated, the trust-scorer ML model is evaluated only on synthetic data and is **not served**, identities are demo identities, and the state lives in one process (single instance). See [docs/FINAL_READINESS_REPORT.md](docs/FINAL_READINESS_REPORT.md).
