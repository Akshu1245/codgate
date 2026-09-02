"""Counterfactual Risk Repair for CodGate.

Risk Repair never changes Policy v1.0 and never grants an override. It asks a
narrower question: can a customer-correctable input defect be repaired so that
the *same frozen policy* would allow COD?

Historical signals (prior RTO, account age, order count, prepaid history) and
commercial signals (amount) are deliberately immutable here. The only scored
repair we currently prove is address completion. Invalid required fields are
returned as correction requirements rather than guessed values.
"""

from __future__ import annotations

from copy import deepcopy

from .ops import sha256_json
from .policy import FORCE_THRESHOLD, decide

_COMPLETE_ADDRESS_PROBE = "12 MG Road, Indiranagar"


def _rules(result: dict) -> list[str]:
    return [str(rule["id"]) for rule in result.get("rules", [])]


def _certificate(base: dict, best: dict | None, status: str, repair_kind: str | None) -> str:
    material = {
        "policy_version": base["policy_version"],
        "base_decision": base["decision"],
        "base_points": base["points"],
        "base_rules": _rules(base),
        "best_decision": None if best is None else best["decision"],
        "best_points": None if best is None else best["points"],
        "best_rules": [] if best is None else _rules(best),
        "status": status,
        "repair_kind": repair_kind,
    }
    return "cgrr_" + sha256_json(material)[:20]


def _required_field_repair(base: dict, field: str, criterion: str) -> dict:
    status = "NEEDS_CORRECTION"
    return {
        "status": status,
        "repairable": None,
        "policy_version": base["policy_version"],
        "threshold": FORCE_THRESHOLD,
        "base_decision": base["decision"],
        "base_points": base["points"],
        "base_rules": _rules(base),
        "best_decision": None,
        "best_points": None,
        "points_reduced": None,
        "margin_to_cod": None,
        "repair_kind": "required_field",
        "customer_action": f"Correct {field}.",
        "required_fields": [field],
        "criterion": criterion,
        "locked_signals": ["amount", "account_age_days", "prepaid_orders", "prior_rto_count", "orders_count"],
        "proof": "CodGate will not invent a replacement value. Re-score the corrected order through the same frozen policy.",
        "repair_receipt": _certificate(base, None, status, "required_field"),
    }


def analyze_repair(order: dict) -> dict:
    """Return the strongest legitimate correction under the frozen policy.

    This is intentionally conservative. It does not mutate history, pincode risk
    bands, amount, or customer tenure merely to cross the threshold.
    """
    base = decide(order)

    if base["decision"] == "STOP":
        stop_rule = _rules(base)[0] if _rules(base) else ""
        if stop_rule == "R1":
            return _required_field_repair(base, "pincode", "Provide the actual 6-digit shipping pincode.")
        if stop_rule == "R2":
            return _required_field_repair(base, "amount", "Provide the actual positive order amount.")
        return _required_field_repair(base, "address", "Provide the actual delivery address.")

    if base["decision"] == "ALLOW_COD":
        status = "ALREADY_SAFE"
        return {
            "status": status,
            "repairable": False,
            "policy_version": base["policy_version"],
            "threshold": FORCE_THRESHOLD,
            "base_decision": base["decision"],
            "base_points": base["points"],
            "base_rules": _rules(base),
            "best_decision": base["decision"],
            "best_points": base["points"],
            "points_reduced": 0,
            "margin_to_cod": max(0, base["points"] - (FORCE_THRESHOLD - 1)),
            "repair_kind": None,
            "customer_action": None,
            "required_fields": [],
            "criterion": None,
            "locked_signals": ["pincode", "amount", "account_age_days", "prepaid_orders", "prior_rto_count", "orders_count"],
            "proof": "No repair is needed; Policy v1.0 already allows COD.",
            "repair_receipt": _certificate(base, base, status, None),
        }

    # The only scored counterfactual we allow today is turning an incomplete or
    # landmark-only address into a genuinely complete address. The probe string
    # is never returned as customer data; it is only a deterministic way to ask
    # extract_features() for the 'complete' address class.
    current_class = str(base.get("features", {}).get("address_class") or "")
    if current_class in {"landmark_only", "short", "partial"}:
        candidate_order = deepcopy(order)
        candidate_order["address"] = _COMPLETE_ADDRESS_PROBE
        best = decide(candidate_order)
        reduced = max(0, int(base["points"]) - int(best["points"]))
        repairable = best["decision"] == "ALLOW_COD"
        status = "REPAIRABLE" if repairable else "STRUCTURAL_RISK"
        if repairable:
            action = "Provide a complete delivery address with a house/flat number plus street or locality, then re-score before checkout."
            proof = (
                f"Under the same Policy v1.0, address completion changes the score from {base['points']} to {best['points']}; "
                f"that is below the {FORCE_THRESHOLD}-point FORCE_PREPAID threshold."
            )
        else:
            action = None
            proof = (
                f"Even after the strongest legitimate address correction, the score only falls from {base['points']} to {best['points']}; "
                f"it remains at or above {FORCE_THRESHOLD}. Historical/location risk must not be edited away."
            )
        return {
            "status": status,
            "repairable": repairable,
            "policy_version": base["policy_version"],
            "threshold": FORCE_THRESHOLD,
            "base_decision": base["decision"],
            "base_points": base["points"],
            "base_rules": _rules(base),
            "best_decision": best["decision"],
            "best_points": best["points"],
            "best_rules": _rules(best),
            "points_reduced": reduced,
            "margin_to_cod": max(0, int(best["points"]) - (FORCE_THRESHOLD - 1)),
            "repair_kind": "complete_address",
            "customer_action": action,
            "required_fields": ["address"] if repairable else [],
            "criterion": "House/flat number plus street or locality.",
            "locked_signals": ["pincode", "amount", "account_age_days", "prepaid_orders", "prior_rto_count", "orders_count"],
            "proof": proof,
            "repair_receipt": _certificate(base, best, status, "complete_address"),
        }

    status = "STRUCTURAL_RISK"
    return {
        "status": status,
        "repairable": False,
        "policy_version": base["policy_version"],
        "threshold": FORCE_THRESHOLD,
        "base_decision": base["decision"],
        "base_points": base["points"],
        "base_rules": _rules(base),
        "best_decision": base["decision"],
        "best_points": base["points"],
        "best_rules": _rules(base),
        "points_reduced": 0,
        "margin_to_cod": max(0, int(base["points"]) - (FORCE_THRESHOLD - 1)),
        "repair_kind": None,
        "customer_action": None,
        "required_fields": [],
        "criterion": None,
        "locked_signals": ["pincode", "amount", "account_age_days", "prepaid_orders", "prior_rto_count", "orders_count"],
        "proof": "No customer-correctable input defect can legitimately move this order below the FORCE_PREPAID threshold.",
        "repair_receipt": _certificate(base, base, status, None),
    }
