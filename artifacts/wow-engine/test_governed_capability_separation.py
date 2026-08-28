from pathlib import Path

from fastapi.testclient import TestClient

import api


SQL_PATH = Path(__file__).with_name("governed_capability_separation.sql")


def test_sql_requires_deployment_calibration_and_production_readiness_for_available():
    normalized = " ".join(SQL_PATH.read_text().split()).lower()

    assert "deployment_contract_pass and calibration_health_status = 'pass' and production_feature_ready then 'available' else 'unavailable'" in normalized
    assert "'deployment_contract_status'" in normalized
    assert "'calibration_health_status'" in normalized
    assert "'production_feature_ready'" in normalized
    assert "'can_execute', false" in normalized


def test_governance_can_report_all_deployment_gates_pass_while_capability_stays_unavailable(monkeypatch):
    gates = [
        {"id": f"G{i:02d}", "status": "PASS", "reason": "TEST_EVIDENCE"}
        for i in range(1, 12)
    ]
    monkeypatch.setattr(
        api,
        "_query_deployment_gate_state",
        lambda: {
            "governed_probability_capability": "UNAVAILABLE",
            "governed_probability_status": "NOT_PRODUCED",
            "deployment_contract_status": "PASS",
            "deployment_gates": gates,
            "calibration_health_status": "BLOCKED",
            "production_feature_ready": False,
            "can_execute": False,
        },
    )
    monkeypatch.setattr(
        api,
        "_query_calibration_health",
        lambda: {
            "calibration_health_status": "BLOCKED",
            "blockers": ["FORWARD_SHADOW_NOT_COMPLETED"],
        },
    )

    response = TestClient(api.app).get("/governance")
    assert response.status_code == 200
    body = response.json()

    assert len(body["deployment_gates"]) == 11
    assert all(gate["status"] == "PASS" for gate in body["deployment_gates"])
    assert body["governed_probability_capability"] == "UNAVAILABLE"
    assert body["governed_probability_status"] == "NOT_PRODUCED"
    assert body["probability_publishable"] is False
    assert body["can_execute"] is False
