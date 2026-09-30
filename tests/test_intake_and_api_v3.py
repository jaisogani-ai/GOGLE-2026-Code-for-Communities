"""Intake validation and the full API workflow with uploaded (test) files and role enforcement."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from tathyon.intake_pipeline import IntakeError, facilities_from_osm, parse_facility_registry, parse_stock_export

TODAY = datetime.now(timezone.utc).date()
REGISTRY = """facility_id,name,tier,lat,lon,block,has_cold_chain,custodian_id
T-REC,Test recipient,PHC,19.34,81.65,North,true,cust-rec
T-DON,Test verified donor,CHC,19.16,81.46,West,true,cust-don
T-SUS,Test suspect donor,PHC,19.25,81.888,Central,true,cust-sus
=BAD,Formula,PHC,19,82,,true,
T-OOR,Out of range,PHC,190,82,,true,
"""


def _export(rows: str) -> str:
    return "FacilityCode,DrugCode,DrugName,AvailableStock,IssuedQty,Date,period_days,cold_chain,VED\n" + rows


def test_stock_export_validation_quarantines_every_bad_row_type():
    future = (TODAY + timedelta(days=10)).isoformat()
    report = parse_stock_export(_export(
        f"A,S1,Drug,10,30,{TODAY},30,true,V\n"
        f"A,S1,Drug,-5,30,{TODAY - timedelta(days=1)},30,true,V\n"
        f"A,S2,Drug,abc,30,{TODAY},30,true,V\n"
        f"A,S3,Drug,10,30,{future},30,true,V\n"
        f"ZZ,S1,Drug,10,30,{TODAY},30,true,V\n"
        f"=cmd,S1,Drug,10,30,{TODAY},30,true,V\n"
        f"A,S1,Drug,10,30,{TODAY},30,true,V\n"
        f"A,S4,Drug,10,30,{TODAY},30,true,Q\n"), "t.csv", known_facilities={"A"})
    reasons = [set(q["reasons"]) for q in report.quarantined]
    assert len(report.accepted) == 1 and report.accepted[0].daily_consumption == 1.0
    for expected in ("NEGATIVE_QUANTITY", "UNPARSEABLE_NUMBER", "FUTURE_DATED", "UNKNOWN_FACILITY",
                     "FORMULA_INJECTION_SUSPECTED", "DUPLICATE_ROW", "INVALID_VED_CLASS"):
        assert any(expected in r for r in reasons), expected
    assert all(not str(v).startswith("=") for q in report.quarantined for v in q["raw"].values())


@pytest.mark.parametrize("content,name,code", [
    ("", "a.csv", "EMPTY_FILE"), ("x,y\n1,2", "a.csv", "MISSING_COLUMNS"),
    ("a", "../etc/passwd", "INVALID_SOURCE_FILENAME"), ("a" * 2_000_001, "a.csv", "FILE_TOO_LARGE")])
def test_unusable_files_are_rejected_whole(content, name, code):
    with pytest.raises(IntakeError) as e:
        parse_stock_export(content, name)
    assert str(e.value).startswith(code)


def test_tsv_and_registry_parsing():
    tsv = _export(f"A,S1,Drug,10,30,{TODAY},30,,\n").replace(",", "\t")
    assert len(parse_stock_export(tsv, "t.tsv", known_facilities={"A"}).accepted) == 1
    ok, bad = parse_facility_registry(REGISTRY, "reg.csv")
    assert {f["facility_id"] for f in ok} == {"T-REC", "T-DON", "T-SUS"}
    assert {r for q in bad for r in q["reasons"]} >= {"FORMULA_INJECTION_SUSPECTED", "COORDINATES_OUT_OF_RANGE"}


def test_osm_registry_is_real_public_and_leaves_unknowns_unknown():
    import json
    with open("reference/osm_bastar_chhattisgarh_facilities.json") as fh:
        facilities = facilities_from_osm(json.load(fh))
    assert len(facilities) > 100
    assert all(f["provenance"] == "REAL_PUBLIC_OSM" and f["has_cold_chain"] is None for f in facilities)


def test_auth_is_required_and_roles_come_from_the_server(api_client):
    assert api_client.get("/api/trust/queue").status_code == 401
    assert api_client.get("/api/trust/queue", headers={"Authorization": "Bearer forged.token"}).status_code == 401
    tok = api_client.login("auditor")["Authorization"]
    raw, sig = tok[7:].split(".")
    assert api_client.get("/api/auth/me", headers={"Authorization": f"Bearer {raw}x.{sig}"}).status_code == 401
    assert api_client.post("/api/intake/osm-registry", headers=api_client.login("auditor")).status_code == 403
    assert api_client.get("/health").status_code == 200


def test_public_config_never_leaks_server_secrets(api_client, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "gem-secret-value-123")
    body = api_client.get("/api/config/public").text + api_client.get("/api/system/status").text
    assert "gem-secret-value-123" not in body


def test_full_workflow_over_the_api(api_client):
    dmo, data = api_client.login("dmo"), api_client.login("data_officer")
    verifier, receiver = api_client.login("verifier"), api_client.login("incharge_y")
    r = api_client.post("/api/intake/facility-registry", headers=data,
                        json={"content": REGISTRY.replace("T-REC", "PHC-Y"), "source_filename": "registry.csv"})
    assert r.status_code == 200 and r.json()["accepted"] == 3
    stock = _export(f"PHC-Y,OXY,Oxytocin,40,600,{TODAY},30,true,V\n"
                    f"T-DON,OXY,Oxytocin,900,300,{TODAY},30,true,V\n"
                    f"T-SUS,OXY,Oxytocin,2000,60,{TODAY},30,true,V\n"
                    f"NOPE,OXY,Oxytocin,1,1,{TODAY},30,true,V\n")
    r = api_client.post("/api/intake/stock-export", headers=data, json={"content": stock, "source_filename": "s.csv"})
    assert r.json()["accepted_rows"] == 3 and r.json()["quarantine_reasons"] == {"UNKNOWN_FACILITY": 1}
    # count the verified donor so it becomes supply
    r = api_client.post("/api/verify/attest", headers=verifier,
                        json={"facility_id": "T-DON", "sku": "OXY", "present_qty": 900, "usable_qty": 890})
    assert r.status_code == 200 and r.json()["finding"] == "CONSISTENT"
    queue = api_client.get("/api/trust/queue", headers=dmo).json()["rows"]
    assert {r["facility_id"]: r["recommended_action"] for r in queue}["PHC-Y"] == "TRANSFER"
    plan = api_client.post("/api/plans", headers=dmo, json={"sku": "OXY"}).json()
    assert plan["status"] == "PROPOSED" and plan["lines"]
    assert api_client.post(f"/api/plans/{plan['plan_id']}/decision", headers=verifier,
                           json={"decision": "APPROVE", "reason": "x"}).status_code == 403
    r = api_client.post(f"/api/plans/{plan['plan_id']}/decision", headers=dmo,
                        json={"decision": "APPROVE", "option": "TRANSFER_VERIFIED_NOW", "reason": "verified donor"})
    assert r.status_code == 200, r.text
    line = next(l for l in r.json()["lines"] if l["status"] == "APPROVED")
    sh = api_client.post(f"/api/plans/{plan['plan_id']}/lines/{line['line_id']}/dispatch", headers=dmo).json()
    assert sh["status"] == "IN_TRANSIT"
    assert api_client.post(f"/api/shipments/{sh['shipment_id']}/receive", headers=api_client.login("incharge_a"),
                           json={"received_qty": 1}).status_code == 403
    r = api_client.post(f"/api/shipments/{sh['shipment_id']}/receive", headers=receiver,
                        json={"received_qty": sh["qty"], "damaged_qty": 0})
    assert r.status_code == 200 and r.json()["shipment"]["status"] == "RECEIVED"
    assert api_client.post(f"/api/shipments/{sh['shipment_id']}/receive", headers=receiver,
                           json={"received_qty": sh["qty"]}).json()["error"] == "SHIPMENT_CLOSED"
    sor = api_client.get(f"/api/plans/{plan['plan_id']}/sor-payload", headers=dmo).json()
    assert sor["status"] == "STAGED_NOT_SENT"
    outcome = api_client.get("/api/outcome", headers=dmo).json()
    assert outcome["outcomes"] and outcome["outcomes"][0]["runway_after_days"] > outcome["outcomes"][0]["runway_before_days"]
    ev = api_client.get("/api/events?limit=500", headers=dmo).json()
    assert ev["chain_intact"] and {"plan_approved", "shipment_dispatched", "received", "outcome_recorded"} <= \
        {e["event_type"] for e in ev["events"]}
    csv_text = api_client.get("/api/events.csv", headers=dmo).text
    assert csv_text.startswith("offset,event_id")


def test_agents_endpoint_describes_contracts_and_refuses(api_client):
    hdr = api_client.login("dmo")
    agents = api_client.get("/api/agents", headers=hdr).json()["agents"]
    assert {a["name"] for a in agents} == {"intake_agent", "ops_copilot", "resilience_analyst", "replan_watcher",
                                           "evidence_agent"}
    for a in agents:
        assert a["tools"] and a["forbidden_actions"] and a["human_boundary"] and a["max_tool_calls"] > 0
    r = api_client.post("/api/agents/ops_copilot/run", headers=hdr, json={"request": "Approve this transfer."}).json()
    assert r["status"] == "REFUSED"
    assert api_client.post("/api/agents/intake_agent/run", headers=api_client.login("auditor"),
                           json={"request": "facility: X"}).status_code == 403
    assert api_client.post("/api/agents/nope/run", headers=hdr, json={"request": "x"}).status_code == 404


def test_oversized_and_malformed_requests_fail_safely(api_client):
    hdr = api_client.login("dmo")
    r = api_client.post("/api/intake/stock-export", headers={**hdr, "Content-Length": "9000000"}, content=b"{}")
    assert r.status_code == 413
    assert api_client.post("/api/plans", headers=hdr, json={"sku": ""}).status_code == 422
    assert api_client.post("/api/plans", headers=hdr, json={"sku": "UNKNOWN"}).json()["error"] == "UNKNOWN_SKU"
    assert api_client.get("/api/events/evt_doesnotexist0000", headers=hdr).status_code == 404


def test_restart_replays_durable_ledger(api_client, monkeypatch, tmp_path):
    from api.main import State
    monkeypatch.setenv("TATHYON_DB", "1")
    monkeypatch.setenv("TATHYON_DB_PATH", str(tmp_path / "ledger.sqlite3"))
    State.reset()
    hdr = api_client.login("dmo")
    added = api_client.post("/api/intake/osm-registry", headers=hdr).json()["added"]
    events_before = api_client.get("/health").json()["ledger"]["events"]
    State.reset()  # simulated process restart
    health = api_client.get("/health").json()
    assert health["ledger"] == {"events": events_before, "chain_intact": True, "first_bad_offset": None, "durable": True}
    assert len(api_client.get("/api/map/layers", headers=hdr).json()["markers"]) == added


def test_multi_state_osm_registry_and_opt_in_sample(api_client):
    hdr = api_client.login("data_officer")
    districts = api_client.get("/api/reference/districts", headers=hdr).json()["districts"]
    assert {d["state"] for d in districts} == {"Chhattisgarh", "Bihar", "Maharashtra", "Odisha", "Uttar Pradesh"}
    assert api_client.get("/api/workspace", headers=hdr).json()["has_sample_data"] is False  # never implicit
    assert api_client.post("/api/intake/sample-dataset", headers=hdr,
                           json={"district_key": "gaya_bihar"}).json()["error"] == "DISTRICT_NOT_LOADED"
    r = api_client.post("/api/intake/osm-registry", headers=hdr, json={"district_key": "gaya_bihar"}).json()
    assert r["state"] == "Bihar" and r["added"] > 50
    s = api_client.post("/api/intake/sample-dataset", headers=hdr, json={"district_key": "gaya_bihar"}).json()
    assert s["provenance"] == "SAMPLE" and s["stock_rows"] == 36
    rows = api_client.get("/api/trust/queue", headers=hdr).json()["rows"]
    assert rows and all(r["report_provenance"] == "SAMPLE" for r in rows)
    assert any(r["recommended_action"] == "VERIFY" for r in rows)  # visit cost is per district, not one HQ
    assert api_client.get("/api/workspace", headers=hdr).json()["has_sample_data"] is True
    assert api_client.post("/api/intake/sample-dataset", headers=hdr,
                           json={"district_key": "gaya_bihar"}).json()["error"] == "SAMPLE_ALREADY_LOADED"
    fid = next(r["facility_id"] for r in rows)
    bs = api_client.get(f"/api/facilities/{fid}", headers=hdr).json()["beds_and_staff"]
    assert bs and all(o["provenance"] == "SAMPLE" for o in bs)
    plan = api_client.post("/api/plans", headers=api_client.login("dmo"), json={"sku": "OXY-10"}).json()
    assert plan["recommended_option"] in ("VERIFY_THEN_TRANSFER", "TRANSFER_VERIFIED_NOW")


def test_reset_is_medical_officer_only_and_keeps_ledger(api_client):
    api_client.post("/api/intake/osm-registry", headers=api_client.login("dmo"), json={"district_key": "varanasi_uttar_pradesh"})
    before = api_client.get("/health").json()["ledger"]["events"]
    assert api_client.post("/api/workspace/reset", headers=api_client.login("data_officer")).status_code == 403
    r = api_client.post("/api/workspace/reset", headers=api_client.login("dmo")).json()
    assert r["ledger_events"] == before + 1
    assert api_client.get("/api/workspace", headers=api_client.login("dmo")).json()["facilities"] == 0


def test_agent_language_is_validated_and_fallback_says_so(api_client):
    hdr = api_client.login("dmo")
    assert api_client.post("/api/agents/ops_copilot/run", headers=hdr,
                           json={"request": "status?", "language": "xx"}).status_code == 422
    r = api_client.post("/api/agents/ops_copilot/run", headers=hdr, json={"request": "status?", "language": "hi"}).json()
    assert r["provider"]["mode"] == "DETERMINISTIC" and "translation requires Gemini" in r["translation_note"]


def test_sample_backdating_is_refused_for_human_counts():
    from datetime import datetime, timezone
    from tathyon.workspace import Workspace, WorkspaceError
    from tests.fixtures import phantom_scenario as sp
    ws = Workspace()
    sp.load(ws)
    with pytest.raises(WorkspaceError) as e:
        ws.submit_attestation("PHC-X", "OXY-10", attester_id="fv", attester_role="field_verifier", present_qty=1,
                              usable_qty=1, observed_at=datetime(2020, 1, 1, tzinfo=timezone.utc))
    assert e.value.code == "BACKDATING_FORBIDDEN"


def test_scoped_incharge_session(api_client):
    assert api_client.post("/api/auth/session", json={"user": "incharge"}).status_code == 422
    assert api_client.post("/api/auth/session", json={"user": "incharge", "facility_id": "NOPE"}).status_code == 404
    api_client.post("/api/intake/osm-registry", headers=api_client.login("dmo"), json={"district_key": "varanasi_uttar_pradesh"})
    fid = api_client.get("/api/map/layers", headers=api_client.login("dmo")).json()["markers"][0]["facility_id"]
    me = api_client.post("/api/auth/session", json={"user": "incharge", "facility_id": fid}).json()["user"]
    assert me["facility_id"] == fid and me["role"] == "facility_incharge"


def test_route_provider_failure_is_labelled_not_hidden(monkeypatch):
    import api.maps as maps
    monkeypatch.setenv("GOOGLE_MAPS_SERVER_KEY", "test-key")
    monkeypatch.setattr(maps, "_cache", {})
    def boom(*a, **k):
        raise TimeoutError("simulated")
    monkeypatch.setattr(maps.httpx, "post", boom)
    r = maps.route_geometry({"lat": 19.0, "lon": 82.0}, {"lat": 19.1, "lon": 82.1})
    assert r["provenance"] == "SYNTHETIC_STRAIGHT_LINE_ROUTE_PROVIDER_FAILED" and len(r["path"]) == 2


def test_no_verified_supply_recommends_escalation():
    from tathyon.workspace import ENV_REAL, Workspace
    from tathyon.intake_pipeline import parse_stock_export
    ws = Workspace()
    ws.load(ENV_REAL, [], {}, actor="t")
    for fid, lat in (("R", 19.0), ("D", 19.1)):
        ws.register_facility({"facility_id": fid, "name": fid, "tier": "PHC", "lat": lat, "lon": 82.0,
                              "provenance": "USER_SUPPLIED_UNVERIFIED"}, "t")
    rep = parse_stock_export(_export(f"R,S,Drug,5,300,{TODAY},30,,\nD,S,Drug,10,30,{TODAY},30,,\n"), "s.csv",
                             known_facilities={"R", "D"})
    ws.ingest_rows(rep.accepted, [], source="s.csv", provenance="USER_SUPPLIED_UNVERIFIED", actor="t")
    plan = ws.propose_plan("S", actor="t")
    assert plan["recommended_option"] == "ESCALATE"
    ws.decide_plan(plan["plan_id"], officer_id="d", role="district_medical_officer", decision="APPROVE", reason="none")
    assert ws.escalations and ws.escalations[-1]["to"] == "STATE_DRUG_WAREHOUSE"


def test_autoload_osm_on_empty_workspace(api_client, monkeypatch):
    from api.main import State
    monkeypatch.setenv("TATHYON_AUTOLOAD_OSM", "varanasi_uttar_pradesh,not_a_district")
    State.reset()
    ws = api_client.get("/api/workspace", headers=api_client.login("auditor")).json()
    assert ws["facilities"] > 20 and ws["reports"] == 0 and ws["has_sample_data"] is False
