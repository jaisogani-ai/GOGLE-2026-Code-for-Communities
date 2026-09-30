# TATHYON — Antigravity Final Prompt Pack

Use these prompts one at a time in Antigravity, with the TATHYON repository open. The prompts tell the agent to inspect the current implementation and provide evidence before changing it. They do not authorize fabricated real data, government claims, credentials, or integrations.

## Prompt 1 — Product wedge, evidence research, and go-to-market

```text
You are the product and market research lead for TATHYON, a health-resource reliability project for India. Work from the existing repository and the source links below. Do deep, current, cited research before recommending product changes. Cite direct source URLs and dates beside material claims. Prefer primary sources (government, CAG, official product/API documentation, peer-reviewed papers, YC company pages, company primary material). Clearly label inference, self-reported company metrics, historical audits, and Reddit anecdotes. Never present an anecdote as prevalence evidence or claim a government API exists unless official documentation proves it.

RESEARCH QUESTIONS
1. Map the actual MoHFW/NHM public health supply-chain structure and decision rights relevant to medicines, beds, and personnel. Explain differences across states; do not imply one national operating model.
2. Inspect C-DAC DVDMS/e-Aushadhi and state medical-corporation documentation: what is actually captured, what export formats exist, what interfaces/APIs are publicly documented, who can access them, and what remains unknown. Separate “data field exists” from “TATHYON has access.”
3. Examine CAG audits (including Punjab e-Aushadhi), NHM Common Review Mission reports, WHO/UNICEF/World Bank/PATH publications, and peer-reviewed LMIC supply-chain studies (including Zambia LMIS discrepancy research). State each source’s date, sample, geography, and limits. Do not generalize one state or a historical audit to all India today.
4. Examine Indian health-tech companies, relevant YC companies and the YC company Farmako, Field Intelligence/Shelf Life and other LMIC precedents. Research the Build-with-AI Track 3 winner HealthGrid from official/primary sources: what it solved, why it won (only if judging evidence exists), and what TATHYON should avoid copying. Identify initial user, payer, narrow wedge, first deployment shape, proof of value, and what TATHYON should not copy. Treat company claims as self-reported unless independently verified.
5. Use Reddit only to surface user interview hypotheses; label every Reddit statement anecdotal and do not cite it as proof of market size or prevalence.

ATTACK THE CURRENT THESIS
Evaluate whether “verification-gated allocation” is the right first wedge. Compare at least 5–10 concrete directions, score them on severity, frequency, identifiable user/payer, time-to-value, data/access burden, deployment risk, defensibility, and ability to pilot without a government integration. Pick one strongest wedge—or say evidence is insufficient. Explicitly distinguish the operational failure (“what action fails, for whom, and when”) from the broad category “inventory.”

DELIVER EXACTLY THESE 26 SECTIONS
1. Executive answer; 2. Specific operational problem; 3. Why current systems still fail; 4. What government systems know; 5. What they do not know / cannot decide; 6. Outcome currently not measured; 7. Evidence ledger with links, dates, and limitations; 8. CAG/NHM findings; 9. DVDMS/e-Aushadhi capabilities and access reality; 10. LMIC/WHO evidence; 11. YC/Farmako lessons; 12. Field Intelligence and LMIC lessons; 13. Indian competitors and substitutes; 14. Five-to-ten product directions and scorecard; 15. Elimination rationale; 16. Final product wedge; 17. Differentiation and moat; 18. First user and economic buyer; 19. First pilot design, measurable outcomes, and stop criteria; 20. What can be demonstrated without government access; 21. What requires permission/integration; 22. AI vs deterministic engineering vs human judgment; 23. Agents, optimization, data network effects, graph, 3D map, and federation roles; 24. MVP and district→state→national→other-country path; 25. Keep/delete/build-next, biggest risk, and five-minute demo; 26. Final startup thesis and confidence.

Include the YC test: who uses it, who pays first, what budget or procurement path is plausible (mark unverified), what the narrow first pilot is, and why it can expand. Include an explicit “federated/BRICS modelling: useful, future, or theatre?” answer. Do not claim federation creates value without a concrete cross-jurisdiction use case, lawful data-sharing basis, and measurable benefit. Treat maps as operationally useful only when geography and route provenance are real; a 3D view is not a moat.

Before finalizing, inspect docs/PRODUCT_WEDGE_RESEARCH_2026-09-30.md, docs/archive/REAL_DATA_PIPELINE_TRAINING_AUDIT_2026-09-30.md (or locate its current equivalent if it has moved), and docs/real_data_features.md. Recheck sources and challenge the research memo; do not merely repeat it. Report contradictions between the repository and external evidence. End with a concise source list and a 30-day validation plan with named interview roles (not invented contacts).
```

