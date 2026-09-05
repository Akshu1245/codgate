"""Statistical evidence gate for externally evaluated RTO candidates.

This module does not predict an order and never executes a payment action. It
checks whether a frozen held-out evidence report is strong enough to let a
candidate proceed to the paired release canary. A candidate that is demonstrably
worse than trivial/random baselines is blocked even when the sample is small.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

DATA = Path(__file__).resolve().parents[1] / "data" / "real_rto_evidence.json"
MIN_ROWS_FOR_PRODUCTION_EVIDENCE = 100
MIN_POSITIVES_FOR_PRODUCTION_EVIDENCE = 20
RANDOM_RANKING_AUC = 0.5


def load_real_evidence() -> dict:
    return json.loads(DATA.read_text(encoding="utf-8"))


def _receipt(report: dict, verdict: str, reasons: list[str]) -> str:
    payload = json.dumps(
        {
            "source_sha256": report["source"]["zip_sha256"],
            "test": report["heldout_test"],
            "verdict": verdict,
            "reasons": reasons,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "cgre_" + hashlib.sha256(payload).hexdigest()[:24]


def evaluate_evidence_report(report: dict) -> dict:
    """Return BLOCK_RELEASE or SHADOW from standalone held-out evidence.

    Standalone evidence can never produce SHIP because production release also
    requires the paired current-vs-candidate replay implemented by app.canary.
    """
    source = report.get("source") or {}
    test = report.get("heldout_test") or {}
    dataset = report.get("dataset") or {}

    required_source = {
        "dataset_slug",
        "dataset_page",
        "zip_sha256",
        "csv_member",
    }
    missing_source = sorted(required_source - set(source))
    if missing_source:
        raise ValueError("evidence provenance incomplete: " + ", ".join(missing_source))
    sha = str(source.get("zip_sha256") or "")
    if len(sha) != 64 or any(ch not in "0123456789abcdef" for ch in sha.lower()):
        raise ValueError("source zip_sha256 must be a 64-character hexadecimal digest")

    required_metrics = {"n", "positives", "precision", "recall", "roc_auc", "tp", "fp", "fn", "tn"}
    missing_metrics = sorted(required_metrics - set(test))
    if missing_metrics:
        raise ValueError("held-out metrics incomplete: " + ", ".join(missing_metrics))

    n = int(test["n"])
    positives = int(test["positives"])
    negatives = n - positives
    tp, fp, fn, tn = (int(test[key]) for key in ("tp", "fp", "fn", "tn"))
    if min(n, positives, negatives, tp, fp, fn, tn) < 0:
        raise ValueError("held-out counts cannot be negative")
    if tp + fp + fn + tn != n:
        raise ValueError("held-out confusion matrix does not sum to n")
    if tp + fn != positives:
        raise ValueError("held-out positives disagree with TP + FN")

    predicted_positive = tp + fp
    precision_from_counts = None if predicted_positive == 0 else tp / predicted_positive
    recall_from_counts = None if positives == 0 else tp / positives
    if precision_from_counts is None or abs(float(test["precision"]) - precision_from_counts) > 1e-12:
        raise ValueError("held-out precision does not match confusion matrix")
    if recall_from_counts is None or abs(float(test["recall"]) - recall_from_counts) > 1e-12:
        raise ValueError("held-out recall does not match confusion matrix")

    holdout_prevalence = positives / n if n else 0.0
    reasons: list[str] = []
    hard_failures: list[str] = []

    roc_auc = float(test["roc_auc"])
    precision = float(test["precision"])
    if roc_auc < RANDOM_RANKING_AUC:
        hard_failures.append(
            f"candidate ROC-AUC {roc_auc:.3f} is below the 0.500 random-ranking baseline"
        )
    if predicted_positive > 0 and precision < holdout_prevalence:
        hard_failures.append(
            f"flagged-order RTO rate {precision:.1%} is below hold-out prevalence {holdout_prevalence:.1%}"
        )

    evidence_sufficient = n >= MIN_ROWS_FOR_PRODUCTION_EVIDENCE and positives >= MIN_POSITIVES_FOR_PRODUCTION_EVIDENCE
    if hard_failures:
        verdict = "BLOCK_RELEASE"
        reasons.extend(hard_failures)
    else:
        verdict = "SHADOW"
        if not evidence_sufficient:
            reasons.append(
                "standalone held-out window is too small for production authorization; collect more observed outcomes"
            )
        else:
            reasons.append(
                "standalone metrics clear trivial baselines, but SHIP requires paired current-vs-candidate replay"
            )

    if report.get("limitations"):
        reasons.append("limitations are attached to the evidence record and must travel with any review")

    return {
        "verdict": verdict,
        "loss_class": report.get("loss_class", "Return to Origin (RTO)"),
        "claim": report.get("claim"),
        "release_receipt": _receipt(report, verdict, reasons),
        "provenance": source,
        "dataset": dataset,
        "split": report.get("split"),
        "heldout_test": test,
        "heldout_ci95_bootstrap": report.get("heldout_ci95_bootstrap"),
        "evidence_sufficient_for_production": evidence_sufficient,
        "production_minimums": {
            "heldout_rows": MIN_ROWS_FOR_PRODUCTION_EVIDENCE,
            "rto_positives": MIN_POSITIVES_FOR_PRODUCTION_EVIDENCE,
        },
        "baseline_checks": {
            "random_ranking_auc": RANDOM_RANKING_AUC,
            "holdout_prevalence": holdout_prevalence,
            "candidate_precision": precision,
            "candidate_roc_auc": roc_auc,
        },
        "reasons": reasons,
        "limitations": report.get("limitations") or [],
        "raw_rows_embedded": False,
    }


def real_evidence_status() -> dict:
    return evaluate_evidence_report(load_real_evidence())
