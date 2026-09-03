from app.canary import demo_release, evaluate_release


def test_good_candidate_can_ship_without_touching_transaction_policy():
    result = demo_release("good")
    assert result["verdict"] == "SHIP"
    assert result["blast_radius"] == 0.15
    assert result["delta"]["modeled_loss_inr"] < 0
    assert result["release_receipt"].startswith("cgrl_")
    assert result["candidate"]["fp"] < result["current"]["fp"]
    assert result["candidate"]["fn"] < result["current"]["fn"]


def test_wide_candidate_is_forced_to_shadow_even_when_better():
    result = demo_release("wide")
    assert result["verdict"] == "SHADOW"
    assert result["delta"]["modeled_loss_inr"] < 0
    assert result["blast_radius"] > result["governance"]["ship_max_blast_radius"]


def test_bad_candidate_is_blocked_on_rupee_regression():
    result = demo_release("bad")
    assert result["verdict"] == "BLOCK_RELEASE"
    assert result["delta"]["modeled_loss_inr"] > 0
    assert "increases modeled loss" in result["reasons"][0]


def test_canary_accepts_precomputed_outputs_and_never_needs_a_candidate_model():
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
    assert result["note"].startswith("Canary verifies precomputed")
