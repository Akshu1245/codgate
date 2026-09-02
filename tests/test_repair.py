from fastapi.testclient import TestClient

import app.main as main
from app.cases import CANONICAL_CASES
from app.repair import analyze_repair
from app.repair_app import app


client = TestClient(app)


def _case(case_id: str) -> dict:
    return next(item for item in CANONICAL_CASES if item["id"] == case_id)["order"]


def _repairable_order() -> dict:
    return {
        "order_id": "ord_repairable_01",
        "pincode": "560038",
        "address": "near temple",
        "amount": 899,
        "account_age_days": 2,
        "prepaid_orders": 0,
        "prior_rto_count": 0,
        "orders_count": 0,
        "phone": "9000000001",
        "customer_name": "Repair Test",
    }


def _isolate_runtime(monkeypatch, tmp_path):
    monkeypatch.setattr(main, "AUDIT", tmp_path / "audit.jsonl")
    monkeypatch.setattr(main, "OUTCOMES", tmp_path / "outcomes.jsonl")
    monkeypatch.setattr(main, "PAYMENT_EVENTS", tmp_path / "payment_events.jsonl")
    monkeypatch.setattr(main, "IDEMPOTENCY_PATH", tmp_path / "idempotency.jsonl")
    monkeypatch.delenv("RAZORPAY_KEY_ID", raising=False)
    monkeypatch.delenv("RAZORPAY_KEY_SECRET", raising=False)


def test_repairable_false_block_has_exact_counterfactual():
    repair = analyze_repair(_repairable_order())
    assert repair["status"] == "REPAIRABLE"
    assert repair["repairable"] is True
    assert repair["base_decision"] == "FORCE_PREPAID"
    assert repair["base_points"] == 60
    assert repair["best_decision"] == "ALLOW_COD"
    assert repair["best_points"] == 12
    assert repair["points_reduced"] == 48
    assert repair["repair_kind"] == "complete_address"
    assert repair["required_fields"] == ["address"]
    assert repair["repair_receipt"].startswith("cgrr_")


def test_structural_risk_cannot_be_repaired_by_editing_history():
    repair = analyze_repair(_case("force"))
    assert repair["status"] == "STRUCTURAL_RISK"
    assert repair["repairable"] is False
    assert repair["base_points"] == 145
    assert repair["best_points"] == 97
    assert repair["best_decision"] == "FORCE_PREPAID"
    assert repair["customer_action"] is None
    assert "prior_rto_count" in repair["locked_signals"]
    assert "account_age_days" in repair["locked_signals"]


def test_allow_order_needs_no_repair_and_certificate_is_deterministic():
    order = _case("allow")
    first = analyze_repair(order)
    second = analyze_repair(order)
    assert first["status"] == "ALREADY_SAFE"
    assert first["base_decision"] == "ALLOW_COD"
    assert first["repair_receipt"] == second["repair_receipt"]


def test_stop_requires_real_correction_instead_of_inventing_data():
    repair = analyze_repair(_case("stop"))
    assert repair["status"] == "NEEDS_CORRECTION"
    assert repair["repairable"] is None
    assert repair["required_fields"] == ["pincode"]
    assert repair["best_points"] is None
    assert "will not invent" in repair["proof"]


def test_score_api_attaches_risk_repair_without_changing_decision(monkeypatch, tmp_path):
    _isolate_runtime(monkeypatch, tmp_path)
    order = _repairable_order()
    response = client.post("/orders/score", json=order, headers={"X-CodGate-Mode": "shadow"})
    assert response.status_code == 200
    body = response.json()
    assert body["decision"] == "FORCE_PREPAID"
    assert body["action"] == "OBSERVE_ONLY"
    assert body["payment_link"] is None
    assert body["risk_repair"]["status"] == "REPAIRABLE"
    assert body["risk_repair"]["best_decision"] == "ALLOW_COD"
    assert body["risk_repair"]["base_points"] == body["points"]
