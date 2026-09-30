from datetime import datetime, timedelta, timezone

from tathyon.anomaly_detection import score_observation
from tathyon.schema import EventType


def test_robust_anomaly_service_abstains_without_history_and_flags_outlier():
    assert score_observation(100, [10, 10])['status'] == 'INSUFFICIENT_HISTORY'
    result = score_observation(50, [10] * 8)
    assert result['status'] == 'FLAGGED_FOR_REVIEW'
    assert result['robust_z'] >= 3.5
    assert "not proof" in result['interpretation_limit']


def test_resource_report_anomalies_for_medicines_beds_personnel_are_advisory(api_client):
    client, hdr = api_client, api_client.login("data_officer")
    base = datetime(2025, 1, 1, tzinfo=timezone.utc)
    dimensions = [
        ("MEDICINES", "MED-X", "closing_balance", "packs"),
        ("BEDS", "ICU", "available_staffed_beds", "beds"),
        ("PERSONNEL", "NURSE", "present_hours", "hours"),
    ]

    for day in range(8):
        payload = {
            "source_report_id": f"hist-{day}",
            "observations": [
                {
                    "facility_id": "FAC-01",
                    "resource_module": module,
                    "resource_key": resource,
                    "metric": metric,
                    "value": 10,
                    "unit": unit,
                    "observed_at": (base + timedelta(days=day)).isoformat(),
                }
                for module, resource, metric, unit in dimensions
            ],
        }
        response = client.post("/api/intake/resource-report", json=payload, headers=hdr)
        assert response.status_code == 200
        assert not any(row["flagged"] for row in response.json()["observations"])

    payload = {
        "source_report_id": "current-1",
        "source_class": "UNVERIFIED_USER_SUPPLIED",
        "observations": [
            {
                "facility_id": "FAC-01",
                "resource_module": module,
                "resource_key": resource,
                "metric": metric,
                "value": 50,
                "unit": unit,
                "observed_at": (base + timedelta(days=8)).isoformat(),
            }
            for module, resource, metric, unit in dimensions
        ],
    }
    response = client.post("/api/intake/resource-report", json=payload, headers=hdr)
    assert response.status_code == 200
    result = response.json()
    assert result["verified_state_changed"] is False
    assert len([row for row in result["observations"] if row["flagged"]]) == 3
    assert result["advisory_label"] == "Statistical anomaly signal — human decides"

    queue = client.get("/api/trust/anomalies", headers=hdr).json()
    assert queue["count"] == 3
    assert {row["resource_module"] for row in queue["advisories"]} == {"MEDICINES", "BEDS", "PERSONNEL"}
    assert all(row["advisory_only"] for row in queue["advisories"])
    assert result["provenance"] == "USER_SUPPLIED_UNVERIFIED"
    from api.main import State
    assert all(event.event_type != EventType.ATTESTED for event in State.get().store.events)


def test_resource_report_requires_upload_role_and_rejects_negative_values(api_client):
    obs = {"facility_id": "FAC-02", "resource_module": "BEDS", "resource_key": "ICU",
           "metric": "available_staffed_beds", "value": -1, "unit": "beds",
           "observed_at": "2026-09-01T00:00:00+00:00"}
    body = {"source_report_id": "r1", "observations": [obs]}
    assert api_client.post("/api/intake/resource-report", json=body,
                           headers=api_client.login("auditor")).status_code == 403
    r = api_client.post("/api/intake/resource-report", json=body, headers=api_client.login("data_officer"))
    assert r.status_code == 422 and r.json()["error"] == "INVALID_VALUE"
