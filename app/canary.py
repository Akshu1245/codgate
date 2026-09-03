"""Internal risk-policy release verifier for Razorpay-owned RTO controls.

Canary never invents or executes a second risk policy. It compares precomputed
CURRENT vs CANDIDATE decisions against observed outcomes and decides whether a
candidate risk release should SHIP, remain in SHADOW, or be BLOCKED.
"""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from typing import Iterable

from .score import FALSE_BLOCK_INR, MISSED_RTO_INR

FORCE = "FORCE_PREPAID"
ALLOW = "ALLOW_COD"
VALID_DECISIONS = {ALLOW, FORCE}

# Governance thresholds for the release verifier, not transaction risk weights.
MAX_SHIP_BLAST_RADIUS = 0.15
MAX_SHIP_SEGMENT_FP_DELTA = 0.02
BLOCK_SEGMENT_FP_DELTA = 0.05


def _normalise_row(row: dict) -> dict:
    current = str(row.get("current_decision") or "").upper()
    candidate = str(row.get("candidate_decision") or "").upper()
    if current not in VALID_DECISIONS or candidate not in VALID_DECISIONS:
        raise ValueError("current_decision and candidate_decision must be ALLOW_COD or FORCE_PREPAID")
    return {
        "order_id": str(row.get("order_id") or "").strip(),
        "merchant_segment": str(row.get("merchant_segment") or "unsegmented").strip() or "unsegmented",
        "amount": float(row.get("amount") or 0),
        "actual_rto": bool(int(row.get("actual_rto", 0))) if isinstance(row.get("actual_rto", 0), str) else bool(row.get("actual_rto", False)),
        "current_decision": current,
        "candidate_decision": candidate,
    }


def _decision_metrics(rows: Iterable[dict], field: str) -> dict:
    tp = fp = fn = tn = 0
    blocked_gmv = false_block_gmv = missed_rto_gmv = 0.0
    for row in rows:
        predicted = row[field] == FORCE
        actual = row["actual_rto"]
        amount = row["amount"]
        if predicted:
            blocked_gmv += amount
        if predicted and actual:
            tp += 1
        elif predicted and not actual:
            fp += 1
            false_block_gmv += amount
        elif not predicted and actual:
            fn += 1
            missed_rto_gmv += amount
        else:
            tn += 1
    precision = None if tp + fp == 0 else tp / (tp + fp)
    recall = None if tp + fn == 0 else tp / (tp + fn)
    total_cost = fp * FALSE_BLOCK_INR + fn * MISSED_RTO_INR
    return {
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "tn": tn,
        "precision": precision,
        "recall": recall,
        "false_block_inr": fp * FALSE_BLOCK_INR,
        "missed_rto_inr": fn * MISSED_RTO_INR,
        "modeled_loss_inr": total_cost,
        "blocked_gmv_inr": round(blocked_gmv, 2),
        "false_block_gmv_inr": round(false_block_gmv, 2),
        "missed_rto_gmv_inr": round(missed_rto_gmv, 2),
    }


def _segment_report(rows: list[dict]) -> tuple[list[dict], float]:
    grouped: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        grouped[row["merchant_segment"]].append(row)

    report: list[dict] = []
    max_fp_delta = 0.0
    for segment in sorted(grouped):
        items = grouped[segment]
        delivered = sum(1 for row in items if not row["actual_rto"])
        current_fp = sum(1 for row in items if not row["actual_rto"] and row["current_decision"] == FORCE)
        candidate_fp = sum(1 for row in items if not row["actual_rto"] and row["candidate_decision"] == FORCE)
        current_rate = 0.0 if delivered == 0 else current_fp / delivered
        candidate_rate = 0.0 if delivered == 0 else candidate_fp / delivered
        delta = candidate_rate - current_rate
        max_fp_delta = max(max_fp_delta, delta)
        changed = sum(1 for row in items if row["current_decision"] != row["candidate_decision"])
        report.append(
            {
                "segment": segment,
                "rows": len(items),
                "changed": changed,
                "change_rate": changed / len(items),
                "current_false_block_rate": current_rate,
                "candidate_false_block_rate": candidate_rate,
                "false_block_rate_delta": delta,
            }
        )
    return report, max_fp_delta


