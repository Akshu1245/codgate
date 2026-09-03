"""Razorpay-internal verifier for releases that affect COD RTO decisions.

One loss class only: COD Return-to-Origin (RTO). Canary does not contain a
second detector and never executes a money action. It compares precomputed
CURRENT vs CANDIDATE decisions against a sealed outcome window, quantifies
errors and rupee cost, checks statistical/slice evidence, then returns SHIP,
SHADOW or BLOCK_RELEASE.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections import defaultdict
from statistics import mean, stdev
from typing import Callable, Iterable

from .score import FALSE_BLOCK_INR, MISSED_RTO_INR

FORCE = "FORCE_PREPAID"
ALLOW = "ALLOW_COD"
VALID_DECISIONS = {ALLOW, FORCE}
LOSS_CLASS = "COD_RTO"
GOVERNANCE_VERSION = "rto-release-verifier-v2"

# Release-safety thresholds. These govern a candidate release, not an order.
MIN_SHIP_ROWS = 100
MIN_SHIP_POSITIVES = 20
MIN_SHIP_NEGATIVES = 50
MIN_SEGMENT_NEGATIVES = 20
MAX_SHIP_BLAST_RADIUS = 0.15
MAX_SHIP_SEGMENT_FP_DELTA = 0.02
BLOCK_SEGMENT_FP_DELTA = 0.05
BLOCK_RECALL_DROP = 0.02
Z95 = 1.959963984540054


def _parse_actual(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    if isinstance(value, str) and value.strip().lower() in {"0", "1", "true", "false"}:
        return value.strip().lower() in {"1", "true"}
    raise ValueError("actual_rto must be a boolean or 0/1")


def _normalise_row(row: dict) -> dict:
    current = str(row.get("current_decision") or "").upper()
    candidate = str(row.get("candidate_decision") or "").upper()
    if current not in VALID_DECISIONS or candidate not in VALID_DECISIONS:
        raise ValueError("current_decision and candidate_decision must be ALLOW_COD or FORCE_PREPAID")
    try:
        amount = float(row.get("amount") or 0)
    except (TypeError, ValueError) as exc:
        raise ValueError("amount must be numeric") from exc
    if not math.isfinite(amount) or amount < 0:
        raise ValueError("amount must be a finite non-negative number")
    return {
        "order_id": str(row.get("order_id") or "").strip(),
        "merchant_segment": str(row.get("merchant_segment") or "unsegmented").strip() or "unsegmented",
        "amount": amount,
        "actual_rto": _parse_actual(row.get("actual_rto", False)),
        "current_decision": current,
        "candidate_decision": candidate,
    }


def _wilson(successes: int, total: int) -> list[float] | None:
    if total <= 0:
        return None
    p = successes / total
    z2 = Z95 * Z95
    denominator = 1 + z2 / total
    centre = (p + z2 / (2 * total)) / denominator
    margin = Z95 * math.sqrt((p * (1 - p) + z2 / (4 * total)) / total) / denominator
    return [max(0.0, centre - margin), min(1.0, centre + margin)]


def _cost_for(predicted_force: bool, actual_rto: bool) -> int:
    if predicted_force and not actual_rto:
        return FALSE_BLOCK_INR
    if not predicted_force and actual_rto:
        return MISSED_RTO_INR
    return 0


def _metrics_for(rows: Iterable[dict], predictor: Callable[[dict], bool]) -> dict:
    tp = fp = fn = tn = 0
    blocked_gmv = false_block_gmv = missed_rto_gmv = 0.0
    items = list(rows)
    for row in items:
        predicted = predictor(row)
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

    predicted_positive = tp + fp
    actual_positive = tp + fn
    actual_negative = fp + tn
    precision = None if predicted_positive == 0 else tp / predicted_positive
    recall = None if actual_positive == 0 else tp / actual_positive
    fpr = None if actual_negative == 0 else fp / actual_negative
    fnr = None if actual_positive == 0 else fn / actual_positive
    total_cost = fp * FALSE_BLOCK_INR + fn * MISSED_RTO_INR
    return {
        "rows": len(items),
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "tn": tn,
        "precision": precision,
        "precision_ci95": _wilson(tp, predicted_positive),
        "recall": recall,
        "recall_ci95": _wilson(tp, actual_positive),
        "false_positive_rate": fpr,
        "false_positive_rate_ci95": _wilson(fp, actual_negative),
        "false_negative_rate": fnr,
        "prevalence": None if not items else actual_positive / len(items),
        "predicted_positive": predicted_positive,
        "actual_positive": actual_positive,
        "actual_negative": actual_negative,
        "false_block_inr": fp * FALSE_BLOCK_INR,
        "missed_rto_inr": fn * MISSED_RTO_INR,
        "modeled_loss_inr": total_cost,
        "modeled_loss_per_row_inr": 0.0 if not items else total_cost / len(items),
        "blocked_gmv_inr": round(blocked_gmv, 2),
        "false_block_gmv_inr": round(false_block_gmv, 2),
        "missed_rto_gmv_inr": round(missed_rto_gmv, 2),
    }


def _decision_metrics(rows: Iterable[dict], field: str) -> dict:
    return _metrics_for(rows, lambda row: row[field] == FORCE)


def _dataset_sha(rows: list[dict]) -> str:
    payload = json.dumps(rows, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _paired_cost_delta_ci(rows: list[dict]) -> list[float]:
    """Normal 95% CI for paired total-cost delta on the same replay window."""
    deltas = []
    for row in rows:
        current_cost = _cost_for(row["current_decision"] == FORCE, row["actual_rto"])
        candidate_cost = _cost_for(row["candidate_decision"] == FORCE, row["actual_rto"])
        deltas.append(candidate_cost - current_cost)
    if len(deltas) <= 1:
        total = float(sum(deltas))
        return [total, total]
    avg = mean(deltas)
    se = stdev(deltas) / math.sqrt(len(deltas))
    return [(avg - Z95 * se) * len(deltas), (avg + Z95 * se) * len(deltas)]


def _segment_report(rows: list[dict]) -> tuple[list[dict], float, list[str]]:
    grouped: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        grouped[row["merchant_segment"]].append(row)

    report: list[dict] = []
    max_eligible_fp_delta = 0.0
    insufficient_segments: list[str] = []
    for segment in sorted(grouped):
        items = grouped[segment]
        current = _decision_metrics(items, "current_decision")
        candidate = _decision_metrics(items, "candidate_decision")
        delivered = current["actual_negative"]
        current_rate = current["false_positive_rate"] or 0.0
        candidate_rate = candidate["false_positive_rate"] or 0.0
        delta = candidate_rate - current_rate
        eligible = delivered >= MIN_SEGMENT_NEGATIVES
        if eligible:
            max_eligible_fp_delta = max(max_eligible_fp_delta, delta)
        else:
            insufficient_segments.append(segment)
        changed = sum(1 for row in items if row["current_decision"] != row["candidate_decision"])
        report.append(
            {
                "segment": segment,
                "rows": len(items),
                "actual_rto": current["actual_positive"],
                "delivered": delivered,
                "changed": changed,
                "change_rate": changed / len(items),
                "current_false_block_rate": current["false_positive_rate"],
                "candidate_false_block_rate": candidate["false_positive_rate"],
                "false_block_rate_delta": delta,
                "candidate_recall": candidate["recall"],
                "candidate_precision": candidate["precision"],
                "candidate_modeled_loss_inr": candidate["modeled_loss_inr"],
                "guardrail_evidence_sufficient": eligible,
            }
        )
    return report, max_eligible_fp_delta, insufficient_segments


def _release_receipt(release_id: str, dataset_sha256: str, verdict: str, summary: dict) -> str:
    payload = json.dumps(
        {
            "governance_version": GOVERNANCE_VERSION,
            "loss_class": LOSS_CLASS,
            "release_id": release_id,
            "dataset_sha256": dataset_sha256,
            "verdict": verdict,
            "summary": summary,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "cgrl_" + hashlib.sha256(payload).hexdigest()[:24]


def evaluate_release(release_id: str, raw_rows: Iterable[dict]) -> dict:
    """Verify a candidate RTO release on paired, observed outcomes.

    The upstream risk system supplies CURRENT and CANDIDATE decisions. This
    verifier measures the one claimed loss class (COD RTO), prices both error
    directions, checks slice and blast-radius safety, and fails closed to
    SHADOW when the evidence window is too small or an improvement is uncertain.
    """
    release_id = str(release_id or "").strip()
    if not release_id:
        raise ValueError("release_id is required")
    rows = [_normalise_row(row) for row in raw_rows]
    if not rows:
        raise ValueError("release window must contain at least one row")
    if any(not row["order_id"] for row in rows):
        raise ValueError("every release row requires order_id")
    order_ids = [row["order_id"] for row in rows]
    if len(order_ids) != len(set(order_ids)):
        raise ValueError("release window contains duplicate order_id values")

    dataset_sha256 = _dataset_sha(rows)
    current = _decision_metrics(rows, "current_decision")
    candidate = _decision_metrics(rows, "candidate_decision")
    baselines = {
        "always_allow": _metrics_for(rows, lambda _row: False),
        "always_force_prepaid": _metrics_for(rows, lambda _row: True),
    }
    changed_rows = [row for row in rows if row["current_decision"] != row["candidate_decision"]]
    blast_radius = len(changed_rows) / len(rows)
    allow_to_force = sum(1 for row in changed_rows if row["current_decision"] == ALLOW)
    force_to_allow = len(changed_rows) - allow_to_force
    segments, max_segment_fp_delta, insufficient_segments = _segment_report(rows)
    cost_delta = candidate["modeled_loss_inr"] - current["modeled_loss_inr"]
    cost_delta_ci95 = _paired_cost_delta_ci(rows)
    recall_delta = (candidate["recall"] or 0.0) - (current["recall"] or 0.0)

    evidence_sufficient = (
        len(rows) >= MIN_SHIP_ROWS
        and current["actual_positive"] >= MIN_SHIP_POSITIVES
        and current["actual_negative"] >= MIN_SHIP_NEGATIVES
    )

    reasons: list[str] = []
    if cost_delta > 0:
        verdict = "BLOCK_RELEASE"
        reasons.append(f"candidate increases modeled loss by ₹{cost_delta}")
    elif recall_delta < -BLOCK_RECALL_DROP:
        verdict = "BLOCK_RELEASE"
        reasons.append(f"candidate recall drops by {abs(recall_delta):.1%}; exceeds 2% release limit")
    elif max_segment_fp_delta > BLOCK_SEGMENT_FP_DELTA:
        verdict = "BLOCK_RELEASE"
        reasons.append("an evidence-qualified merchant segment false-block rate worsens by more than 5 percentage points")
    elif not evidence_sufficient:
        verdict = "SHADOW"
        reasons.append("held-out window is too small to authorize a production ship; collect more shadow outcomes")
    elif cost_delta < 0 and cost_delta_ci95[1] > 0:
        verdict = "SHADOW"
        reasons.append("candidate looks cheaper, but the paired 95% cost-delta interval still crosses zero")
    elif blast_radius > MAX_SHIP_BLAST_RADIUS:
        verdict = "SHADOW"
        reasons.append(f"candidate changes {blast_radius:.1%} of decisions; exceeds 15% ship blast radius")
    elif max_segment_fp_delta > MAX_SHIP_SEGMENT_FP_DELTA:
        verdict = "SHADOW"
        reasons.append("an evidence-qualified merchant segment false-block rate worsens by more than 2 percentage points")
    else:
        verdict = "SHIP"
        reasons.append("candidate clears cost, recall, evidence, blast-radius and merchant-slice guardrails")

    if insufficient_segments:
        reasons.append(
            "slice warning: insufficient delivered examples for release guardrail in "
            + ", ".join(insufficient_segments)
        )

    changed_examples = []
    for row in changed_rows[:8]:
        current_cost = _cost_for(row["current_decision"] == FORCE, row["actual_rto"])
        candidate_cost = _cost_for(row["candidate_decision"] == FORCE, row["actual_rto"])
        changed_examples.append(
            {
                "order_id": row["order_id"],
                "segment": row["merchant_segment"],
                "amount": row["amount"],
                "actual_rto": row["actual_rto"],
                "current": row["current_decision"],
                "candidate": row["candidate_decision"],
                "modeled_cost_delta_inr": candidate_cost - current_cost,
            }
        )

    summary_for_receipt = {
        "rows": len(rows),
        "current_loss": current["modeled_loss_inr"],
        "candidate_loss": candidate["modeled_loss_inr"],
        "cost_delta": cost_delta,
        "blast_radius": blast_radius,
        "candidate_precision": candidate["precision"],
        "candidate_recall": candidate["recall"],
        "max_segment_fp_delta": max_segment_fp_delta,
    }

    trivial_best = min(
        baselines["always_allow"]["modeled_loss_inr"],
        baselines["always_force_prepaid"]["modeled_loss_inr"],
    )
    return {
        "release_id": release_id,
        "loss_class": LOSS_CLASS,
        "governance_version": GOVERNANCE_VERSION,
        "verdict": verdict,
        "release_receipt": _release_receipt(release_id, dataset_sha256, verdict, summary_for_receipt),
        "dataset_sha256": dataset_sha256,
        "rows": len(rows),
        "changed_decisions": len(changed_rows),
        "blast_radius": blast_radius,
        "decision_flips": {"allow_to_force": allow_to_force, "force_to_allow": force_to_allow},
        "evidence": {
            "sufficient_for_ship": evidence_sufficient,
            "minimum_rows": MIN_SHIP_ROWS,
            "minimum_rto": MIN_SHIP_POSITIVES,
            "minimum_delivered": MIN_SHIP_NEGATIVES,
            "observed_rto": current["actual_positive"],
            "observed_delivered": current["actual_negative"],
            "paired_cost_delta_ci95_inr": [round(cost_delta_ci95[0], 2), round(cost_delta_ci95[1], 2)],
            "segment_minimum_delivered": MIN_SEGMENT_NEGATIVES,
        },
        "current": current,
        "candidate": candidate,
        "baselines": baselines,
        "benchmark": {
            "candidate_beats_current_on_modeled_loss": candidate["modeled_loss_inr"] < current["modeled_loss_inr"],
            "candidate_beats_best_trivial_cost_baseline": candidate["modeled_loss_inr"] < trivial_best,
            "best_trivial_modeled_loss_inr": trivial_best,
        },
        "delta": {
            "precision": None if current["precision"] is None or candidate["precision"] is None else candidate["precision"] - current["precision"],
            "recall": recall_delta,
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
            "defense_only": True,
            "ship_max_blast_radius": MAX_SHIP_BLAST_RADIUS,
            "ship_max_segment_fp_delta": MAX_SHIP_SEGMENT_FP_DELTA,
            "block_segment_fp_delta": BLOCK_SEGMENT_FP_DELTA,
            "block_recall_drop": BLOCK_RECALL_DROP,
            "cost_rule": "any increase in modeled ₹ loss blocks release",
            "uncertainty_rule": "an apparent cost improvement whose paired 95% interval crosses zero stays in SHADOW",
        },
        "note": "Verifier consumes precomputed current/candidate outputs and observed COD-RTO labels. It does not contain or replace Razorpay's upstream RTO model.",
    }


def _base_demo_rows() -> list[dict]:
    """200-row sealed-style fixture with balanced error opportunities by segment."""
    segments = ["enterprise", "marketplace", "smb", "startup"]
    rows: list[dict] = []
    positive_seen = defaultdict(int)
    negative_seen = defaultdict(int)
    for index in range(200):
        segment = segments[index // 50]
        actual_rto = index % 4 == 0  # 50 RTO, 150 delivered.
        if actual_rto:
            positive_seen[segment] += 1
            # Exactly five misses per segment => 20 FN overall.
            current = ALLOW if positive_seen[segment] <= 5 else FORCE
        else:
            negative_seen[segment] += 1
            # Exactly five false blocks per segment => 20 FP overall.
            current = FORCE if negative_seen[segment] <= 5 else ALLOW
        rows.append(
            {
                "order_id": f"rzp_canary_{index + 1:03d}",
                "merchant_segment": segment,
                "amount": 499 + ((index * 337) % 4500),
                "actual_rto": actual_rto,
                "current_decision": current,
                "candidate_decision": current,
            }
        )
    return rows


def demo_release(scenario: str) -> dict:
    rows = _base_demo_rows()
    scenario = scenario.lower()
    if scenario == "good":
        # Repair two FNs and two FPs per segment: 16/200 flips, strong paired gain.
        fn_fixed = defaultdict(int)
        fp_fixed = defaultdict(int)
        for row in rows:
            segment = row["merchant_segment"]
            if row["actual_rto"] and row["current_decision"] == ALLOW and fn_fixed[segment] < 2:
                row["candidate_decision"] = FORCE
                fn_fixed[segment] += 1
            elif not row["actual_rto"] and row["current_decision"] == FORCE and fp_fixed[segment] < 2:
                row["candidate_decision"] = ALLOW
                fp_fixed[segment] += 1
        release_id = "rto_policy_2026_09_good"
    elif scenario == "wide":
        # Perfect on this synthetic replay but changes 20% of production decisions.
        for row in rows:
            row["candidate_decision"] = FORCE if row["actual_rto"] else ALLOW
        release_id = "rto_policy_2026_09_wide"
    elif scenario == "bad":
        # Add false blocks and new misses in every segment.
        extra_fp = defaultdict(int)
        extra_fn = defaultdict(int)
        for row in rows:
            segment = row["merchant_segment"]
            if not row["actual_rto"] and row["current_decision"] == ALLOW and extra_fp[segment] < 3:
                row["candidate_decision"] = FORCE
                extra_fp[segment] += 1
            elif row["actual_rto"] and row["current_decision"] == FORCE and extra_fn[segment] < 1:
                row["candidate_decision"] = ALLOW
                extra_fn[segment] += 1
        release_id = "rto_policy_2026_09_bad"
    else:
        raise ValueError("scenario must be good, wide, or bad")
    result = evaluate_release(release_id, rows)
    result["demo_scenario"] = scenario
    result["data_note"] = "Deterministic 200-row synthetic release fixture. Production input is Razorpay-owned paired shadow/replay decisions joined to observed COD-RTO outcomes."
    return result
