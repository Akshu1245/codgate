import copy

import pytest
from fastapi.testclient import TestClient

from app.evidence_gate import evaluate_evidence_report, load_real_evidence, real_evidence_status
from app.repair_app import app
from app.return_risk_runtime import detector_status, score_return_risk


client = TestClient(app)
MEESHO_SOURCE_SHA = "bd8dc168d218c403a7519f42364f307fbff26ad56adced18668e79cb9e171b6e"
AMAZON_SOURCE_SHA = "2d174af66d3390f6bdd157fec4e29e076e3454ed6935f124510ccc66f85c459a"
RUNTIME_MODEL_SHA = "ced7e510515cc54ab874f598c4999c6c407d76fce36dccc007d114f128ccd754"


def test_frozen_meesho_external_evidence_is_exact_and_blocks_release():
    result = real_evidence_status()
    test = result["heldout_test"]
    dataset = result["dataset"]

    assert result["verdict"] == "BLOCK_RELEASE"
    assert result["raw_rows_embedded"] is False
    assert result["provenance"]["dataset_slug"] == "sahilr05/meesho-orders"
    assert result["provenance"]["zip_sha256"] == MEESHO_SOURCE_SHA

    assert dataset["terminal_orders"] == 138
    assert dataset["terminal_rto"] == 28
    assert dataset["terminal_delivered"] == 110

    assert test["n"] == 28
    assert test["positives"] == 8
    assert (test["tp"], test["fp"], test["fn"], test["tn"]) == (3, 10, 5, 10)
    assert test["precision"] == pytest.approx(3 / 13)
    assert test["recall"] == pytest.approx(3 / 8)
    assert test["roc_auc"] == pytest.approx(0.43125)

    assert any("random-ranking baseline" in reason for reason in result["reasons"])
    assert any("below hold-out prevalence" in reason for reason in result["reasons"])
    assert result["release_receipt"].startswith("cgre_")


def test_external_rto_api_returns_same_fail_closed_verdict():
    response = client.get("/evidence/real-rto")
    assert response.status_code == 200
    body = response.json()
    assert body["verdict"] == "BLOCK_RELEASE"
    assert body["heldout_test"]["precision"] == pytest.approx(3 / 13)
    assert body["heldout_test"]["recall"] == pytest.approx(3 / 8)
    assert body["provenance"]["zip_sha256"] == MEESHO_SOURCE_SHA


def test_frozen_primary_detector_integrity_and_real_heldout_metrics():
    status = detector_status()
    test = status["heldout_test"]
    dataset = status["dataset"]

    assert status["ready"] is True
    assert status["loss_class"] == "RETURN_TO_SELLER"
    assert status["model_version"] == "amazon-return-risk-v2"
    assert status["score_is_calibrated_probability"] is False
    assert status["source_zip_sha256"] == AMAZON_SOURCE_SHA
    assert status["runtime_model_sha256"] == RUNTIME_MODEL_SHA
    assert dataset["terminal_orders"] == 28417
    assert dataset["returned_to_seller"] == 1851
    assert dataset["delivered_to_buyer"] == 26566
    assert test["n"] == 5726
    assert test["positives"] == 362
    assert (test["tp"], test["fp"], test["fn"], test["tn"]) == (84, 665, 278, 4699)
    assert test["precision"] == pytest.approx(0.11214953271028037)
    assert test["recall"] == pytest.approx(0.23204419889502761)
    assert test["prevalence"] == pytest.approx(0.06322039818372337)
    assert test["precision_lift_vs_prevalence"] == pytest.approx(1.7739453709918933)
    assert test["false_positive_order_gmv_at_risk_inr"] == pytest.approx(443627.0)
    assert test["missed_return_order_gmv_inr"] == pytest.approx(187138.0)


def test_return_risk_api_scores_with_frozen_model_and_never_moves_money():
    payload = {
        "order_date": "2022-06-10",
        "fulfilment": "Merchant",
        "sales_channel": "Amazon.in",
        "service_level": "Standard",
        "style": "",
        "sku": "",
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
    }
    response = client.post("/return-risk/score", json=payload)
    assert response.status_code == 200
    body = response.json()
    assert body["model_version"] == "amazon-return-risk-v2"
    assert body["decision"] in {"FLAG_RETURN_RISK", "STANDARD_FLOW"}
    assert body["action"] in {"RISK_REVIEW", "NO_RISK_INTERVENTION"}
    assert body["execution"] == "advisory_only"
    assert body["score_is_calibrated_probability"] is False
    assert "risk_probability" not in body
    assert 0 <= body["risk_score"] <= 1
    assert body["runtime_model_sha256"] == RUNTIME_MODEL_SHA
    assert body["source_zip_sha256"] == AMAZON_SOURCE_SHA
    assert body["heldout_modeled_false_positive_cost_inr"] == pytest.approx(665 * 250)
    assert "payment_link" not in body


