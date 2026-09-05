import copy

import pytest
from fastapi.testclient import TestClient

from app.evidence_gate import evaluate_evidence_report, load_real_evidence, real_evidence_status
from app.repair_app import app


client = TestClient(app)
SOURCE_SHA = "bd8dc168d218c403a7519f42364f307fbff26ad56adced18668e79cb9e171b6e"


def test_frozen_real_evidence_is_exact_and_blocks_release():
    result = real_evidence_status()
    test = result["heldout_test"]
    dataset = result["dataset"]

    assert result["verdict"] == "BLOCK_RELEASE"
    assert result["raw_rows_embedded"] is False
    assert result["provenance"]["dataset_slug"] == "sahilr05/meesho-orders"
    assert result["provenance"]["zip_sha256"] == SOURCE_SHA

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


def test_evidence_api_returns_same_fail_closed_verdict():
    response = client.get("/evidence/real-rto")
    assert response.status_code == 200
    body = response.json()
    assert body["verdict"] == "BLOCK_RELEASE"
    assert body["heldout_test"]["precision"] == pytest.approx(3 / 13)
    assert body["heldout_test"]["recall"] == pytest.approx(3 / 8)
    assert body["provenance"]["zip_sha256"] == SOURCE_SHA


def test_deployed_metrics_are_real_evidence_first():
    response = client.get("/metrics")
    assert response.status_code == 200
    body = response.json()

    assert "precision" not in body
    assert body["primary_evidence"]["verdict"] == "BLOCK_RELEASE"
    assert body["primary_evidence"]["heldout_test"]["precision"] == pytest.approx(3 / 13)
    assert body["primary_evidence"]["provenance"]["zip_sha256"] == SOURCE_SHA

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


def test_live_rupee_cost_is_labeled_assumption():
    response = client.get("/metrics/live")
    assert response.status_code == 200
    body = response.json()
    assert body["cost_model"]["classification"] == "assumption"
    assert body["cost_model"]["measured_merchant_economics"] is False
    assert body["cost_model"]["false_block_inr_per_case"] == 180
    assert body["cost_model"]["missed_rto_inr_per_case"] == 250


def test_ops_status_exposes_release_block_separately_from_service_health():
    response = client.get("/ops/status")
    assert response.status_code == 200
    body = response.json()
    assert body["release_authorized"] is False
    assert body["primary_evidence"]["verdict"] == "BLOCK_RELEASE"
    assert body["regression_fixture"]["integrity_only"] is True


def test_standalone_evidence_can_never_ship_even_if_metrics_are_good():
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
