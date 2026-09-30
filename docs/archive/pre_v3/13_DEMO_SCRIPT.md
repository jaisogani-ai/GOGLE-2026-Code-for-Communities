# 13 — Demo Script

Target length **4 minutes 20 seconds**, with a hard floor of 3:00 and a hard
ceiling of 5:00. Two columns throughout: what the presenter says, and what the
operator does. One person can run both, but rehearse as two roles — the
commonest demo failure is a presenter narrating a click that has not happened
yet.

Every figure spoken aloud comes from `artifacts/eval_report.json` or
`artifacts/demo_scenario.json`. Nothing is read off a slide that is not in one of
those two files.

---

## Before the room

Run once, from a clean clone, on the presenting machine:

```bash
git clone <repo> tathyon-demo && cd tathyon-demo
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
make all                      # data → eval → demo → web → 62 tests
```

`make all` must end with 62 passing tests and both artifacts regenerated. If it
does not, the demo does not run; use the fallback (§ Fallback).

Then, in two terminals:

```bash
# terminal 1 — the engine
make api                      # uvicorn api.main:app --reload

# terminal 2 — kept ready, not yet run
python -m tathyon.pipeline
```

Browser: `web/workspace.html` open, zoomed so the reason codes are legible from
the back of the room. Second tab: `artifacts/eval_report.json` in a viewer.

---

## 0:00 – 0:25 — The disclaimer, first, not last

| Presenter says | Operator does |
|---|---|
| "Before anything else: every number you are about to see is synthetic. There is no pilot, no deployment, no government partnership and no customer. What I am demonstrating is that the software behaves the way we claim — not that it has changed anything in the world. Twenty-five seconds on that, because it is the only honest way to start." | Title slide. Single line visible: **ALL DATA SYNTHETIC — no pilot, no deployment, no partnership, no revenue.** |

---

## 0:25 – 1:05 — Open on the equipment claim

This scene is first because it needs no build-up. The audience understands it
before the presenter finishes the sentence.

| Presenter says | Operator does |
|---|---|
| "This is an ICU ventilator at facility FAC000. The asset register says FUNCTIONAL. It says FUNCTIONAL for one hundred percent of the hundred and twenty assets in this district — because the register is written once, at procurement, and never again." | Workspace → **Equipment** → asset `FAC000-VENT-0`. Register status `FUNCTIONAL` visible. |
| "The maintenance vendor reports 97.5% uptime on this unit. Across the district the mean vendor-reported uptime is 97.9%, against a 95% SLA. Every vendor is comfortably beating their contract." | Vendor panel: `vendor_reported_uptime 0.9754`, `sla_target 0.95`. |
| "Both of those are claims. Neither has independent evidence behind it. The register is a claim by the custodian; the uptime is a claim by the party being paid against it. In this synthetic district, 49.2% of assets are actually functional. The divergence is 50.8%." | Detector reason code on screen: `UPTIME_SELF_REPORTED`. |
| "Hold that. We will come back to what the system does about it." | Leave the asset open in a background tab. |

---

## 1:05 – 2:50 — The medicine scene

### 1:05 – 1:25 — A naive optimizer proposes a transfer

| Presenter says | Operator does |
|---|---|
| "Different problem, same district. Salbutamol. A facility is four days from stockout. A conventional optimiser looks for the nearest facility with surplus, applies sensible guards — no more than 40% of a donor's stock, donor keeps a safety floor — and proposes a transfer." | Workspace → **Decisions** → `SALB`. The naive arm renders a proposed transfer against the reported balance. |
| "That is a good optimiser. The guards are correct. It has one assumption: that the number in the ledger is the number on the shelf." | Highlight the reported quantity on the donor row. |

### 1:25 – 1:40 — The gate blocks

| Presenter says | Operator does |
|---|---|
| "Tathyon runs the same solve and returns this instead." | Click **Run gated decision**. The decision object renders. |
| "`BLOCKED_VERIFICATION_REQUIRED`. Nineteen source facilities blocked on this SKU alone, each with a reason code — `ATTESTATION_STALE` here — and a remedy attached. Note the HTTP status: two hundred. This is not an error. Errors get retried and swallowed by clients. A typed decision gets rendered." | Status line and `blocked_sources` count visible; expand one to show reason codes. |
| "The unverified stock is not constrained down to zero in the solver. The solver never sees it." | — |

### 1:40 – 2:15 — Evidence captured offline