## Prompt 2 — Authorized real-data pipeline and local Mac training

```text
Own the TATHYON local real-data pipeline. First inspect the complete repository, docs/archive/REAL_DATA_PIPELINE_TRAINING_AUDIT_2026-09-30.md (or its current equivalent), the existing trainer and hardened DVDMS/e-Aushadhi-shaped CSV/TSV adapter, feature definitions, P6 thresholds, manifests, registry, and tests. Report what is already implemented and what is missing before editing. Do not spend an hour sleeping, looping, or generating more synthetic rows: use compute only for meaningful local data processing, and stop early when the acceptance gates fail.

HARD DATA RULES
- Train only on authentic, authorized operational records whose source, collection period, permitted use, and exact content hash can be independently verified. Human attestation records must be genuine and linked to each label; labels are only 1 when a qualified human attests a material discrepancy using the documented P6 threshold. No inferred, simulated, LLM-created, proxy, or weak labels.
- Never use Kaggle data. Never mix synthetic rows into training, validation, test, calibration, or claimed real-data metrics. Synthetic data may be run separately only to reproduce the existing benchmark, with explicit SIMULATION labeling.
- If the only available source is missing, generated, unauthorized, unlabelled, or unverifiable, do not fit a real model. Complete safe ingestion/audit/dry-run work, state the blocker plainly, and provide the exact data-owner handoff checklist.
- Parse with the hardened adapter. Quarantine malformed rows with reason and source-row identity; do not silently repair, coerce, impute, or discard them.
- Use the exact existing feature sets: 18 medicine, 10 bed, 10 personnel. A real-data field absent from the source stays explicitly missing. Do not invent a proxy. Record every mapped field, missing field, and approved substitution in docs/real_data_features.md and the data card.
- Prevent leakage. Split by facility and time; no random-row split. Report facility/time boundaries and label counts.
- Use the baseline GradientBoosting hyperparameters unchanged. A challenger is eligible only if held-out PR-AUC and its delta against the same-budget RANDOM ablation both beat the baseline under the repository’s predeclared acceptance rule. Report PR-AUC, hit-rate at the fixed verification budget, same-budget random results, uncertainty/limitations, and per-module counts. Do not imply causal impact from predictive metrics.
- Version every attempted/approved model in artifacts/model_registry.json with model/data/feature hashes, source-manifest ID, seed, split, hyperparameters, metrics, environment, approval state, and artifact hash. A dry-run or rejected candidate must not become a served model.

MAC WORKFLOW
Inspect available CPU/RAM/disk and the repo’s supported environment. Prefer a deterministic local command with progress, checkpointing only where safe, reproducible seed, and an explicit maximum runtime. Do not install dependencies or upload data without authorization. Keep sensitive source files local and out of logs. Do not expose credentials.

DELIVERABLES
1. Implement only the missing safe pipeline pieces; keep the API/UI from describing simulated inputs as real.
2. Run the synthetic benchmark separately and confirm whether current reference numbers reproduce; label all output simulated.
3. If and only if authorized real records and valid human attestations exist, train and evaluate locally using the specified gates. Otherwise do not train and mark “BLOCKED: eligible ground truth unavailable.”
4. Update docs/real_data_features.md, provenance manifest/data card, registry, and a concise run report with exact commands and evidence.
5. Produce a pilot handoff checklist covering data-owner authorization, source export, identifiers/time zones, attester identity and method, discrepancy thresholds, privacy/retention, missing data, facility/time split feasibility, security review, rollback, and sign-off.

Acceptance means honest provenance and reproducibility, not a model existing at any cost. Never fabricate a successful real-data run or real-data metric.
```

## Prompt 3 — Complete product engineering: anomalies, agents, map, trust, and federation

