from fastapi.testclient import TestClient

import app.main as main
from app.cases import CANONICAL_CASES
from app.repair_app import app


client = TestClient(app)


def _case(case_id: str) -> dict:
    return next(item for item in CANONICAL_CASES if item["id"] == case_id)["order"]


def test_five_minute_judge_journey(monkeypatch, tmp_path):
    """One test mirrors the order a Razorpay judge is likely to click."""
    monkeypatch.setattr(main, "AUDIT", tmp_path / "audit.jsonl")
    monkeypatch.setattr(main, "OUTCOMES", tmp_path / "outcomes.jsonl")
    monkeypatch.setattr(main, "PAYMENT_EVENTS", tmp_path / "payment_events.jsonl")
    monkeypatch.setattr(main, "IDEMPOTENCY_PATH", tmp_path / "idempotency.jsonl")
    monkeypatch.delenv("RAZORPAY_KEY_ID", raising=False)
    monkeypatch.delenv("RAZORPAY_KEY_SECRET", raising=False)

    # 01 — deterministic policy regression: veteran Bengaluru order allows COD.
    allow = client.post("/orders/score", json=_case("allow"), headers={"X-CodGate-Mode": "enforce"})
    assert allow.status_code == 200
    allow_body = allow.json()
    assert allow_body["decision"] == "ALLOW_COD"
    assert allow_body["points"] == 0
    assert allow_body["risk_repair"]["status"] == "ALREADY_SAFE"

    # 02 — deterministic policy regression: high-risk case forces prepaid and the
    # explicit simulated provider proves action wiring without pretending CI is live money.
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

    # 03 — invalid pincode: STOP and ask for correction rather than invent data.
    stop = client.post("/orders/score", json=_case("stop"), headers={"X-CodGate-Mode": "enforce"})
    assert stop.status_code == 200
    stop_body = stop.json()
    assert stop_body["decision"] == "STOP"
    assert stop_body["rules"][0]["id"] == "R1"
    assert stop_body["risk_repair"]["status"] == "NEEDS_CORRECTION"
    assert stop_body["risk_repair"]["required_fields"] == ["pincode"]

    # Counterfactual proof — a fixable address-quality block can regain COD, but
    # the score response itself remains FORCE_PREPAID until corrected and re-run.
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
    assert repairable_body["points"] == 60
    assert repairable_body["risk_repair"]["status"] == "REPAIRABLE"
    assert repairable_body["risk_repair"]["best_points"] == 12
    assert repairable_body["risk_repair"]["best_decision"] == "ALLOW_COD"
    assert repairable_body["payment_link"] is None

    # Real public-data evidence is the primary benchmark. The measured candidate
    # is weak, so the correct working behavior is to block its release.
    metrics = client.get("/metrics").json()
    primary = metrics["primary_evidence"]
    assert primary["verdict"] == "BLOCK_RELEASE"
    assert primary["dataset"]["terminal_orders"] == 138
    assert primary["heldout_test"]["tp"] == 3
    assert primary["heldout_test"]["fp"] == 10
    assert primary["heldout_test"]["fn"] == 5
    assert primary["heldout_test"]["tn"] == 10
    assert primary["heldout_test"]["precision"] == 3 / 13
    assert primary["heldout_test"]["recall"] == 3 / 8

    # The old 80-row set remains only to catch deterministic policy drift.
    fixture = metrics["regression_fixture"]
    assert fixture["classification"] == "handcrafted_synthetic_regression_fixture"
    assert fixture["sha_matches"] is True

    audit = client.get("/audit/verify").json()
    assert audit["verified"] is True
    assert audit["hashed_rows"] == 4
