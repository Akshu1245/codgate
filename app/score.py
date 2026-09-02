"""python -m app.score — Precision / Recall / false-block ₹180 / missed-RTO ₹250."""

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
    with path.open(encoding="utf-8") as f:
        return list(csv.DictReader(f))


def evaluate(rows: list[dict] | None = None) -> dict:
    rows = rows if rows is not None else load_rows()
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
    raw = CSV_PATH.read_bytes()
    sha = hashlib.sha256(raw).hexdigest()
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
    }


def main() -> None:
    r = evaluate()
    print(f"CodGate v1.0 · n={r['n']} scored={r['scored']}")
    print(f"Precision {r['precision'] * 100:.1f}%")
    print(f"Recall    {r['recall'] * 100:.1f}%")
    print(
        f"false-block ₹{r['false_block_inr']} (₹{FALSE_BLOCK_INR} × FP {r['fp']})"
    )
    print(
        f"missed-RTO  ₹{r['missed_rto_inr']} (₹{MISSED_RTO_INR} × FN {r['fn']})"
    )
    print(f"SHA-256 {r['sha256']}")
    if r["sha256"] != HELDOUT_SHA256:
        print("WARN: held-out hash drifted — labels were edited.")


if __name__ == "__main__":
    main()
