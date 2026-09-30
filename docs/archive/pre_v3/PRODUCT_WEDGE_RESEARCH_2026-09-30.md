# TATHYON product wedge: evidence and decisions

**Research snapshot:** 2026-09-30  
**Purpose:** Test whether TATHYON should be a broad AI health command center or a narrower operational product.  
**Status:** Desk research only. Buyer willingness to pay, local permissions, data access, and field outcomes remain unvalidated.

## Recommendation

Test an **evidence-backed exception resolution workflow** for medicine availability first: reconcile an authorized facility/warehouse stock export with a time-stamped independent physical check, route the discrepancy to an accountable officer, record a permitted corrective action, and measure time-to-closure and usable availability. Make the product fit beside an incumbent LMIS through file import and export; do not present it as a replacement system.

This is a hypothesis to validate, not a proven market wedge. “Inventory dashboard,” “AI risk score,” and “Google map” are weak wedge descriptions on their own. The valuable job is closing a consequential gap between the record, physically usable stock, and the action/evidence needed to resolve the gap. Do not assume every reported mismatch is fraud, negligence, or an allocation opportunity.

## What the evidence supports

### Existing systems already do substantial supply-chain work

C-DAC describes DVDMS/e-Aushadhi as software for procurement, purchase orders, receipt/issue, inventory, quality control, distribution, and drug-consumption monitoring. C-DAC reports deployments across multiple states and programme-specific uses. A TATHYON pitch that claims these systems do not manage inventory or distribution would be inaccurate. Public material reviewed for this memo does not document an open, generally available API for a particular state deployment; this is **not proof that a private or state-specific interface does not exist**. Treat access, schema, integration permission, and data rights as deployment-specific questions. [C-DAC DVDMS overview](https://cdac.gov.in/index.aspx?id=project_details&projectId=DrugandVaccineDistributionManagementSystem%28DVDMS%29), [C-DAC health-informatics overview](https://cdac.gov.in/index.aspx?id=achieve_health_informatics)

### Digital recording does not eliminate last-mile gaps

NHM's 17th Common Review Mission reports medicine availability observations across visited facilities in Madhya Pradesh, variation against EDL/IPHS expectations, stock-outs of selected essential medicines, procurement-order lead times of roughly 45–90 days, and a need to improve forecasting and regular physical verification. These are findings from visited facilities in one state and a particular review, not a national prevalence estimate. The most actionable implication is that facility availability and decision latency remain operational outcomes worth measuring even where e-Aushadhi is in use. [NHM 17th CRM 2025 report](https://nhm.gov.in/New-Update-2024-26/CRM/17th-CRM-2025.pdf)

CAG's Uttarakhand public-health audit records e-Aushadhi coverage of 49.74% across facilities and users not entering information regularly or in real time; the report says this limited its ability to identify district drug-warehouse requirements. This is a state-specific audit of a historical period, not a statement about current Uttarakhand or every state. CAG's older Punjab e-Aushadhi IT audit likewise documented rollout and workflow shortcomings in its audited period. These audits support testing coverage, freshness, reconciliation and closure—not claiming that e-Aushadhi is universally broken. [CAG Uttarakhand report](https://cag.gov.in/uploads/download_audit_report/2024/Report-No.-3-of-2024_PA-on-PHIMHS-GoUK_English-067b70f865ca290.98427914.pdf), [CAG Punjab e-Aushadhi audit](https://cag.gov.in/webroot/uploads/download_audit_report/2019/Chapter_2_Performance_Audit_of_Report_No_4_of_2019_Social_General_and_Economic_Sectors_Non_PSUs_Government_of_Punjab.pdf)

WHO's 2025 last-mile LMIS guidance identifies data visibility, consistency, traceability, planning, batch/expiry monitoring, stock-on-hand visibility, and wastage as continuing issues, while emphasizing standard logistics indicators. The product should therefore produce trustworthy operational evidence and standard outcome measures, not another ungrounded composite risk score. [WHO LMIS guidance, 2025](https://www.who.int/publications/i/item/9789240111585)

A 2025 cross-sectional study in Zambia reports differences between physical counts and eLMIS stock-out values for selected commodities in Copperbelt public hospitals. It is useful as an analogous question generator, not as evidence of the size or nature of the Indian problem. [Zambia LMIS study](https://doi.org/10.4236/pp.2025.162005)

## Product and competitor lessons

### HealthGrid: what to learn and what not to copy

A winning team member's public post describes HealthGrid as a Smart Health district command center: voice updates from field workers, medicine-demand forecasts, facility risk scores, maps, administrator recommendations, and notifications. A media report lists HealthGrid/Noida Boys as the Smart Health winner. These are respectively a team account and secondary reporting; no public judging rubric or independently evaluated deployment results were found in this research pass. Learn that a coherent detect→recommend→notify→acknowledge narrative can communicate well. Do not copy its command-center surface, claim it runs live, or infer proven impact from a competition win. TATHYON needs a sharper proof loop: show the source, corroborating physical evidence, responsible reviewer, closure action, and measured result. [Team member's account](https://www.linkedin.com/posts/saatvik-das_googlecloud-hack2skill-codeforcommunities-activity-7484472104856313856-2sh_), [event coverage naming winners](https://cxotoday.com/media-coverage/google-connects-developers-and-mps-to-tackle-local-challenges-with-ai/)

### YC and Field Intelligence

YC's Farmako company page describes an initial shift from a broader digital health-record vision toward medicine fulfilment, B2B distribution, pharmacy operations and APIs. Its current scale and partner numbers are company-provided claims on YC's page, not independently audited here. The lesson is the operational wedge and paying workflow, not “add AI.” [YC Farmako profile](https://www.ycombinator.com/companies/farmako-healthcare)

Field Intelligence's Shelf Life combines forecasting with procurement, delivery, inventory management and financing for participating pharmacies; its public materials describe pay-as-you-sell/consignment economics. It is a vertically operated supply and finance model with distribution execution, not a dashboard-only software analogue. TATHYON should not imply it can copy this without working capital, procurement authority, supplier relationships and logistics. [Field/Shelf Life model](https://field.inc/news/funding-announcement), [Field Nigeria launch and service description](https://field.inc/news/nigeria-expansion)

### Reddit

Patient posts about a prescribed medicine being unavailable at a public-hospital pharmacy are anecdotal and often lack verifiable facility/date/item details. Use such posts only to recruit interview questions (“what happened after the patient was told it was unavailable?”), never to estimate prevalence, severity, or the addressable market. [Example Reddit discussion](https://www.reddit.com/r/india/comments/w6mhby/why_do_doctors_in_hospitals_prescribe_the/)

## Direction scorecard

Scores are directional desk-research judgments (1 low, 5 high), not market measurements.

| Direction | Problem value | Pilotability without API | Differentiation | Risk / access burden | Decision |
|---|---:|---:|---:|---:|---|
| Evidence-backed stock exception resolution and closure | 5 | 4 | 4 | 3 | **Test first** |
| Generic inventory/availability dashboard | 3 | 3 | 1 | 3 | Drop as core wedge; incumbent overlap |
| AI-generated risk scores across medicines, beds, staff | 3 | 2 | 2 | 5 | Defer; labels, evidence, and validation are missing |
| Verification-gated inter-facility allocation | 4 | 2 | 3 | 5 | Keep as a human-approved downstream workflow only |
| Near-expiry/FIFO transfer suggestions | 4 | 3 | 2 | 4 | Pilot only with batch-level usable stock, demand and authority |
| Bed/personnel availability reconciliation | 4 | 2 | 2 | 5 | Separate discovery track; highly sensitive clinical/HR data |
| Patient diversion routing | 4 | 1 | 2 | 5 | Do not operationalize without authorized live capacity and emergency protocols |
| 3D map of facilities/resources | 1 | 5 (demo only) | 1 | 2 | Presentation option; not a wedge |
| Federated/BRICS model | 1 today | 1 | 2 | 5 | Architecture-only; no justified near-term need |
| Pharmacy distribution/working-capital network | 5 | 2 | 2 | 5 | Different capital-intensive business model |

## Proposed first pilot and commercial test

- **User:** district or facility pharmacist/logistics operator who reconciles reported stock and handles verification follow-up; validate this with interviews rather than assuming a statutory job title.
- **Economic buyer:** unknown. Candidate buyers are a state medical-services/procurement corporation, State Health Society, district health administration, or an implementation partner sponsoring a narrowly scoped pilot. No purchase authority, funding line, or willingness to pay has been verified. A private multi-site hospital network may be a faster paid test of reconciliation mechanics, but would not validate government procurement or public-sector workflow.
- **Pilot scope:** one willing organization, a small cohort (for example 5–10 facilities), one clearly mapped commodity class, an authorized export, independent physical counts, and a human-owned exception/closure process. Start in shadow mode; no inventory writes or dispatches.
- **Primary metrics:** coverage and freshness of source records; proportion of records independently checked; discrepancy yield at a fixed verification budget versus random selection; median time from flagged exception to evidence-backed closure; usable-on-shelf availability for selected tracer items; expiry/wastage exposure. Define denominators and missingness before launch.
- **Stop conditions:** no written data permission; facility identifiers cannot be reliably matched; independent attestation is not feasible; queue causes additional unmanageable workload; or a comparator cannot be established.
- **Before access:** synthetic prototype, adapter mapping, user interviews, usability testing, threat/privacy review, and a paper pilot protocol. These validate workflow and usability only, never real-data performance.

## What AI, map, agents, and federation should do

- **AI where useful:** multilingual voice/document extraction from messy reports with source spans and human correction; constrained explanation of a computed anomaly using cited fields/events; operator Q&A over authorized records with explicit “no data” behavior.
- **Deterministic first:** schema validation, row quarantine, arithmetic reconciliation, P6 label calculation, expiry arithmetic, threshold checks, fixed-budget ranking evaluation, route feasibility constraints, and all approvals.
- **Anomaly detection:** the current repo implementation is a deterministic robust-deviation review signal; it is not an AI model and its threshold is not a statistical significance test. Keep its label distinct. If introducing significance claims, preregister a suitable model/test, test temporal stability and false-alert burden, and require enough authorized comparable history.
- **Agents:** the repository has bounded role descriptions and tool guards. Without `GEMINI_API_KEY`, this shell cannot demonstrate live LLM synthesis. A passing deterministic test is evidence for code paths, not proof that six autonomous AI agents run against production data.
- **Maps:** current facility/resource states and route illustrations are synthetic. Google Maps is an optional visualization provider requiring valid user-supplied restricted key and configured Map ID; no credentials are configured in this environment. OSM basemap geography does not make stock values, facility status or routes real. A map becomes operationally useful when location changes an actual human-reviewed choice; 3D/tilt is optional and not a moat.
- **Federation/BRICS:** no immediate cross-country product problem has been demonstrated. Different facility definitions, commodity catalogs, law, governance and data maturity create substantial coordination cost. Keep a threat-modeled architecture proposal and synthetic demo only until several authorized nodes, a common decision task, and measurable improvement over local models exist.

## Current repository reality (rechecked)

Running `.venv/bin/python -m tathyon.real_training --dry-run` on this Mac inspected the current file without fitting a model: 18,000 rows, 30 facilities, 369 label values, hash `8229d3e4c0343d4faedc25749f99996dbaab16c0be3f75e859d275f0ff02fcd8`, generator output detected, no sovereign authorization, no human approval, and status **FILE PRESENT BUT UNVERIFIED / Supervised Training BLOCKED FROM SERVING**. The registry currently records zero approved real-data models. Do not train this data as a real model.

Current shell credential check found `GEMINI_API_KEY`, `GOOGLE_MAPS_API_KEY`, and `GOOGLE_MAPS_MAP_ID` all unset. Therefore live Gemini generation and live Google Maps rendering were not verified. Map resources/routes are explicitly synthetic demo data. The report-anomaly implementation compares user-declared unverified observations using a deterministic robust-z threshold; it writes advisory events to a **process-memory event store**, so it is a local prototype path, not durable production intake. Its current minimum history is eight and it correctly does not call the threshold statistically significant.

The local Mac reports 10 CPU cores and 16 GB RAM; it has enough capacity for the existing small tabular GradientBoosting pipeline. Hardware is not the current blocker; authorized real records and human labels are. Running a real fit with no eligible data would be a fabricated success, so the correct action is to wait for a data-owner-approved pilot dataset and attestation campaign. A no-input one-hour compute loop would not improve readiness.

## Sources and limits

This memo combines government and C-DAC descriptions, audits for specific state/time contexts, WHO guidance, one analogous Zambia study, YC/company material, secondary hackathon coverage, and a Reddit anecdote. It does not establish current national stockout prevalence, state API availability, procurement authority, willingness to pay, clinical benefit, legal authority for individual users, or current operational status of third-party providers. Those require primary-source follow-up and field interviews.
