"""One-command submission preflight.

Run: python -m app.preflight

This is intentionally boring and fail-closed. It separates real public-data
release evidence from deterministic synthetic regression fixtures.
"""

from __future__ import annotations

from .canary import demo_release
from .cases import CANONICAL_CASES
from .evidence_gate import real_evidence_status
from .policy import decide
from .repair import analyze_repair
from .score import evaluate, frozen_block

EXPECTED_REGRESSION_FIXTURE = """CodGate v1.0 · n=80 scored=80
Precision 74.2%
Recall    60.5%
false-block ₹1440 (₹180 × FP 8)
missed-RTO  ₹3750 (₹250 × FN 15)
TP 23 · FP 8 · FN 15 · TN 34
SHA-256 327f392da4049860f2eca1399b248f78e313a5e6b1694f6a5057d6573fb8e20a"""
REAL_SOURCE_SHA = "bd8dc168d218c403a7519f42364f307fbff26ad56adced18668e79cb9e171b6e"


def _fail(message: str) -> None:
    raise SystemExit(f"PREFLIGHT FAIL · {message}")


def main() -> None:
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

    fixture = frozen_block(evaluate())
    if fixture != EXPECTED_REGRESSION_FIXTURE:
        _fail("synthetic regression fixture drifted")

    real = real_evidence_status()
    if real["verdict"] != "BLOCK_RELEASE":
        _fail(f"weak real public-data candidate must BLOCK_RELEASE, got {real['verdict']}")
    if real["provenance"]["zip_sha256"] != REAL_SOURCE_SHA:
        _fail("real evidence source SHA drifted")
    heldout = real["heldout_test"]
    if (heldout["tp"], heldout["fp"], heldout["fn"], heldout["tn"]) != (3, 10, 5, 10):
        _fail("real held-out confusion matrix drifted")
    if abs(heldout["precision"] - (3 / 13)) > 1e-12 or abs(heldout["recall"] - (3 / 8)) > 1e-12:
        _fail("real held-out precision/recall drifted")

    force_repair = analyze_repair(dict(CANONICAL_CASES[1]["order"]))
    if force_repair["status"] != "STRUCTURAL_RISK" or force_repair["repairable"] is not False:
        _fail("canonical Siwan order must remain structurally unsafe after address repair")
    if force_repair["base_points"] != 145 or force_repair["best_points"] != 97:
        _fail("canonical Siwan repair proof must remain 145 → 97")
    if force_repair["best_decision"] != "FORCE_PREPAID":
        _fail("canonical Siwan repair must never restore COD")

    canary_expected = {"good": "SHIP", "wide": "SHADOW", "bad": "BLOCK_RELEASE"}
    canary = {}
    for scenario, expected in canary_expected.items():
        result = demo_release(scenario)
        canary[scenario] = result
        if result["verdict"] != expected:
            _fail(f"Risk Canary regression fixture {scenario} expected {expected}, got {result['verdict']}")
        if len(result["dataset_sha256"]) != 64:
            _fail(f"Risk Canary regression fixture {scenario} missing sealed evidence hash")
    if not canary["good"]["evidence"]["sufficient_for_ship"]:
        _fail("good regression fixture must clear minimum evidence gate")
    if canary["good"]["evidence"]["paired_cost_delta_ci95_inr"][1] >= 0:
        _fail("good regression fixture must have a 95% paired cost interval below zero")
    if canary["wide"]["blast_radius"] <= canary["wide"]["governance"]["ship_max_blast_radius"]:
        _fail("wide regression fixture must exceed ship blast-radius guardrail")

    print("CodGate Track 02 preflight · PASS")
    print("loss class COD_RTO · verifier/control layer")
    print("REAL evidence · 138 terminal · holdout 28 / RTO 8")
    print("REAL heldout · Precision 23.08% · Recall 37.50% · ROC-AUC 0.4313")
    print("REAL candidate verdict · BLOCK_RELEASE")
    print("synthetic n=80 policy set · regression fixture only · SHA exact")
    print("canonical ALLOW 0 · FORCE 145 · STOP R1")
    print("Risk Repair canonical 145 → 97 · STRUCTURAL_RISK")
    print("Risk Canary regression fixtures · good SHIP · wide SHADOW · bad BLOCK_RELEASE")
    print(f"Real source SHA {real['provenance']['zip_sha256']}")


if __name__ == "__main__":
    main()
