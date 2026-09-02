"""python -m app.score — frozen held-out metrics and SHA drift check."""

from __future__ import annotations

import csv
import hashlib
from pathlib import Path

from .policy import decide

ROOT = Path(__file__).resolve().parents[1]
CSV_PATH = ROOT / "data" / "heldout.csv"
FALSE_BLOCK_INR = 180
MISSED_RTO_INR = 250
HELDOUT_SHA256 = "327f392da4049860f2eca1399b248f78e313a5e6b1694f6a5057d6573fb8e20a"


def load_rows(path: Path = CSV_PATH) -> list[dict]:
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def evaluate(rows: list[dict] | None = None, path: Path = CSV_PATH) -> dict:
    rows = rows if rows is not None else load_rows(path)
    tp = fp = fn = tn = skipped = 0

    for row in rows:
        order = {
            "order_id": row["order_id"],
            "pincode": row["pincode"],
            "address": row["address"],
            "amount": float(row["amount"]),
            "account_age_days": int(row["account_age_days"]),
            "prepaid_orders": int(row["prepaid_orders"]),
            "prior_rto_count": int(row["prior_rto_count"]),
            "orders_count": int(row["orders_count"]),
            "phone": row["phone"],
        }
        result = decide(order)
        if result["decision"] == "STOP":
            skipped += 1
            continue

        predicted = result["decision"] == "FORCE_PREPAID"
        actual = row["actual_rto"].strip() == "1"
        if predicted and actual:
            tp += 1
        elif predicted and not actual:
            fp += 1
        elif not predicted and actual:
            fn += 1
        else:
            tn += 1

    precision = 0 if tp + fp == 0 else tp / (tp + fp)
    recall = 0 if tp + fn == 0 else tp / (tp + fn)
    sha = hashlib.sha256(path.read_bytes()).hexdigest()
    return {
        "n": len(rows),
        "scored": len(rows) - skipped,
        "precision": precision,
        "recall": recall,
        "false_block_inr": fp * FALSE_BLOCK_INR,
        "missed_rto_inr": fn * MISSED_RTO_INR,
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "tn": tn,
        "skipped": skipped,
        "sha256": sha,
        "sha_matches": sha == HELDOUT_SHA256,
    }


def frozen_block(report: dict) -> str:
    return "\n".join(
        [
            f"CodGate v1.0 · n={report['n']} scored={report['scored']}",
            f"Precision {report['precision'] * 100:.1f}%",
            f"Recall    {report['recall'] * 100:.1f}%",
            f"false-block ₹{report['false_block_inr']} (₹{FALSE_BLOCK_INR} × FP {report['fp']})",
            f"missed-RTO  ₹{report['missed_rto_inr']} (₹{MISSED_RTO_INR} × FN {report['fn']})",
            f"TP {report['tp']} · FP {report['fp']} · FN {report['fn']} · TN {report['tn']}",
            f"SHA-256 {report['sha256']}",
        ]
    )


def main() -> None:
    report = evaluate()
    print(frozen_block(report))
    if not report["sha_matches"]:
        print("WARN: held-out hash drifted — labels were edited.")


if __name__ == "__main__":
    main()
