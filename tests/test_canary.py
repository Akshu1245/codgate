import pytest

from app.canary import demo_release, evaluate_release


def test_good_candidate_can_ship_with_statistical_evidence():
    result = demo_release("good")
    assert result["verdict"] == "SHIP"
    assert result["loss_class"] == "COD_RTO"
    assert result["rows"] == 200
    assert result["blast_radius"] == pytest.approx(0.08)
    assert result["evidence"]["sufficient_for_ship"] is True
    assert result["evidence"]["paired_cost_delta_ci95_inr"][1] < 0
    assert result["delta"]["modeled_loss_inr"] < 0
    assert result["release_receipt"].startswith("cgrl_")
    assert len(result["dataset_sha256"]) == 64
    assert result["candidate"]["fp"] < result["current"]["fp"]
    assert result["candidate"]["fn"] < result["current"]["fn"]
    assert result["candidate"]["precision_ci95"] is not None
    assert result["candidate"]["recall_ci95"] is not None
    assert result["benchmark"]["candidate_beats_current_on_modeled_loss"] is True
    assert result["benchmark"]["candidate_beats_best_trivial_cost_baseline"] is True


def test_wide_candidate_is_forced_to_shadow_even_when_perfect():
    result = demo_release("wide")
    assert result["verdict"] == "SHADOW"
    assert result["candidate"]["fp"] == 0
    assert result["candidate"]["fn"] == 0
    assert result["delta"]["modeled_loss_inr"] < 0
    assert result["blast_radius"] == pytest.approx(0.20)
    assert result["blast_radius"] > result["governance"]["ship_max_blast_radius"]


def test_bad_candidate_is_blocked_on_rupee_and_slice_regression():
    result = demo_release("bad")
    assert result["verdict"] == "BLOCK_RELEASE"
    assert result["delta"]["modeled_loss_inr"] > 0
    assert "increases modeled loss" in result["reasons"][0]
    assert result["delta"]["max_segment_false_block_rate_delta"] > 0


def test_small_window_can_improve_but_cannot_authorize_ship():
    rows = [
        {
            "order_id": f"small_{i}",
            "merchant_segment": "smb",
            "amount": 1000,
            "actual_rto": i < 4,
            "current_decision": "ALLOW_COD",
            "candidate_decision": "FORCE_PREPAID" if i < 4 else "ALLOW_COD",
        }
        for i in range(12)
    ]
    result = evaluate_release("candidate_from_upstream", rows)
    assert result["candidate"]["fn"] == 0
    assert result["delta"]["modeled_loss_inr"] < 0
    assert result["evidence"]["sufficient_for_ship"] is False
    assert result["verdict"] == "SHADOW"
    assert "too small" in result["reasons"][0]


def test_duplicate_order_ids_fail_closed():
    row = {
        "order_id": "dup",
        "merchant_segment": "smb",
        "amount": 1000,
        "actual_rto": 0,
        "current_decision": "ALLOW_COD",
        "candidate_decision": "ALLOW_COD",
    }
    with pytest.raises(ValueError, match="duplicate order_id"):
        evaluate_release("duplicate_window", [row, dict(row)])


def test_invalid_outcome_is_rejected_instead_of_coerced_truthy():
    row = {
        "order_id": "bad_label",
        "merchant_segment": "smb",
        "amount": 1000,
        "actual_rto": "maybe",
        "current_decision": "ALLOW_COD",
        "candidate_decision": "ALLOW_COD",
    }
    with pytest.raises(ValueError, match="actual_rto"):
        evaluate_release("bad_label_window", [row])


def test_release_receipt_binds_dataset_and_candidate_output():
    first = demo_release("good")
    second = demo_release("wide")
    assert first["dataset_sha256"] != second["dataset_sha256"]
    assert first["release_receipt"] != second["release_receipt"]


def test_canary_accepts_precomputed_outputs_and_never_needs_candidate_model_code():
    rows = [
        {
            "order_id": "a",
            "merchant_segment": "smb",
            "amount": 1000,
            "actual_rto": 1,
            "current_decision": "ALLOW_COD",
            "candidate_decision": "FORCE_PREPAID",
        },
        {
            "order_id": "b",
            "merchant_segment": "smb",
            "amount": 500,
            "actual_rto": 0,
            "current_decision": "ALLOW_COD",
            "candidate_decision": "ALLOW_COD",
        },
    ]
    result = evaluate_release("candidate_from_upstream", rows)
    assert result["rows"] == 2
    assert result["current"]["fn"] == 1
    assert result["candidate"]["fn"] == 0
    assert result["note"].startswith("Verifier consumes precomputed")
