from fastapi.testclient import TestClient

from app.repair_app import app


client = TestClient(app)


def test_release_demo_routes_match_governance_verdicts():
    expected = {"good": "SHIP", "wide": "SHADOW", "bad": "BLOCK_RELEASE"}
    for scenario, verdict in expected.items():
        response = client.get(f"/release/demo/{scenario}")
        assert response.status_code == 200
        body = response.json()
        assert body["verdict"] == verdict
        assert body["release_receipt"].startswith("cgrl_")
        assert body["note"].startswith("Canary verifies precomputed")


def test_release_check_is_internal_verifier_not_candidate_model():
    payload = {
        "release_id": "risk-model-v42",
        "rows": [
            {
                "order_id": "ord1",
                "merchant_segment": "enterprise",
                "amount": 2500,
                "actual_rto": True,
                "current_decision": "ALLOW_COD",
                "candidate_decision": "FORCE_PREPAID",
            },
            {
                "order_id": "ord2",
                "merchant_segment": "enterprise",
                "amount": 900,
                "actual_rto": False,
                "current_decision": "ALLOW_COD",
                "candidate_decision": "ALLOW_COD",
            },
        ],
    }
    response = client.post("/release/check", json=payload)
    assert response.status_code == 200
    body = response.json()
    assert body["release_id"] == "risk-model-v42"
    assert body["candidate"]["fn"] == 0
    assert body["current"]["fn"] == 1


def test_release_check_rejects_unknown_decision_values():
    payload = {
        "release_id": "bad-enum",
        "rows": [
            {
                "order_id": "ord1",
                "merchant_segment": "smb",
                "amount": 500,
                "actual_rto": False,
                "current_decision": "ALLOW_COD",
                "candidate_decision": "YOLO",
            }
        ],
    }
    response = client.post("/release/check", json=payload)
    assert response.status_code == 422
