"""One-command fail-closed submission preflight.

Run: python -m app.preflight

Primary evidence is the frozen Amazon India RETURN_TO_SELLER detector. Meesho is
an independent exact-RTO validation set that currently fails closed. The old
80-row handcrafted file is retained only for deterministic software regression.
"""

from __future__ import annotations

from .canary import demo_release
from .cases import CANONICAL_CASES
from .evidence_gate import real_evidence_status
from .policy import decide
from .repair import analyze_repair
from .return_risk_runtime import detector_status, score_return_risk
from .score import evaluate, frozen_block

EXPECTED_REGRESSION_FIXTURE = """CodGate v1.0 · n=80 scored=80
Precision 74.2%
Recall    60.5%
false-block ₹1440 (₹180 × FP 8)
missed-RTO  ₹3750 (₹250 × FN 15)
TP 23 · FP 8 · FN 15 · TN 34
SHA-256 327f392da4049860f2eca1399b248f78e313a5e6b1694f6a5057d6573fb8e20a"""
MEESHO_SOURCE_SHA = "bd8dc168d218c403a7519f42364f307fbff26ad56adced18668e79cb9e171b6e"
AMAZON_SOURCE_SHA = "2d174af66d3390f6bdd157fec4e29e076e3454ed6935f124510ccc66f85c459a"
RUNTIME_MODEL_SHA = "ced7e510515cc54ab874f598c4999c6c407d76fce36dccc007d114f128ccd754"


def _fail(message: str) -> None:
    raise SystemExit(f"PREFLIGHT FAIL · {message}")


def main() -> None:
    # Deterministic governance layer.
    results = {}
    for case in CANONICAL_CASES:
        result = decide(dict(case["order"]))
        results[case["id"]] = result
        if result["decision"] != case["expected"]:
            _fail(f"{case['id']} expected {case['expected']}, got {result['decision']}")

    if results["allow"]["points"] != 0:
        _fail("canonical ALLOW must remain 0 points")
    if results["force"]["points"] != 145:
        _fail("canonical FORCE must remain 145 points")
    if results["force"].get("payment_link") is not None:
        _fail("pure decide() must never issue a Payment Link")
    if not results["stop"]["rules"] or results["stop"]["rules"][0]["id"] != "R1":
        _fail("canonical STOP must short-circuit on R1")
    if results["force"]["features"].get("pin_rto_rate") is not None:
        _fail("public prototype must not invent an empirical pincode RTO rate")

    # Old handcrafted dataset is integrity-only.
    fixture = frozen_block(evaluate())
    if fixture != EXPECTED_REGRESSION_FIXTURE:
        _fail("synthetic regression fixture drifted")

    # Primary real learned detector and frozen runtime artifact.
    detector = detector_status()
    heldout = detector["heldout_test"]
    if detector["runtime_model_sha256"] != RUNTIME_MODEL_SHA:
        _fail("frozen real detector model SHA drifted")
    if detector["source_zip_sha256"] != AMAZON_SOURCE_SHA:
        _fail("primary Amazon source SHA drifted")
    if detector["score_is_calibrated_probability"] is not False:
        _fail("balanced logistic score must not be advertised as calibrated probability")
    if detector["dataset"]["terminal_orders"] != 28417:
        _fail("primary real-data population drifted")
    if heldout["n"] != 5726 or heldout["positives"] != 362:
        _fail("sealed primary holdout drifted")
    if (heldout["tp"], heldout["fp"], heldout["fn"], heldout["tn"]) != (84, 665, 278, 4699):
        _fail("primary held-out confusion matrix drifted")
    if abs(heldout["precision"] - 0.11214953271028037) > 1e-12:
        _fail("primary held-out precision drifted")
    if abs(heldout["recall"] - 0.23204419889502761) > 1e-12:
        _fail("primary held-out recall drifted")
    if heldout["precision"] <= heldout["prevalence"]:
        _fail("primary detector lost precision lift over base rate")

    scored = score_return_risk(
        {
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
        }
    )
    if scored["execution"] != "advisory_only" or scored["score_is_calibrated_probability"] is not False:
        _fail("real detector execution/calibration contract drifted")
    if "payment_link" in scored or "risk_probability" in scored:
        _fail("real detector must neither move money nor overclaim calibrated probability")

    # Independent exact-RTO external validation is deliberately visible and weak.
    external = real_evidence_status()
    if external["verdict"] != "BLOCK_RELEASE":
        _fail(f"weak Meesho external validation must BLOCK_RELEASE, got {external['verdict']}")
    if external["provenance"]["zip_sha256"] != MEESHO_SOURCE_SHA:
        _fail("Meesho external source SHA drifted")
    ext_test = external["heldout_test"]
    if (ext_test["tp"], ext_test["fp"], ext_test["fn"], ext_test["tn"]) != (3, 10, 5, 10):
        _fail("Meesho external confusion matrix drifted")

    # Correctability proof.
    force_repair = analyze_repair(dict(CANONICAL_CASES[1]["order"]))
    if force_repair["status"] != "STRUCTURAL_RISK" or force_repair["repairable"] is not False:
        _fail("canonical Siwan order must remain structurally unsafe after address repair")
    if force_repair["base_points"] != 145 or force_repair["best_points"] != 97:
        _fail("canonical Siwan repair proof must remain 145 → 97")
    if force_repair["best_decision"] != "FORCE_PREPAID":
        _fail("canonical Siwan repair must never restore COD")

    # Release-governance regression fixtures.
    canary_expected = {"good": "SHIP", "wide": "SHADOW", "bad": "BLOCK_RELEASE"}
    canary = {}
    for scenario, expected in canary_expected.items():
        result = demo_release(scenario)
        canary[scenario] = result
        if result["verdict"] != expected:
            _fail(f"Risk Canary fixture {scenario} expected {expected}, got {result['verdict']}")
        if len(result["dataset_sha256"]) != 64:
            _fail(f"Risk Canary fixture {scenario} missing sealed evidence hash")
    if not canary["good"]["evidence"]["sufficient_for_ship"]:
        _fail("good regression fixture must clear minimum evidence gate")
    if canary["good"]["evidence"]["paired_cost_delta_ci95_inr"][1] >= 0:
        _fail("good regression fixture must have a 95% paired cost interval below zero")
    if canary["wide"]["blast_radius"] <= canary["wide"]["governance"]["ship_max_blast_radius"]:
        _fail("wide regression fixture must exceed ship blast-radius guardrail")

    print("CodGate Track 02 preflight · PASS")
    print("PRIMARY real detector · RETURN_TO_SELLER · 28,417 terminal orders")
    print("SEALED holdout · n=5,726 · returns=362 · Precision 11.21% · Recall 23.20% · lift 1.77x")
    print("PRIMARY FP order-GMV exposure · ₹443,627 · not claimed as realized merchant loss")
    print("EXTERNAL exact-RTO Meesho · 138 terminal · verdict BLOCK_RELEASE")
    print("EXACT-COD audit · 47 terminal rows · domain audit only, not benchmark")
    print("synthetic n=80 policy set · regression fixture only · SHA exact")
    print("bounded scorer · advisory_only · no payment action · score uncalibrated")
    print("canonical ALLOW 0 · FORCE 145 · STOP R1")
    print("Risk Repair canonical 145 → 97 · STRUCTURAL_RISK")
    print("Risk Canary fixtures · good SHIP · wide SHADOW · bad BLOCK_RELEASE")
    print(f"Primary source SHA {detector['source_zip_sha256']}")
    print(f"Runtime model SHA {detector['runtime_model_sha256']}")


if __name__ == "__main__":
    main()
