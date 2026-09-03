"""One-command submission preflight.

Run: python -m app.preflight

This is intentionally boring and fail-closed. It checks the exact public claims
that a Track 02 reviewer should be able to reproduce without opening the UI.
"""

from __future__ import annotations

from .canary import demo_release
from .cases import CANONICAL_CASES
from .policy import decide
from .repair import analyze_repair
from .score import evaluate, frozen_block

EXPECTED_FROZEN = """CodGate v1.0 · n=80 scored=80
Precision 74.2%
Recall    60.5%
false-block ₹1440 (₹180 × FP 8)
missed-RTO  ₹3750 (₹250 × FN 15)
TP 23 · FP 8 · FN 15 · TN 34
SHA-256 327f392da4049860f2eca1399b248f78e313a5e6b1694f6a5057d6573fb8e20a"""


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

    frozen = frozen_block(evaluate())
    if frozen != EXPECTED_FROZEN:
        _fail("frozen held-out evidence drifted")

    force_repair = analyze_repair(dict(CANONICAL_CASES[1]["order"]))
    if force_repair["status"] != "NO_SAFE_REPAIR":
        _fail("canonical Siwan order must remain structurally unsafe after address repair")
    if force_repair["best_candidate"]["points"] != 97:
        _fail("canonical Siwan repair proof must remain 145 → 97")

    canary_expected = {"good": "SHIP", "wide": "SHADOW", "bad": "BLOCK_RELEASE"}
    canary = {}
    for scenario, expected in canary_expected.items():
        result = demo_release(scenario)
        canary[scenario] = result
        if result["verdict"] != expected:
            _fail(f"Risk Canary {scenario} expected {expected}, got {result['verdict']}")
        if len(result["dataset_sha256"]) != 64:
            _fail(f"Risk Canary {scenario} missing sealed evidence hash")
    if not canary["good"]["evidence"]["sufficient_for_ship"]:
        _fail("good release fixture must clear minimum evidence gate")
    if canary["good"]["evidence"]["paired_cost_delta_ci95_inr"][1] >= 0:
        _fail("good release fixture must have a 95% paired cost interval below zero")
    if canary["wide"]["blast_radius"] <= canary["wide"]["governance"]["ship_max_blast_radius"]:
        _fail("wide fixture must exceed ship blast-radius guardrail")

    print("CodGate Track 02 preflight · PASS")
    print("loss class COD_RTO · defense-only")
    print("canonical ALLOW 0 · FORCE 145 · STOP R1")
    print("frozen held-out Precision 74.2% · Recall 60.5% · SHA exact")
    print("Risk Repair canonical 145 → 97 · NO SAFE REPAIR")
    print("Risk Canary good SHIP · wide SHADOW · bad BLOCK_RELEASE")
    print(f"Canary good evidence SHA {canary['good']['dataset_sha256']}")


if __name__ == "__main__":
    main()