| Presenter says | Operator does |
|---|---|
| "The remedy names a facility and a resource. Someone has to go and count. Watch this." | Switch to the capture view. |
| "Wifi off. On stage. This is the actual network state, not a simulation of one." | **Turn wifi off physically.** Show the OS indicator to the camera or the room. |
| "The count sheet is paper. A human counts and writes. The AI does not count — vision-language models score between 0.23 and 0.58 on object counting, so a model that counts is a model that will eventually sign a wrong number." | Capture photo of the count sheet. Nonce, frame count and perceptual hash appear on the evidence record. |
| "Gemini transcribes what the human wrote. It reads handwriting; it does not form an opinion about stock. Any cell below the confidence threshold renders blank-to-fill — never pre-filled. Pre-filling would launder a model error into a *signed* record, and a signed record is the thing a third party relies on." | Transcription panel renders. Point at one blank-to-fill cell. |
| "And the person signing is not the person who keeps the register. That is enforced at write time — the store raises if the attester is the custodian. Asking someone to sign that their own books were wrong is a structural conflict." | Attestation panel: attestor `usr_incharge_07`, role and delegation visible. |
| "Wifi back on. The evidence syncs. Nothing was lost and nothing was retried by hand." | **Turn wifi back on.** Sync indicator clears. |

### 2:15 – 2:50 — Verified stock, recomputed forecast, a safe recommendation

| Presenter says | Operator does |
|---|---|
| "Here is what was actually on the shelf." | Verified state renders: usable quantity and unusable quantity side by side against the reported figure. |
| "The optimiser is now handed `Q_alpha` — not the reported figure and not even the point estimate, but the quantity we are confident of having *at least*. The posterior is Gamma–Poisson, Negative Binomial in closed form, and the consumer supplies alpha, because the consumer owns the risk. An optimiser that can re-plan asks for 0.6. A policy-based certificate asks for 0.98." | Posterior panel: `model gamma_poisson_negbin`, `alpha`, `q_alpha`. |
| "The forecast recomputes on verified usable stock, not on the reported balance. This series has an ADI of 5.12, so the model selector picks TSB over the naive mean — that threshold is Syntetos–Boylan, 1.32." | Forecast panel: `TSB (ADI=5.12 > 1.32)`. |
| "And now a transfer that is safe, because the stock behind it has been counted by a person who signed for it." | New decision renders: `ALLOWED`, with the transfer, travel hours and rationale. |
| "A human approves it. This system does not dispatch anything by itself." | Click **Approve**. Approval event appends to the chain. |
| "Across the district, the gate blocked 151 sources and roughly 1,600 phantom units — stock a naive optimiser would have moved that does not physically exist. Those are vehicles dispatched to collect nothing." | Counterfactual panel: `sources_blocked_by_gate`, `phantom_units_naive_would_move`. |

---

## 2:50 – 3:20 — The certificate refuses

Back to the ventilator. This is the emotional peak of the demo and should not be
rushed.

| Presenter says | Operator does |
|---|---|
| "Back to the ventilator. A biomedical engineer went and looked. The asset is present. It was never commissioned." | Switch to the equipment tab. Attestation: `status NOT_COMMISSIONED`, `age_months 36`, `amc_active true`, `last_service_months 31`. |
| "Register said FUNCTIONAL. Vendor said 97.5% uptime, above SLA, and an AMC is being paid on it. Physical evidence says it has never been switched on in service." | Verification state: `REJECTED`, reason `ASSET_NOT_COMMISSIONED`. |
| "Under GFR 2017 Rule 213, fixed assets require annual physical verification, recorded on Form GFR-22. So: emit the certificate." | Click **Emit GFR-22 line item**. |
| "It refuses." | Refusal renders: *Certificate refused: no independent evidence supports the claimed status. A GFR-22 line cannot be emitted without a valid attestation.* `gfr22_certificate_issued: false` |
| "That refusal is the product. Everything else in this repository exists to make that refusal correct and to make it explainable — because the moment a system can be made to certify something it has no evidence for, it stops being a verification layer and becomes a laundering layer." | Leave the refusal on screen for two full seconds before moving. |
| "And every step you just watched is on a hash chain. Replay reproduces the state exactly; nothing computed is stored. Alter any past event and `verify_chain()` returns the offset where it broke." | Audit timeline: 7 events for scene 1, offsets and hashes visible. |

---

## 3:20 – 4:20 — Honest metrics

This slide is not optional and is not the slide that gets cut for time. If the
demo is running long, cut the counterfactual panel in the medicine scene, not
this.