```text
Act as senior product engineer for the existing TATHYON repository. Audit first, then implement the highest-priority gaps end to end. Read README, docs/agents.md, docs/decision-services.md, docs/archive/REAL_DATA_PIPELINE_TRAINING_AUDIT_2026-09-30.md (or its current equivalent), docs/real_data_features.md, and relevant API, UI, map, provenance, and tests. Inventory every feature as IMPLEMENTED / PARTIAL / DEMO / MISSING / BLOCKED, with file and API evidence. Do not trust screenshots or old product copy.

PRODUCT REQUIREMENTS
1. Resource anomaly review: analyze medicines, beds, and personnel reports for unusual values only when a comparable history and provenance permit it. Use deterministic robust statistical detection unless a verified model is actually available. Show the observed value, comparison window/sample count, method, uncertainty, and abstention reason. Do not call a heuristic statistically significant without a valid statistical test. Do not invent causal explanations. All findings enter the Trust Queue with “AI suggestion — human decides” only when generated by an AI model; deterministic findings must say “Statistical signal — human decides.” Human review, dismissal, and evidence must be auditable. Advisories never silently mutate stock, allocate resources, or become training labels.
2. Operational agents: verify which agent roles really execute and which are static/demo. Each active role must have a bounded input/output contract, provenance, failure mode, permission boundary, and visible live/demo/unavailable status. Do not claim that agents are trained models when they are prompts, deterministic functions, or API calls.
3. Map: trace every facility, count, status, polygon, route, and timestamp to its backend/API source. If values/geometries are seeded/generated, mark the whole layer SYNTHETIC DEMO. Never display “live,” “verified,” or “real route” without evidence. Real-road routing must come from a named routing provider or an authorized route plan; straight-line geometry is not a road route. Use real coordinates only when their provenance and permission are documented. Add useful empty/loading/stale/provider-error states.
4. Google Maps: make 2D the reliable default. Require a restricted browser key and an explicitly configured Map ID for Advanced Markers; keep keys session-only and never put credentials in source, logs, URLs, or persistent browser storage. Use current AdvancedMarkerElement API and keyboard-accessible interactions. Label 45-degree tilt as perspective tilt; do not call it true 3D. If a genuine Google 3D Maps API is proposed, verify current official docs and availability first, keep it optional, and preserve a usable 2D fallback.
5. Federation/BRICS: keep this future-facing unless the repo demonstrates multiple authorized sites and a working privacy/security model. Do not imply cross-border training or data exchange exists. If implemented, document threat model, consent/legal basis, aggregation leakage, participating nodes, minimum cohort, failure behavior, and a measurable benefit over local models; otherwise present a clearly marked architecture proposal only.
6. Remove unsupported legal/government/clinical claims, fake approvals, fabricated signatures, hardcoded “approved” routes, and UI labels that imply live data. Preserve a visible demo mode.

IMPLEMENTATION METHOD
- Follow existing architecture and accessibility conventions. Reuse one source of truth across API/UI/map. Add validation and safe error behavior. Do not fabricate feeds, keys, credentials, facilities, or labels.
- Add focused tests for contracts, provenance, no silent state mutation, abstention, API/UI state, and map fallback. Do not weaken old tests to make the change pass.
- Run the focused tests that cover changed behavior, inspect the diff, and report every unverified external integration separately.

OUTPUT: a concise feature audit; changes made; files touched; test commands/results; remaining real-world blockers; and a prioritized next-step list. Treat “complete” as evidenced implementation, not visual presence.
```

## Prompt 4 — Google Stitch UI/UX redesign and motion system

