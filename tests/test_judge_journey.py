import pytest
from fastapi.testclient import TestClient

import app.main as main
from app.cases import CANONICAL_CASES
from app.repair_app import app


client = TestClient(app)


def _case(case_id: str) -> dict:
    return next(item for item in CANONICAL_CASES if item["id"] == case_id)["order"]


def test_five_minute_judge_journey(monkeypatch, tmp_path):
    """One test mirrors the order a Razorpay judge should be able to verify."""
    monkeypatch.setattr(main, "AUDIT", tmp_path / "audit.jsonl")
    monkeypatch.setattr(main, "OUTCOMES", tmp_path / "outcomes.jsonl")
    monkeypatch.setattr(main, "PAYMENT_EVENTS", tmp_path / "payment_events.jsonl")
    monkeypatch.setattr(main, "IDEMPOTENCY_PATH", tmp_path / "idempotency.jsonl")
    monkeypatch.delenv("RAZORPAY_KEY_ID", raising=False)
    monkeypatch.delenv("RAZORPAY_KEY_SECRET", raising=False)

    # 01 — the deployed app cannot claim READY unless the frozen real model passes
    # its compressed/raw SHA checks.
    ready = client.get("/ready")
    assert ready.status_code == 200
    assert ready.json()["return_risk_detector"]["ready"] is True

    # 02 — judge-visible primary evidence is the large sealed real holdout.
    detector = client.get("/return-risk/status")
    assert detector.status_code == 200
    detector_body = detector.json()
    heldout = detector_body["heldout_test"]
    assert detector_body["model_version"] == "amazon-return-risk-v2"
    assert detector_body["score_is_calibrated_probability"] is False
    assert detector_body["dataset"]["terminal_orders"] == 28417
    assert heldout["n"] == 5726
    assert heldout["positives"] == 362
    assert (heldout["tp"], heldout["fp"], heldout["fn"], heldout["tn"]) == (84, 665, 278, 4699)
    assert heldout["precision"] == pytest.approx(0.11214953271028037)
    assert heldout["recall"] == pytest.approx(0.23204419889502761)
    assert heldout["precision_lift_vs_prevalence"] == pytest.approx(1.7739453709918933)

    # 03 — the exact frozen learned model can score a new order-time feature vector.
    scored = client.post(
        "/return-risk/score",
        json={
            "order_date": "2022-06-10",
            "fulfilment": "Merchant",
            "sales_channel": "Amazon.in",
            "service_level": "Standard",
            "category": "kurta",
            "size": "M",
            "ship_city": "Bengaluru",
            "ship_state": "Karnataka",
            "postal_code": "560038",
            "b2b": False,
            "quantity": 1,
            "amount": 899,
            "item_rows": 1,
            "false_positive_cost_per_order_inr": 250,
        },
    )
    assert scored.status_code == 200
    score_body = scored.json()
    assert score_body["decision"] in {"FLAG_RETURN_RISK", "STANDARD_FLOW"}
    assert score_body["execution"] == "advisory_only"
    assert score_body["score_is_calibrated_probability"] is False
    assert "risk_probability" not in score_body
    assert "payment_link" not in score_body
    assert score_body["heldout_modeled_false_positive_cost_inr"] == pytest.approx(166250)

    # 04 — independent exact-RTO validation is not hidden when it fails.
    metrics = client.get("/metrics").json()
    primary = metrics["primary_evidence"]
    external = metrics["external_rto_validation"]
    assert primary["ready"] is True
    assert primary["heldout_test"]["n"] == 5726
    assert external["verdict"] == "BLOCK_RELEASE"
    assert external["dataset"]["terminal_orders"] == 138
    assert external["heldout_test"]["precision"] == pytest.approx(3 / 13)
    assert external["heldout_test"]["recall"] == pytest.approx(3 / 8)

    # 05 — deterministic governance still proves customer-facing execution safely.
    allow = client.post("/orders/score", json=_case("allow"), headers={"X-CodGate-Mode": "enforce"})
    assert allow.status_code == 200
    allow_body = allow.json()
    assert allow_body["decision"] == "ALLOW_COD"
    assert allow_body["points"] == 0
    assert allow_body["risk_repair"]["status"] == "ALREADY_SAFE"

    force = client.post("/orders/score", json=_case("force"), headers={"X-CodGate-Mode": "enforce"})
    assert force.status_code == 200
    force_body = force.json()
    assert force_body["decision"] == "FORCE_PREPAID"
    assert force_body["points"] == 145
    assert force_body["payment_link"] == "plink_SIMULATED_ord_siwan_temple_01"
    assert force_body["payment_link_mode"] == "simulated"
    assert force_body["risk_repair"]["status"] == "STRUCTURAL_RISK"
    assert force_body["risk_repair"]["best_points"] == 97
    assert force_body["risk_repair"]["best_decision"] == "FORCE_PREPAID"

    paid = client.post(f"/payment-links/{force_body['payment_link']}/paid")
    assert paid.status_code == 200
    assert paid.json()["message"] == "Payment recorded. COD will not be collected."

    stop = client.post("/orders/score", json=_case("stop"), headers={"X-CodGate-Mode": "enforce"})
    assert stop.status_code == 200
    stop_body = stop.json()
    assert stop_body["decision"] == "STOP"
    assert stop_body["rules"][0]["id"] == "R1"
    assert stop_body["risk_repair"]["status"] == "NEEDS_CORRECTION"
    assert stop_body["risk_repair"]["required_fields"] == ["pincode"]

    repairable_order = {
        "order_id": "ord_repairable_judge_01",
        "pincode": "560038",
        "address": "near temple",
        "amount": 899,
        "account_age_days": 2,
        "prepaid_orders": 0,
        "prior_rto_count": 0,
        "orders_count": 0,
        "phone": "9000000001",
        "customer_name": "Repair Proof",
    }
    repairable = client.post("/orders/score", json=repairable_order, headers={"X-CodGate-Mode": "shadow"})
    assert repairable.status_code == 200
    repairable_body = repairable.json()
    assert repairable_body["decision"] == "FORCE_PREPAID"
    assert repairable_body["action"] == "OBSERVE_ONLY"
    assert repairable_body["risk_repair"]["status"] == "REPAIRABLE"
    assert repairable_body["risk_repair"]["best_decision"] == "ALLOW_COD"
    assert repairable_body["payment_link"] is None

    # 06 — old synthetic numbers remain integrity-only, never headline evidence.
    fixture = metrics["regression_fixture"]
    assert fixture["classification"] == "handcrafted_synthetic_regression_fixture"
    assert fixture["sha_matches"] is True

    audit = client.get("/audit/verify").json()
    assert audit["verified"] is True
    assert audit["hashed_rows"] == 4