| Presenter says | Operator does |
|---|---|
| "Now the part that usually gets skipped. Three results that do not flatter us." | Metrics slide up. All figures traceable to `artifacts/eval_report.json`. |
| "One. **The trust model is weak.** PR-AUC of 0.079 against a base rate of 0.053. It beats the deterministic staleness rule at 0.047, and it cuts the verification effort needed to catch half the wrong records by about 37% versus random. That is a real result and it is a small one. It is nowhere near what a tuned model on friendly data would show, and the gap is the honest one." | Line 1 highlighted. |
| "Two. **Our forecasting does not beat the naive mean when pooled.** TSB 0.709, naive 0.699. Naive wins. That is a property of our generator — it draws demand from a largely stationary Poisson process, and for stationary Poisson the sample mean is the maximum-likelihood estimator, so naive is near-optimal by construction. Stratify by intermittency and the textbook result comes back: on series with ADI above 1.32, TSB wins, 0.819 against 0.827. The pooled loss stays in the report." | Line 2: both strata shown side by side. |
| "Three. **One of our own honesty checks failed and we did not fix it.** We run a detector, D1, that asks whether the model is flagging *unusual* records rather than *wrong* ones. It fired. We tried facility-relative residualisation. **It did not fix it.** Both arms — naive and residualised — are in the eval report so the failure is auditable. Our diagnosis is that this generator assigns pathologies per facility-SKU series, so facility genuinely carries signal about wrongness, which violates D1's own assumption. The detector is working; its threshold is mis-specified for this data-generating process. We documented it rather than deleting it." | Line 3: both arms visible in the JSON viewer. |
| "Sixty-two tests pass. The hash chain verifies. The Gemini adapter you saw is a mock interface — the boundary is designed, the API call is not built. And all of this is synthetic data. It validates a pipeline. It does not validate a hypothesis about the world." | Final slide: the four facts, no logo, no call to action. |
| "The thesis is one sentence. A health system should not optimise, transfer or certify against a number merely because a database claims it." | Stop. |

---

## Fallback plan — the 2am version

Assume: the venue network is hostile, the wifi toggle does not come back, the
laptop rejoins a captive portal mid-capture, or `make all` fails on stage.

**The rule: the fallback replays a pre-recorded capture through the unmodified
server path. The pipeline, the gate, the state machine and the chain all still
execute live. Only the camera input is pre-recorded.**

**And the presenter says so, on stage, in these words:**

> "The capture you are about to see was recorded earlier — the venue network is
> not cooperating. The photograph and the handwritten sheet are from a recording.
> Everything downstream of it is running live on this machine right now: the same
> API, the same gate, the same solver, the same hash chain. I am replaying the
> input, not the result."

Say it *before* the replay, not afterwards when someone asks. A demo that
silently substitutes a recording is the exact behaviour this project exists to
prevent, and doing it on stage would be the most expensive irony available.

**Preparation (must exist before the event — currently `NOT_STARTED`, `archive/05_WBS.md` K6):**

```bash
# record the capture artifacts once, in a good network, into a fixture dir
mkdir -p artifacts/fallback
# capture the photo + transcription payload exactly as the client would POST it
curl -s -X POST localhost:8000/evidence \
  -H 'content-type: application/json' \
  -d @artifacts/fallback/evidence_payload.json | tee artifacts/fallback/evidence_response.json
```

**On stage, if needed:**

```bash
# 1. engine is already running from `make api` — do not restart it
# 2. replay the recorded capture through the real endpoint
curl -s -X POST localhost:8000/evidence \
  -H 'content-type: application/json' \
  -d @artifacts/fallback/evidence_payload.json

# 3. attestation, gate and decision proceed live and unmodified
curl -s -X POST localhost:8000/attestations -H 'content-type: application/json' \
  -d @artifacts/fallback/attestation_payload.json
curl -s -X POST localhost:8000/decisions -H 'content-type: application/json' \
  -d @artifacts/fallback/decision_request.json
```

**Deeper fallback — if the API will not start at all:**

```bash
python -m tathyon.pipeline          # prints the full two-scene run to stdout
```

This writes `artifacts/demo_scenario.json` and prints the scene summary
including `GFR-22 ISSUED : False`. Narrate from the terminal. It is less
impressive and it is completely honest, which is the correct trade.

**Deepest fallback — if the machine is unusable:** read the numbers off
`artifacts/eval_report.json` on a phone and say plainly that the live demo
failed. Do not show a screen recording of a working demo without saying it is a
recording.

---

## Command reference

```bash
# full reproducible run, from a clean clone
pip install -r requirements.txt
make all                # = data → eval → demo → web → test

# individual stages
make data               # regenerate the SYNTHETIC CAG-anchored corpus
make eval               # writes artifacts/eval_report.json
make demo               # end-to-end, writes artifacts/demo_scenario.json
make web                # builds web/workspace.html
make test               # 62 tests
make api                # uvicorn api.main:app --reload

# the two live checks a sceptic will ask for
python -m pytest tests/test_gate.py -q        # unverified stock cannot move
python -m pytest tests/test_no_leakage.py -q  # no ground truth in FEATURES
```

## Timing discipline

| Segment | Budget | Cut first if long |
|---|---|---|
| Disclaimer | 0:25 | Never cut |
| Equipment claim | 0:40 | Trim vendor detail to one sentence |
| Medicine: naive proposal | 0:20 | Merge into the block |
| Medicine: gate blocks | 0:15 | Never cut — this is the product |
| Medicine: offline capture | 0:35 | Keep the wifi-off moment; drop the phash detail |
| Medicine: verified + recompute | 0:35 | **Cut the counterfactual panel first** |
| Certificate refusal | 0:30 | Never cut |
| Honest metrics | 1:00 | Never cut |

Running under 3:00 means something was skipped. Running over 5:00 means the
honest-metrics slide is about to be sacrificed, which is the one outcome this
script is structured to prevent.
