# Five-minute walkthrough (every step is a real backend transition)

Start: `make api`, open http://127.0.0.1:8000. Identity selector (top right) switches between demo roles; the
server enforces what each role can do.

| # | Do | What the judge should see |
|---|---|---|
| 1 | **Data intake** as District Medical Officer → pick *Gaya, Bihar* → **Load OpenStreetMap facilities** | ~105 real facilities, badge `REAL · OSM`, fetch status PARTIAL shown honestly |
| 2 | **Load sample dataset** for Gaya | Red SAMPLE banner. 36 stock rows, 15 earlier counts, 264 bed/staff reports. Upload your own CSV with a bad row to see typed quarantine |
| 3 | **Trust queue** | Facilities below the alert runway → `TRANSFER`. Two uncounted "surplus" facilities → `VERIFY` with reasons (count stale, outlier vs district) and units at stake per verifier-hour. Confidence is evidence age, not a probability |
| 4 | **Map** | Google basemap, markers coloured by state; click one → stock with provenance, beds & staff with an UNUSUAL signal; 3D button explains why it is unavailable (Map Tiles API) |
| 5 | **Allocation** → SKU `OXY-10` → **Generate options** | Options side by side: *Trust reported* (counterfactual, not approvable), *Transfer verified now* (partial, shortfall), *Verify then transfer* (recommended, rule shown), *Escalate*. Rejected donors with reasons |
| 6 | **Approval** → reason → **Approve** | Contingent lines held; verification task opened; ledger event linked |
| 7 | Switch to **Field Verifier** → Trust queue → **Record count** on the held donor; enter a usable count **below the held line's quantity** (e.g. 20) | Toast: `PHANTOM_STOCK … replan required`; the approved plan becomes REPLAN_REQUIRED. (A count that still covers the line keeps the plan feasible — that is correct.) |
| 8 | **Ask agents** → Replan Watcher → run | Gemini drafts a replan candidate (PROPOSED); it cannot approve it |
| 9 | DMO → **Approval** → approve. If the replan again holds lines on an uncounted donor, count it as the verifier (e.g. the reported amount) → DMO → **Allocation** → **Release after count** → **Dispatch** (or choose *Transfer verified now* in Approval to dispatch immediately) | Shipments IN_TRANSIT; map routes solid; provider GOOGLE_ROUTES_API |
| 10 | **Outcome** → **Report delay** on one shipment → **Act as receiving in-charge** (signs a session scoped to that facility) → **Record receipt** with some damaged | RECEIVED_PARTIAL → automatic REPLAN_REQUIRED (PARTIAL_RECEIPT); runway before → after. Another facility's in-charge would be refused (`NOT_RECIPIENT_FACILITY`) |
| 11 | **Ask agents** → Evidence agent, language *हिन्दी* → "What happened and why did the first plan fail?" → 🔊 Read aloud | Answer in Hindi, every statement with clickable ledger ids |
| 12 | **Audit** → Export CSV; try asking the Copilot "Approve this transfer" | Hash chain verified; the agent refuses and the refusal is itself an event |
