# TATHYON Vertical Slice Architecture Lock

## 1. Six Screens (Max 6)
1. **Risk (Homepage)**: Ranked failure queue by urgency (P(stockout), days left, usable vs claimed stock, causal drivers). What fails next?
2. **Resource**: Node detail (usable, claimed, reserved, incoming, safety floor, expiry, optional route/facility panel).
3. **Twin**: Scenario stress test (+20%, +50%, +100%, warehouse down, lead time x2, outbreak). Baseline vs Response. Provenance: SIMULATION.
4. **Plan**: OR-Tools redistribution ticket with donor buffer check, recipient coverage gain, ton-km, and list of rejected donors with explicit reasons.
5. **Approve**: policy-based Human Medical Officer sign-off (`X-Role: medical_officer`). Commits `plan_approved` to SHA-256 hash chain. Outcome variance tracking (planned vs actual).
6. **Audit**: Cryptographic EventStore timeline with block offset, actor, and hash chain verification.

## 2. Eight Adapters (Max 8)
Each adapter exposes `source_id`, `fetched_at`, `ttl_s`, and `provenance` (`LIVE` | `SYNTHETIC` | `SIMULATION` | `STALE`):
1. `facilities`: Master facility registry (type, tier, district, GPS, cold chain flag).
2. `medicine_usable`: Ground-attested physical stock balance and active batches.
3. `beds`: Total, occupied, and available operational hospital beds.
4. `equipment`: Biomedical equipment uptime and functional verification status.
5. `personnel`: Active duty roster counts (doctors, nurses, pharmacists).
6. `demand_forecast`: Intermittent demand rates from forecast competition.
7. `shock_overlay`: Dynamic macro stress shock parameters applied in Resilience Scenario Engine.
8. `transfer_plan`: Constrained redistribution candidate legs and safety reservations.

## 3. Objects (Strict Required Fields)
- **Facility**: `facility_id`, `name`, `facility_type`, `district`, `lat`, `lon`, `cold_chain_capable`
- **ResourceNode**: `facility_id`, `resource_id`, `resource_type`, `claimed_qty`, `usable_qty`, `reserved_qty`, `incoming_qty`, `safety_floor_days`, `daily_velocity`, `lead_time_days`, `last_attested_at`
- **StockoutForecast**: `facility_id`, `resource_id`, `p_stockout`, `days_to_stockout`, `shortage_qty`, `lead_time_demand`, `selected_model`, `naive_mase`, `winner_mase`, `drivers`
- **TwinRun**: `simulation_id`, `shock`, `resource_id`, `horizon_days`, `baseline_stockouts`, `baseline_unmet_units`, `baseline_coverage_pct`, `response_stockouts`, `response_unmet_units`, `response_coverage_pct`, `provenance` (SIMULATION)
- **ResponsePlan**: `plan_id`, `resource_id`, `simulation_id`, `total_quantity`, `transfers`, `rejected_donors`, `status`, `provenance` (SIMULATION)
- **Approval**: `plan_id`, `officer_id`, `role`, `approved_at`, `notes`, `event_offset`
- **Outcome**: `plan_id`, `dispatched_qty`, `received_qty`, `quantity_variance_pct`, `planned_hours`, `actual_hours`, `time_variance_hours`, `stockout_averted`, `recorded_at`

## 4. APIs for the 3-Minute Path
- `GET /risk`: Ranked district failure queue sorted by urgency.
- `GET /graph`: Network graph summary and node states (claimed, usable, reserved, incoming, safety floor).
- `GET /forecast/{fid}/{rid}`: Holdout forecast competition (Naive vs Seasonal Naive vs Croston-SBA vs TSB) declaring winner and MASE.
- `POST /twin/run`: Runs deterministic stress simulation across shocks comparing Baseline vs Response (`provenance: "SIMULATION"`).
- `POST /plans/generate`: Computes constrained redistribution via OR-Tools; lists accepted transfers and rejected donors with reasons.
- `POST /plans/{id}/approve`: Requires `X-Role: medical_officer` header. Commits `plan_approved` event to hash chain. Returns 403 otherwise.
- `POST /brief`: Typed context pack (risk + twin + plan) passed to Gemini for 8-line structured memo with citations. Fallback MOCK if no key.
- `GET /events`: Returns append-only SHA-256 event log.

## 5. Freshness Rule
If `last_attested_at` is older than 48 hours (or missing), `donor_qty = 0.0`. Stale inventory is barred from donation pools.

## 6. Invariants
1. Unverified or stale inventory cannot donate (`transferable_donor_qty = 0.0`).
2. Resilience scenario engine results and proposed response plans must carry `provenance: "SIMULATION"`.
3. Plan approval requires `X-Role: medical_officer` header; returns HTTP 403 on missing/invalid role.
4. Demand forecast must show Naive baseline alongside empirical winner; winner selected solely by holdout MASE without bias.

## 7. Explicit NOT-NOW List
- NO IoT gateway, Modbus, MQTT, BLE, RFID, load-cells, CCTV.
- NO MapLibre/Deck.gl globe hero homepage.
- NO corpus rule engine or open-ended chatbot.
- NO 14-item navigation menu (strictly 6 views).
- NO Epidemic Act legalistic chrome.
- NO complex Gamma-Poisson math headers in web UI.
