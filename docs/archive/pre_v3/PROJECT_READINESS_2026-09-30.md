# TATHYON project readiness — 2026-09-30

## Decision

**Not ready for public deployment or a real-data/model submission claim.** The local prototype is more honest and safer after this hardening pass, but there is no authorized operational dataset, no authenticated human identity integration, no approved trained model, and no connected government system. The benchmark file in `data/real/` is generated simulation and remains ineligible for operational use and supervised training.

## Current state

| Area | State | Evidence / limitation |
|---|---|---|
| Facility, stock, bed and personnel records | Not connected | Operational queue and map endpoints return empty states. Imported reports remain unverified user input. |
| Real-data model | Not available | Current artifact is rejected for serving; no eligible labels/authorization. |
| AI agents | Partial, local-only | Role D is deterministic/read-only and has no connected verified facts to answer from. Role F reports disconnected status. Roles A/B/C/E are disabled until their source adapters and human controls exist. |
| Gemini | Disabled | No record or document is sent to Gemini. Supplying a key alone will not enable it. |
| Maps | Basemap only while no operational locations are loaded | Google Maps cannot display facility locations; external basemaps are disabled when locations/routes are loaded. Use a locally hosted or separately approved tile source for sensitive facility geography. |
| Consequential workflows | Disabled | Physical attestations, allocation, approvals, dispatch, reconciliation, near-expiry and outcome metrics require trusted identity, authorized sources and evidence. |
| Local network exposure | Reduced | `make api` binds to `127.0.0.1`; Docker instructions publish to loopback and use a named local volume. The service has no production authentication. |
| Public deployment | Disabled | Render/Cloud Run configs are intentionally non-deployable. No security review, retention, backup, monitoring or operating procedures are in place. |

## Verification run

- Python compilation: passed for `api/main.py` and `tathyon/*.py`.
- Browser inline JavaScript syntax check (`node --check`): passed.
- API smoke checks: passed for health/status/model registry, empty queue/map, disabled reports, raw-document rejection and disconnected-role behavior.
- Full existing test suite: **496 passed, 45 failed, 2 errors**. Many failures encode the old fixture/demo contract (non-empty sample queue, fixture facilities, synthetic allocation, fake agent answers). The tests have not yet been rewritten to assert the new fail-closed operational contract, so this suite is not green and this result is not a release sign-off.
- Docker image build and external service checks were not run. No Maps or Gemini key was provided.

## Required before a real pilot or public release

1. Receive source-owner authorization and an untouched, locally stored export with verified provenance; keep synthetic benchmark data out of the training/serving path.
2. Collect genuine, individually linked human P6 attestations and enough facilities/time coverage; preserve missing features as missing and verify the facility/time split.
3. Replace the existing old-contract tests with tests for authorization evidence, source lineage, empty/unavailable states, role restrictions, map data privacy and artifact/registry serving gates; then get a green suite.
4. Integrate trusted officer identity, role binding, approval signatures, audit review, secure local storage/retention, backup/restore and access control.
5. If Gemini is considered, define and approve a redaction boundary and data-processing policy first. A key must not activate document/record transmission by itself.
6. For sensitive facility geography, use local tiles or complete a data-protection review with the map provider. Google Maps facility markers are intentionally blocked.
7. Complete a security review, operational procedures, monitoring, incident response, deployment secrets handling and restore rehearsal before enabling public deployment manifests.