def test_deployed_metrics_are_large_real_detector_first():
    response = client.get("/metrics")
    assert response.status_code == 200
    body = response.json()

    assert "precision" not in body
    primary = body["primary_evidence"]
    assert primary["ready"] is True
    assert primary["model_version"] == "amazon-return-risk-v2"
    assert primary["heldout_test"]["n"] == 5726
    assert primary["heldout_test"]["precision"] == pytest.approx(0.11214953271028037)
    assert primary["heldout_test"]["recall"] == pytest.approx(0.23204419889502761)
    assert primary["source_zip_sha256"] == AMAZON_SOURCE_SHA

    external = body["external_rto_validation"]
    assert external["verdict"] == "BLOCK_RELEASE"
    assert external["heldout_test"]["precision"] == pytest.approx(3 / 13)
    assert external["provenance"]["zip_sha256"] == MEESHO_SOURCE_SHA

    fixture = body["regression_fixture"]
    assert fixture["classification"] == "handcrafted_synthetic_regression_fixture"
    assert fixture["claim_allowed"] == "deterministic software regression only"
    assert "production accuracy" in fixture["claim_forbidden"]
    assert fixture["precision"] == pytest.approx(0.7419354838709677)
    assert fixture["cost_values"].startswith("assumptions")


def test_synthetic_csv_endpoint_is_explicitly_classified():
    response = client.get("/data/heldout.csv")
    assert response.status_code == 200
    assert response.headers["X-CodGate-Data-Class"] == "handcrafted-synthetic-regression-fixture"
    assert response.headers["X-CodGate-Claim-Scope"] == "software-regression-only"


def test_live_cost_fields_separate_regression_assumptions_from_real_exposure():
    response = client.get("/metrics/live")
    assert response.status_code == 200
    body = response.json()

    legacy = body["legacy_cost_model"]
    assert legacy["classification"] == "regression-assumption-only"
    assert legacy["measured_merchant_economics"] is False
    assert legacy["false_block_inr_per_case"] == 180
    assert legacy["missed_rto_inr_per_case"] == 250

    real = body["real_detector_cost_model"]
    assert real["default_false_positive_cost_inr"] is None
    assert real["merchant_supplied_required"] is True
    assert real["source_derived_heldout_false_positive_gmv_inr"] == pytest.approx(443627.0)


def test_ops_status_exposes_real_detector_and_external_gate_separately():
    response = client.get("/ops/status")
    assert response.status_code == 200
    body = response.json()
    assert body["return_risk_detector"]["ready"] is True
    assert body["return_risk_detector"]["model_version"] == "amazon-return-risk-v2"
    assert body["return_risk_detector"]["heldout_precision"] == pytest.approx(0.11214953271028037)
    assert body["return_risk_detector"]["heldout_recall"] == pytest.approx(0.23204419889502761)
    assert body["external_rto_validation"]["verdict"] == "BLOCK_RELEASE"
    assert body["regression_fixture"]["integrity_only"] is True


def test_standalone_external_evidence_can_never_ship_even_if_metrics_are_good():
    report = copy.deepcopy(load_real_evidence())
    report["dataset"]["terminal_orders"] = 1000
    report["dataset"]["terminal_rto"] = 200
    report["dataset"]["terminal_delivered"] = 800
    report["heldout_test"] = {
        "n": 200,
        "positives": 40,
        "precision": 0.8,
        "recall": 0.8,
        "roc_auc": 0.9,
        "tp": 32,
        "fp": 8,
        "fn": 8,
        "tn": 152,
    }
    result = evaluate_evidence_report(report)
    assert result["verdict"] == "SHADOW"
    assert result["evidence_sufficient_for_production"] is True
    assert any("SHIP requires paired" in reason for reason in result["reasons"])


def test_tampered_precision_is_rejected_not_normalized():
    report = copy.deepcopy(load_real_evidence())
    report["heldout_test"]["precision"] = 0.99
    with pytest.raises(ValueError, match="precision does not match confusion matrix"):
        evaluate_evidence_report(report)


def test_tampered_confusion_matrix_is_rejected():
    report = copy.deepcopy(load_real_evidence())
    report["heldout_test"]["tn"] += 1
    with pytest.raises(ValueError, match="does not sum to n"):
        evaluate_evidence_report(report)


def test_missing_provenance_is_rejected():
    report = copy.deepcopy(load_real_evidence())
    del report["source"]["zip_sha256"]
    with pytest.raises(ValueError, match="provenance incomplete"):
        evaluate_evidence_report(report)
