# Agents — contracts

All agents run through `tathyon/agents/base.py` (`BoundedAgent`). Live descriptions, including JSON schemas for
every tool argument and the output: `GET /api/agents`.

**Model:** Gemini via the Google GenAI SDK (`GEMINI_MODEL`, default `gemini-2.5-flash`; on HTTP 404 the client
switches once to `GEMINI_FALLBACK_MODEL`, default `gemini-flash-lite-latest`). Automatic function calling is
disabled: Gemini may only *name* a tool; the harness validates and executes it. Thinking budget is capped (512)
and one "JSON only" repair turn is allowed. Without a key, or on any model failure, the same tools run through a
deterministic planner/composer and the result says `DETERMINISTIC` (and `DEGRADED` + reason if Gemini failed).

## Shared guarantees

| Control | Implementation |
|---|---|
| Request screening | Regex rules refuse: consequential actions, safety bypass, fabrication, live-government data, secrets, audit tampering, provenance misrepresentation, state mutation. Refusal is an `AGENT_REFUSED` event. |
| Allowlist | Tools are declared per agent; effects are only `READ` or `PROPOSE`. Unknown tool → `TOOL_NOT_ALLOWLISTED`, recorded. |
| Arguments | Every tool call validated by a pydantic model (`INVALID_ARGUMENTS`). |
| Budget / timeout | `max_tool_calls` per agent, wall-clock deadline (25 s), SDK request timeout 20 s. |
| Evidence first | The deterministic evidence tools run first; Gemini starts from their results. |
| Grounding | A statement is kept only if **every** cited `evt_…` id exists on the ledger **and** appeared in this run's tool results. Uncited/malformed statements are dropped and counted. If none survive → deterministic fallback. |
| Prompt injection | Tool results are data; the system prompt says so. Even if a model is steered, it has no write tool, and ungrounded claims are dropped. |
| Secrets | Key-shaped strings and configured secret values are redacted from output. |
| Audit | Every run is an `AGENT_RUN` event: request hash, tools used, blocked model attempts, cited ids, provider, duration. |
| Language | `language` ∈ 12 Indian languages; Gemini writes answers in it, keeping ids and numbers verbatim. The fallback answers in English and says so. |

## Agents

| Agent | Trigger | Tools | Forbidden | Human boundary |
|---|---|---|---|---|
| `intake_agent` | Verifier uploads a count sheet (text/photo) | `extract_count_sheet`, `validate_schema`, `quarantine_row`, `file_attestation_record` (files a **DRAFT** only) | approve, allocate, write verified state, fill unreadable values, attest for a human | A verifier re-enters/confirms via `POST /api/verify/attest` |
| `ops_copilot` | Officer question | `query_trust_queue`, `get_facility_detail`, `search_audit_log`, `get_allocation_plan`, `explain_event`, `get_outcome` | approve, veto, allocate, edit inventory, dispatch, change policy | Decisions happen in Approval |
| `resilience_analyst` | "Why is X at risk / what if…" | `get_case_set`, `get_resource_state`, `get_forecast`, `get_network_constraints`, `run_scenario` (sandbox ledger), `compare_options` | execute allocation, persist anything | CP-SAT computes; officer decides |
| `replan_watcher` | After delays/receipts/counts, or "Run replan watcher" | `get_active_plans`, `get_shipments`, `get_latest_attestations`, `evaluate_plan_feasibility`, `flag_replan_required`, `generate_replan_candidate` | approve, dispatch, cancel, change quantities | Candidate is a PROPOSED plan in Approval |
| `evidence_agent` | Auditor asks what happened | `search_events`, `get_case_history`, `get_attestations`, `get_decisions`, `get_outcomes` | edit/delete events, unsupported claims | Evidence for a human reviewer, not a finding of fault |

Tests: `tests/test_agents_bounded.py` (48 adversarial prompt × agent cases, forbidden tool, hallucinated citations,
bad arguments, budget, model exception, secret redaction, sandbox isolation, draft-not-attestation, photo
confidence → null).

Verified live with the configured key (2026-09-30): Copilot, Analyst, Watcher and Evidence agents returned
`OK / GEMINI / gemini-2.5-flash` with grounded citations in 8/8 runs; the Copilot answered in Hindi.
