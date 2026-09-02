"""Named-rule COD gate. Pure function — no network, no LLM, no I/O."""

from .features import extract_features

POLICY_VERSION = "v1.0"
FORCE_THRESHOLD = 50


def _stop(order: dict, features: dict, rule: dict) -> dict:
    return {
        "decision": "STOP",
        "points": 0,
        "threshold": FORCE_THRESHOLD,
        "policy_version": POLICY_VERSION,
        "rules": [rule],
        "features": features,
        "payment_link": None,
        "payment_link_note": None,
        "order": order,
    }


def decide(order: dict) -> dict:
    features = extract_features(order)
    rules = []

    if not features["pincode_valid"]:
        return _stop(
            order,
            features,
            {
                "id": "R1",
                "name": "STOP_PINCODE_INVALID",
                "points": 0,
                "kind": "stop",
                "reason": f'Pincode must be 6 digits, got "{features["pincode"] or "∅"}".',
            },
        )

    if not (features["amount"] > 0):
        return _stop(
            order,
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
            order,
            features,
            {
                "id": "R3",
                "name": "STOP_ADDRESS_EMPTY",
                "points": 0,
                "kind": "stop",
                "reason": "Address is empty — order does not enter the gate.",
            },
        )

    if features["address_class"] == "landmark_only":
        rules.append(
            {
                "id": "R4",
                "name": "LANDMARK_ONLY",
                "points": 40,
                "kind": "risk",
                "reason": "Address is a landmark pin (temple / mandir / mosque) with no house number.",
            }
        )
    elif features["address_class"] == "short":
        rules.append(
            {
                "id": "R10",
                "name": "SHORT_ADDRESS",
                "points": 20,
                "kind": "risk",
                "reason": f"Address is {features['address_len']} chars — too thin to deliver.",
            }
        )
    elif features["address_class"] == "partial":
        rules.append(
            {
                "id": "R13",
                "name": "PARTIAL_ADDRESS",
                "points": 8,
                "kind": "risk",
                "reason": "Address has a house or locality, not both.",
            }
        )
    else:
        rules.append(
            {
                "id": "C4",
                "name": "COMPLETE_ADDRESS",
                "points": -8,
                "kind": "credit",
                "reason": "House number and street/locality both present.",
            }
        )

    if features["pin_band"] == "high":
        city = f" ({features['pin_city']})" if features["pin_city"] else ""
        rate = (features["pin_rto_rate"] or 0) * 100
        rules.append(
            {
                "id": "R5",
                "name": "HIGH_RTO_PIN",
                "points": 25,
                "kind": "risk",
                "reason": f"Pincode {features['pincode']}{city} RTO {rate:.0f}%.",
            }
        )
    elif features["pin_band"] == "mid":
        rate = (features["pin_rto_rate"] or 0) * 100
        rules.append(
            {
                "id": "R11",
                "name": "MID_RTO_PIN",
                "points": 10,
                "kind": "risk",
                "reason": f"Pincode {features['pincode']} sits in the mid RTO band ({rate:.0f}%).",
            }
        )
    elif features["pin_band"] == "low":
        city = f" ({features['pin_city']})" if features["pin_city"] else ""
        rules.append(
            {
                "id": "C3",
                "name": "LOW_RTO_PIN",
                "points": -10,
                "kind": "credit",
                "reason": f"Pincode {features['pincode']}{city} is a low-RTO pin.",
            }
        )

    if features["is_new_customer"]:
        rules.append(
            {
                "id": "R6",
                "name": "NEW_CUSTOMER",
                "points": 20,
                "kind": "risk",
                "reason": "New on this phone — under 21 days or zero prior orders.",
            }
        )
    elif int(order.get("account_age_days") or 0) >= 180:
        rules.append(
            {
                "id": "C2",
                "name": "OLD_CUSTOMER",
                "points": -10,
                "kind": "credit",
                "reason": f"Account age {order.get('account_age_days')} days.",
            }
        )

    if features["high_ticket"]:
        rules.append(
            {
                "id": "R7",
                "name": "HIGH_TICKET",
                "points": 15,
                "kind": "risk",
                "reason": f"COD of ₹{round(features['amount'])} is above the ₹3,000 ticket cut.",
            }
        )

    if features["prior_rto_on_phone"]:
        n = int(order.get("prior_rto_count") or 0)
        rules.append(
            {
                "id": "R8",
                "name": "PRIOR_RTO_PHONE",
                "points": 35,
                "kind": "risk",
                "reason": f"This phone already has {n} RTO{'s' if n != 1 else ''}.",
            }
        )

    if not features["prepaid_history"]:
        rules.append(
            {
                "id": "R9",
                "name": "NO_PREPAID_HISTORY",
                "points": 10,
                "kind": "risk",
                "reason": "No prepaid orders on this phone.",
            }
        )
    elif features["prepaid_veteran"]:
        rules.append(
            {
                "id": "C1",
                "name": "PREPAID_VETERAN",
                "points": -15,
                "kind": "credit",
                "reason": f"{order.get('prepaid_orders')} prepaid orders on file.",
            }
        )

    points = max(0, sum(r["points"] for r in rules))
    fired = [r for r in rules if r["points"] != 0]
    decision = "FORCE_PREPAID" if points >= FORCE_THRESHOLD else "ALLOW_COD"
    return {
        "decision": decision,
        "points": points,
        "threshold": FORCE_THRESHOLD,
        "policy_version": POLICY_VERSION,
        "rules": fired,
        "features": features,
        "payment_link": None,
        "payment_link_note": None,
        "order": order,
    }
