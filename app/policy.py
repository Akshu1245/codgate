"""CodGate Policy v1.0. Pure function: no network, no LLM, no I/O.

This deterministic policy is a control/regression fixture. Its location severity
bands are not empirical pincode RTO rates; real model evidence is governed by
the separate evidence/release gates.
"""

from .features import extract_features

POLICY_VERSION = "v1.0"
FORCE_THRESHOLD = 50


def _stop(features: dict, rule: dict) -> dict:
    return {
        "decision": "STOP",
        "points": 0,
        "threshold": FORCE_THRESHOLD,
        "policy_version": POLICY_VERSION,
        "rules": [rule],
        "features": features,
        "payment_link": None,
    }


def decide(order: dict) -> dict:
    """Score one COD order. STOP short-circuits; otherwise points are additive."""
    features = extract_features(order)
    rules: list[dict] = []

    if not features["pincode_valid"]:
        return _stop(
            features,
            {
                "id": "R1",
                "name": "STOP_PINCODE_INVALID",
                "points": 0,
                "kind": "stop",
                "reason": f'Pincode must be 6 digits, got "{features["pincode"] or "∅"}".',
            },
        )

    if features["amount"] <= 0:
        return _stop(
            features,
            {
                "id": "R2",
                "name": "STOP_AMOUNT_INVALID",
                "points": 0,
                "kind": "stop",
                "reason": "Amount must be a positive rupee figure.",
            },
        )

    if features["address_class"] == "empty":
        return _stop(
            features,
            {
                "id": "R3",
                "name": "STOP_ADDRESS_EMPTY",
                "points": 0,
                "kind": "stop",
                "reason": "Address is empty — order does not enter the gate.",
            },
        )

    address_class = features["address_class"]
    if address_class == "landmark_only":
        rules.append({"id": "R4", "name": "LANDMARK_ONLY", "points": 40, "kind": "risk", "reason": "Landmark-only address with no house number."})
    elif address_class == "short":
        rules.append({"id": "R10", "name": "SHORT_ADDRESS", "points": 20, "kind": "risk", "reason": f"Address is {features['address_len']} chars — under 12."})
    elif address_class == "partial":
        rules.append({"id": "R13", "name": "PARTIAL_ADDRESS", "points": 8, "kind": "risk", "reason": "House or locality is incomplete."})
    elif address_class == "complete":
        rules.append({"id": "C4", "name": "COMPLETE_ADDRESS", "points": -8, "kind": "credit", "reason": "House number and street/locality are both present."})

    # Legacy band IDs remain frozen so old decision fixtures stay reproducible.
    # They are deterministic policy severity groups, not measured pincode RTO rates.
    pin_band = features["pin_band"]
    if pin_band == "high":
        rules.append({"id": "R5", "name": "HIGH_LOCATION_POLICY", "points": 25, "kind": "risk", "reason": "Pincode is in the frozen high-severity control band; no empirical pincode RTO rate is claimed."})
    elif pin_band == "mid":
        rules.append({"id": "R11", "name": "MID_LOCATION_POLICY", "points": 10, "kind": "risk", "reason": "Pincode is in the frozen mid-severity control band; no empirical pincode RTO rate is claimed."})
    elif pin_band == "low":
        rules.append({"id": "C3", "name": "LOW_LOCATION_POLICY", "points": -10, "kind": "credit", "reason": "Pincode is in the frozen low-severity control band; no empirical pincode RTO rate is claimed."})

    if features["is_new_customer"]:
        rules.append({"id": "R6", "name": "NEW_CUSTOMER", "points": 20, "kind": "risk", "reason": "Zero prior orders or account age under 21 days."})
    elif int(order.get("account_age_days") or 0) >= 180:
        rules.append({"id": "C2", "name": "OLD_CUSTOMER", "points": -10, "kind": "credit", "reason": "Account age is at least 180 days."})

    if features["high_ticket"]:
        rules.append({"id": "R7", "name": "HIGH_TICKET", "points": 15, "kind": "risk", "reason": "COD amount is at least ₹3,000."})

    if features["prior_rto_on_phone"]:
        rules.append({"id": "R8", "name": "PRIOR_RTO_PHONE", "points": 35, "kind": "risk", "reason": "This phone has at least one prior RTO."})

    if not features["prepaid_history"]:
        rules.append({"id": "R9", "name": "NO_PREPAID_HISTORY", "points": 10, "kind": "risk", "reason": "No prepaid order history on this phone."})
    elif features["prepaid_veteran"]:
        rules.append({"id": "C1", "name": "PREPAID_VETERAN", "points": -15, "kind": "credit", "reason": "At least three prepaid orders are on file."})

    points = max(0, sum(rule["points"] for rule in rules))
    decision = "FORCE_PREPAID" if points >= FORCE_THRESHOLD else "ALLOW_COD"

    return {
        "decision": decision,
        "points": points,
        "threshold": FORCE_THRESHOLD,
        "policy_version": POLICY_VERSION,
        "rules": rules,
        "features": features,
        "payment_link": None,
    }