def _release_receipt(release_id: str, rows: list[dict], verdict: str) -> str:
    payload = json.dumps(
        {"release_id": release_id, "verdict": verdict, "rows": rows},
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "cgrl_" + hashlib.sha256(payload).hexdigest()[:24]


def evaluate_release(release_id: str, raw_rows: Iterable[dict]) -> dict:
    """Compare a candidate release against current production decisions.

    The caller supplies precomputed decisions from the current and candidate
    systems plus observed RTO outcomes. Canary only verifies the release; it
    does not implement or infer the candidate policy/model.
    """
    rows = [_normalise_row(row) for row in raw_rows]
    if not rows:
        raise ValueError("release window must contain at least one row")
    if any(not row["order_id"] for row in rows):
        raise ValueError("every release row requires order_id")

    current = _decision_metrics(rows, "current_decision")
    candidate = _decision_metrics(rows, "candidate_decision")
    changed_rows = [row for row in rows if row["current_decision"] != row["candidate_decision"]]
    blast_radius = len(changed_rows) / len(rows)
    segments, max_segment_fp_delta = _segment_report(rows)
    cost_delta = candidate["modeled_loss_inr"] - current["modeled_loss_inr"]

    reasons: list[str] = []
    if cost_delta > 0:
        verdict = "BLOCK_RELEASE"
        reasons.append(f"candidate increases modeled loss by ₹{cost_delta}")
    elif max_segment_fp_delta > BLOCK_SEGMENT_FP_DELTA:
        verdict = "BLOCK_RELEASE"
        reasons.append("a merchant segment false-block rate worsens by more than 5 percentage points")
    elif blast_radius > MAX_SHIP_BLAST_RADIUS:
        verdict = "SHADOW"
        reasons.append(f"candidate changes {blast_radius:.1%} of decisions; exceeds 15% ship blast radius")
    elif max_segment_fp_delta > MAX_SHIP_SEGMENT_FP_DELTA:
        verdict = "SHADOW"
        reasons.append("a merchant segment false-block rate worsens by more than 2 percentage points")
    else:
        verdict = "SHIP"
        reasons.append("modeled loss does not increase and blast/segment guardrails stay inside ship limits")

    changed_examples = [
        {
            "order_id": row["order_id"],
            "segment": row["merchant_segment"],
            "amount": row["amount"],
            "actual_rto": row["actual_rto"],
            "current": row["current_decision"],
            "candidate": row["candidate_decision"],
        }
        for row in changed_rows[:8]
    ]

    return {
        "release_id": release_id,
        "verdict": verdict,
        "release_receipt": _release_receipt(release_id, rows, verdict),
        "rows": len(rows),
        "changed_decisions": len(changed_rows),
        "blast_radius": blast_radius,
        "current": current,
        "candidate": candidate,
        "delta": {
            "modeled_loss_inr": cost_delta,
            "false_block_inr": candidate["false_block_inr"] - current["false_block_inr"],
            "missed_rto_inr": candidate["missed_rto_inr"] - current["missed_rto_inr"],
            "blocked_gmv_inr": round(candidate["blocked_gmv_inr"] - current["blocked_gmv_inr"], 2),
            "max_segment_false_block_rate_delta": max_segment_fp_delta,
        },
        "segments": segments,
        "changed_examples": changed_examples,
        "reasons": reasons,
        "governance": {
            "ship_max_blast_radius": MAX_SHIP_BLAST_RADIUS,
            "ship_max_segment_fp_delta": MAX_SHIP_SEGMENT_FP_DELTA,
            "block_segment_fp_delta": BLOCK_SEGMENT_FP_DELTA,
            "cost_rule": "any increase in modeled ₹ loss blocks release",
        },
        "note": "Canary verifies precomputed current/candidate outputs. It does not contain a second RTO model or policy.",
    }


def _base_demo_rows() -> list[dict]:
    # Synthetic Razorpay-internal-style release window used only to demonstrate
    # the verifier. Production input would come from internal shadow/replay data.
    actual = [1, 1, 1, 1, 1, 1, 1, 1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0]
    current = [FORCE, FORCE, ALLOW, FORCE, ALLOW, FORCE, ALLOW, FORCE, FORCE, ALLOW, FORCE, ALLOW, ALLOW, ALLOW, FORCE, ALLOW, ALLOW, ALLOW, ALLOW, ALLOW]
    segments = ["enterprise"] * 5 + ["marketplace"] * 5 + ["smb"] * 5 + ["startup"] * 5
    amounts = [3499, 899, 2199, 4799, 699, 1299, 3299, 999, 1499, 749, 3999, 899, 1299, 599, 2799, 1099, 1899, 499, 999, 2499]
    return [
        {
            "order_id": f"rzp_canary_{index + 1:02d}",
            "merchant_segment": segments[index],
            "amount": amounts[index],
            "actual_rto": actual[index],
            "current_decision": current[index],
            "candidate_decision": current[index],
        }
        for index in range(20)
    ]


def demo_release(scenario: str) -> dict:
    rows = _base_demo_rows()
    scenario = scenario.lower()
    if scenario == "good":
        # Correct two misses and one false block: 15% blast radius, no new harm.
        for idx in (2, 4):
            rows[idx]["candidate_decision"] = FORCE
        rows[10]["candidate_decision"] = ALLOW
        release_id = "rto_policy_2026_09_good"
    elif scenario == "wide":
        # Candidate looks much better on this window but changes too much traffic
        # to ship immediately; Canary demands shadow evidence.
        for row in rows:
            row["candidate_decision"] = FORCE if row["actual_rto"] else ALLOW
        release_id = "rto_policy_2026_09_wide"
    elif scenario == "bad":
        # Introduce new false blocks and a miss; modeled loss goes up.
        for idx in (8, 9, 11, 12):
            rows[idx]["candidate_decision"] = FORCE
        rows[0]["candidate_decision"] = ALLOW
        release_id = "rto_policy_2026_09_bad"
    else:
        raise ValueError("scenario must be good, wide, or bad")
    result = evaluate_release(release_id, rows)
    result["demo_scenario"] = scenario
    result["data_note"] = "Synthetic release-window fixture. Production would use Razorpay-owned shadow decisions and observed outcomes."
    return result