```text
Design a distinctive, production-quality Google Stitch UI for TATHYON: a trustworthy health-resource reliability and exception-resolution platform for district/state public-health operators in India. Create a complete responsive interface system, not a landing-page mockup. Follow this visual system: white and soft-grey surfaces, deep teal near #0F766E for primary actions/navigation, a serif display face for major headings and a highly legible sans-serif for dense data, an 8px spacing rhythm, 14px dense table text, restrained borders, and 8px card corners. Use a clear, restrained but colorful operational palette: red/amber/green only for status with text/icons and accessible contrast. Avoid gradients, glass effects, fake government seals, and invented official branding. Keep the console calm, precise, humane, and credible under time pressure.

GLOBAL SHELL AND SIX DESTINATIONS ONLY
- Persistent app title, visible “SYNTHETIC DEMO DATA” banner whenever demo content is used, role/district context, freshness indicator, notification center, and user menu. Include an accessible English/Hindi language toggle and correct Hindi rendering for key actions.
- Six destinations: Trust Queue, Map, Allocation, Approval, Outcome, Audit. Use consistent breadcrumbs, titles, filters, action placement, and keyboard behavior. Do not add decorative KPI clutter; every metric needs period, unit, source/definition, and coverage.

DESIGN THE FULL WORKFLOW
1. Trust Queue as the default: Medicines/Beds/Personnel sub-tabs; dense sortable/filterable table with facility, resource, risk and confidence, top three reason chips, report age, verification cost, expected value, and status. Explain ranking inline. Bulk selection opens a verification-dispatch review, never an immediate action. Support large lists and no-result, stale, partial-load, and API-error states.
2. Map: selectable facilities, resource/facility filters, facility/district search, risk/freshness, synchronized accessible list/table fallback, and contextual facility drawer that preserves filters/scroll. 2D is the reliable default; design an optional Google Maps 3D/perspective experience as a separate mode, labeled accurately and contingent on provider/API availability. Show route provenance; never imply geography is a source of truth. Include map unavailable/demo states.
3. Allocation: medicine transfers, bed diversions, and staff redeployments only where supported; show donor, recipient, quantity, route, constraints, assumptions, and plan version. Show rejected candidates and the exact deterministic rule. Label AI explanatory content “AI suggestion — human decides”; label deterministic proposals accurately. Link approvals and events.
4. Approval: one card per pending action, supporting evidence visible, role authorization, explicit confirmation for approve/veto, structured veto reason and optional note, approver/role/time/policy after action, safe stale/superseded/concurrent-change behavior. Show 72-hour/break-glass escalation only where policy is proven.
5. Outcome: stockout-days averted, phantom units blocked, verification hit rate, silent phantoms caught, with definitions, denominator, range, and data coverage; before/after reconciliation and missing-data explanation. Never portray simulated impact as real.
6. Audit: searchable event ledger with event ID, timestamp, actor, action, object, outcome, and chain/hash status. Distinguish verified integrity checks from ordinary IDs/checksums. Provide accessible filtered export with scope/generated-at metadata; highlight exceptional actions by text and icon.
7. Facility/exception details can be contextual drawers across destinations: claimed vs physically attested values, threshold, source/attester provenance, batch/expiry when relevant, history, missing fields, and decision trail.
8. Data pipeline and model provenance may appear within Audit: source import, row quarantine/reason, 18/10/10 feature availability, human-label state, model/data version, split/metrics, and explicit simulated/unverified/real badges.

MOTION / MICRO-INTERACTIONS
Specify useful motion: 200ms page transitions; queue-card expand/collapse; filter feedback; loading skeleton/progressive results; a one-time anomaly cue (never a continuous alarm); risk bars animate on first load; route draw only when a real route exists; and outcome counters animate once per view. Include a 12-step deterministic, captioned “Phantom Trap” demo with play/pause, each step highlighting the affected queue row/allocation card/map marker and stating exactly what demo data changed. Motion never hides provenance or approval. Respect prefers-reduced-motion with a non-animated equivalent.

RESPONSIVE AND ACCESSIBILITY
Design desktop at 1440 and 1280 widths, tablet, and mobile. Define navigation per breakpoint, touch targets, focus order, screen-reader labels, keyboard selection, empty/loading/error/offline/stale/partial states, adequate contrast, and non-color cues. Include realistic but explicitly synthetic sample values where needed; visibly tag every sample as DEMO. Never imply data is live, government-authorized, clinically validated, or connected unless evidence proves it.

STITCH OUTPUT
Return a coherent six-destination screen set, reusable design tokens/components, responsive behavior, English/Hindi key-action labels, interaction/motion notes, and critical empty/loading/error/reduced-motion variants. Provide reviewable captures/frames at 1440px, 1280px, and a mobile viewport. If Stitch can produce artifacts, create them; otherwise provide a screen-by-screen spec ready for implementation. Preserve filter and scroll context when drawers close. Make it obvious what is known, missing, synthetic, suggested, and who decides next. Do not substitute static mockups for working workflows or claim implementation when only a design was produced.
```
