"""Build real, leakage-safe return/cancellation-risk evidence from UCI Online Retail.

Source dataset:
  Chen, D. (2015). Online Retail [Dataset]. UCI Machine Learning Repository.
  DOI: 10.24432/C5BW33
  License: CC BY 4.0

The raw UCI log contains positive sale rows and later cancellation rows whose
invoice number starts with C. This script NEVER uses that cancellation marker,
negative quantity, returned value, or any future outcome as a model feature.
Instead, it matches cancellation units back to earlier sales, labels the
ORIGINAL sale invoice, builds only order-time / prior-purchase features, makes a
chronological train/validation/test split, tunes a threshold on validation, and
reports the untouched test metrics.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from collections import defaultdict
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import GradientBoostingClassifier
from sklearn.metrics import (
    accuracy_score,
    average_precision_score,
    balanced_accuracy_score,
    confusion_matrix,
    f1_score,
    precision_score,
    recall_score,
    roc_auc_score,
)

DATASET_NAME = "UCI Online Retail"
DATASET_DOI = "10.24432/C5BW33"
DATASET_URL = "https://archive.ics.uci.edu/dataset/352/online+retail"
DATASET_LICENSE = "CC BY 4.0"
RANDOM_STATE = 20260905
RETURN_HORIZON_DAYS = 60
TOP_COUNTRIES = 12

SERVICE_CODES = {
    "POST",
    "D",
    "M",
    "DOT",
    "BANK CHARGES",
    "AMAZONFEE",
    "CRUK",
    "S",
    "B",
    "PADS",
    "ADJUST",
    "ADJUST2",
    "TEST001",
    "TEST002",
}

FORBIDDEN_FEATURE_TOKENS = {
    "invoice",
    "cancel",
    "return",
    "label",
    "target",
    "returned",
    "outcome",
    "quantity_sign",
}


@dataclass
class MatchAudit:
    sale_rows: int = 0
    cancel_rows: int = 0
    cancel_units: int = 0
    matched_units: int = 0
    unmatched_units: int = 0
    matched_cancel_rows: int = 0


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def normalise_columns(df: pd.DataFrame) -> pd.DataFrame:
    aliases = {
        "Invoice": "InvoiceNo",
        "Price": "UnitPrice",
        "Customer ID": "CustomerID",
    }
    df = df.rename(columns=aliases)
    required = {
        "InvoiceNo",
        "StockCode",
        "Description",
        "Quantity",
        "InvoiceDate",
        "UnitPrice",
        "CustomerID",
        "Country",
    }
    missing = sorted(required - set(df.columns))
    if missing:
        raise ValueError(f"Dataset schema mismatch; missing columns: {missing}")
    return df[list(required)].copy()


def load_raw(path: Path) -> pd.DataFrame:
    df = pd.read_excel(path)
    df = normalise_columns(df)
    df["InvoiceNo"] = df["InvoiceNo"].astype(str).str.strip().str.upper()
    df["StockCode"] = df["StockCode"].astype(str).str.strip().str.upper()
    df["Description"] = df["Description"].astype(str).str.strip()
    df["Country"] = df["Country"].astype(str).str.strip()
    df["Quantity"] = pd.to_numeric(df["Quantity"], errors="coerce")
    df["UnitPrice"] = pd.to_numeric(df["UnitPrice"], errors="coerce")
    df["InvoiceDate"] = pd.to_datetime(df["InvoiceDate"], errors="coerce")
    df["CustomerID"] = pd.to_numeric(df["CustomerID"], errors="coerce")
    return df


def clean_transaction_log(raw: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    audit: dict[str, int] = {"raw_rows": int(len(raw))}
    df = raw.drop_duplicates().copy()
    audit["exact_duplicates_removed"] = int(len(raw) - len(df))

    essential = df[["InvoiceNo", "StockCode", "Quantity", "InvoiceDate", "UnitPrice", "CustomerID"]]
    valid = essential.notna().all(axis=1)
    df = df.loc[valid].copy()
    audit["rows_missing_essential_removed"] = int((~valid).sum())

    service = df["StockCode"].isin(SERVICE_CODES)
    df = df.loc[~service].copy()
    audit["service_adjustment_rows_removed"] = int(service.sum())

    # Only positive-price sale/cancellation lines are economically meaningful here.
    invalid_price = df["UnitPrice"] <= 0
    df = df.loc[~invalid_price].copy()
    audit["nonpositive_price_rows_removed"] = int(invalid_price.sum())

    is_cancel = df["InvoiceNo"].str.startswith("C")
    sale = (~is_cancel) & (df["Quantity"] > 0)
    cancel = is_cancel & (df["Quantity"] < 0)
    usable = sale | cancel
    audit["other_adjustment_rows_removed"] = int((~usable).sum())
    df = df.loc[usable].copy()
    df["is_cancel_outcome"] = df["InvoiceNo"].str.startswith("C")
    audit["usable_rows"] = int(len(df))
    audit["sale_rows"] = int((~df["is_cancel_outcome"]).sum())
    audit["cancellation_rows"] = int(df["is_cancel_outcome"].sum())
    return df, audit


def match_cancellations_to_prior_sales(df: pd.DataFrame) -> tuple[pd.DataFrame, MatchAudit]:
    """LIFO-match returned units to an earlier identical customer/SKU/price sale.

    The match is deliberately conservative: same anonymised customer, SKU and
    rounded unit price, earlier timestamp, and <= RETURN_HORIZON_DAYS apart.
    Unmatched cancellation units are counted and never silently relabelled.
    """

    work = df.sort_values(["InvoiceDate", "InvoiceNo", "StockCode"]).copy()
    sale_rows = work.loc[~work["is_cancel_outcome"]].copy()
    sale_rows["returned_qty"] = 0.0

    returned_qty: dict[int, float] = defaultdict(float)
    # key -> mutable records [row_index, timestamp, remaining_qty]
    pools: dict[tuple[int, str, float], list[list]] = defaultdict(list)
    audit = MatchAudit()

    for row in work.itertuples():
        customer = int(row.CustomerID)
        sku = str(row.StockCode)
        price = round(float(row.UnitPrice), 2)
        key = (customer, sku, price)
        qty = float(row.Quantity)
        ts = row.InvoiceDate

        if not bool(row.is_cancel_outcome):
            audit.sale_rows += 1
            pools[key].append([row.Index, ts, qty])
            continue

        audit.cancel_rows += 1
        needed = int(round(abs(qty)))
        audit.cancel_units += needed
        original_needed = needed
        candidates = pools.get(key, [])

        for rec in reversed(candidates):
            if needed <= 0:
                break
            sale_idx, sale_ts, remaining = rec
            age_days = (ts - sale_ts).total_seconds() / 86400
            if age_days < 0:
                continue
            if age_days > RETURN_HORIZON_DAYS:
                # Older candidates earlier in the list are even older.
                break
            if remaining <= 0:
                continue
            allocated = min(float(needed), float(remaining))
            rec[2] -= allocated
            returned_qty[sale_idx] += allocated
            audit.matched_units += int(round(allocated))
            needed -= int(round(allocated))

        if needed < original_needed:
            audit.matched_cancel_rows += 1
        audit.unmatched_units += needed

    sale_rows["returned_qty"] = [returned_qty.get(idx, 0.0) for idx in sale_rows.index]
    sale_rows["line_value_gbp"] = sale_rows["Quantity"] * sale_rows["UnitPrice"]
    sale_rows["returned_value_gbp"] = sale_rows["returned_qty"] * sale_rows["UnitPrice"]
    return sale_rows, audit


def build_orders(sale_rows: pd.DataFrame) -> pd.DataFrame:
    max_observed = sale_rows["InvoiceDate"].max()
    cutoff = max_observed - timedelta(days=RETURN_HORIZON_DAYS)
    eligible = sale_rows.loc[sale_rows["InvoiceDate"] <= cutoff].copy()

    orders = (
        eligible.groupby("InvoiceNo", sort=False)
        .agg(
            order_date=("InvoiceDate", "min"),
            customer_id=("CustomerID", "first"),
            country=("Country", "first"),
            total_gbp=("line_value_gbp", "sum"),
            units=("Quantity", "sum"),
            line_count=("StockCode", "size"),
            distinct_skus=("StockCode", "nunique"),
            mean_unit_price=("UnitPrice", "mean"),
            max_unit_price=("UnitPrice", "max"),
            returned_value_gbp=("returned_value_gbp", "sum"),
            returned_units=("returned_qty", "sum"),
        )
        .reset_index()
    )
    orders = orders[(orders["total_gbp"] > 0) & (orders["units"] > 0)].copy()
    orders["label_return"] = (orders["returned_units"] > 0).astype(int)
    orders["avg_item_value"] = orders["total_gbp"] / orders["units"].clip(lower=1)
    orders["basket_density"] = orders["units"] / orders["distinct_skus"].clip(lower=1)
    return orders.sort_values(["order_date", "InvoiceNo"]).reset_index(drop=True)


def add_prior_purchase_features(orders: pd.DataFrame) -> pd.DataFrame:
    # No prior RETURN outcome is used here because it may not have been observed
    # yet at the timestamp of the next order. Only purchase history known at order
    # time is allowed.
    state: dict[int, dict] = {}
    rows = []

    for row in orders.itertuples(index=False):
        customer = int(row.customer_id)
        ts = row.order_date
        s = state.get(customer)
        if s is None:
            prior_orders = 0
            prior_spend = 0.0
            tenure_days = 0.0
            days_since_last = -1.0
            avg_prior_order_value = 0.0
        else:
            prior_orders = int(s["orders"])
            prior_spend = float(s["spend"])
            tenure_days = max(0.0, (ts - s["first"]).total_seconds() / 86400)
            days_since_last = max(0.0, (ts - s["last"]).total_seconds() / 86400)
            avg_prior_order_value = prior_spend / max(prior_orders, 1)

        record = row._asdict()
        record.update(
            customer_prior_orders=prior_orders,
            customer_prior_spend=prior_spend,
            customer_tenure_days=tenure_days,
            days_since_last_order=days_since_last,
            avg_prior_order_value=avg_prior_order_value,
            order_hour=int(ts.hour),
            order_day_of_week=int(ts.dayofweek),
            order_month=int(ts.month),
            is_weekend=int(ts.dayofweek >= 5),
        )
        rows.append(record)

        if s is None:
            state[customer] = {
                "orders": 1,
                "spend": float(row.total_gbp),
                "first": ts,
                "last": ts,
            }
        else:
            s["orders"] += 1
            s["spend"] += float(row.total_gbp)
            s["last"] = ts

    return pd.DataFrame(rows)


def chronological_split(orders: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    n = len(orders)
    train_end = int(n * 0.70)
    val_end = int(n * 0.85)
    train = orders.iloc[:train_end].copy()
    val = orders.iloc[train_end:val_end].copy()
    test = orders.iloc[val_end:].copy()
    if min(train["label_return"].sum(), val["label_return"].sum(), test["label_return"].sum()) <= 0:
        raise RuntimeError("At least one split has no positive return labels")
    if not (train["order_date"].max() <= val["order_date"].min() <= test["order_date"].min()):
        raise RuntimeError("Chronological split invariant failed")
    return train, val, test


def build_feature_frames(train: pd.DataFrame, val: pd.DataFrame, test: pd.DataFrame):
    top_countries = train["country"].value_counts().head(TOP_COUNTRIES).index.tolist()

    numeric = [
        "total_gbp",
        "units",
        "line_count",
        "distinct_skus",
        "mean_unit_price",
        "max_unit_price",
        "avg_item_value",
        "basket_density",
        "customer_prior_orders",
        "customer_prior_spend",
        "customer_tenure_days",
        "days_since_last_order",
        "avg_prior_order_value",
        "order_hour",
        "order_day_of_week",
        "order_month",
        "is_weekend",
    ]

    def transform(frame: pd.DataFrame) -> pd.DataFrame:
        out = frame[numeric].astype(float).replace([np.inf, -np.inf], np.nan).fillna(0.0).copy()
        for country in top_countries:
            safe = "".join(ch if ch.isalnum() else "_" for ch in country).strip("_")
            out[f"country__{safe}"] = (frame["country"] == country).astype(float).values
        out["country__OTHER"] = (~frame["country"].isin(top_countries)).astype(float).values
        return out

    x_train, x_val, x_test = transform(train), transform(val), transform(test)
    feature_names = list(x_train.columns)
    for name in feature_names:
        lowered = name.lower()
        if any(token in lowered for token in FORBIDDEN_FEATURE_TOKENS):
            raise RuntimeError(f"Leakage guard rejected feature: {name}")
    if feature_names != list(x_val.columns) or feature_names != list(x_test.columns):
        raise RuntimeError("Feature schema drift across splits")
    return x_train, x_val, x_test, feature_names, top_countries


def select_threshold(y_true: np.ndarray, probabilities: np.ndarray) -> tuple[float, dict]:
    # Threshold is selected on validation only. We optimise F1 and break ties in
    # favour of higher recall, then higher precision.
    candidates = sorted(set(np.linspace(0.02, 0.98, 193).round(4).tolist()))
    best = None
    for threshold in candidates:
        pred = (probabilities >= threshold).astype(int)
        p = precision_score(y_true, pred, zero_division=0)
        r = recall_score(y_true, pred, zero_division=0)
        f1 = f1_score(y_true, pred, zero_division=0)
        key = (f1, r, p, -threshold)
        if best is None or key > best[0]:
            best = (key, threshold, {"precision": p, "recall": r, "f1": f1})
    assert best is not None
    return float(best[1]), best[2]


def safe_auc(y_true: np.ndarray, probabilities: np.ndarray) -> float | None:
    return None if len(np.unique(y_true)) < 2 else float(roc_auc_score(y_true, probabilities))


def evaluate_split(frame: pd.DataFrame, y: np.ndarray, probabilities: np.ndarray, threshold: float) -> dict:
    pred = (probabilities >= threshold).astype(int)
    tn, fp, fn, tp = confusion_matrix(y, pred, labels=[0, 1]).ravel()
    false_positive_mask = (pred == 1) & (y == 0)
    false_negative_mask = (pred == 0) & (y == 1)
    true_positive_mask = (pred == 1) & (y == 1)

    total_return_value = float(frame.loc[y == 1, "returned_value_gbp"].sum())
    captured_return_value = float(frame.loc[true_positive_mask, "returned_value_gbp"].sum())
    false_positive_gmv = float(frame.loc[false_positive_mask, "total_gbp"].sum())
    missed_return_value = float(frame.loc[false_negative_mask, "returned_value_gbp"].sum())

    return {
        "rows": int(len(frame)),
        "positives": int(y.sum()),
        "prevalence": float(y.mean()),
        "threshold": float(threshold),
        "tp": int(tp),
        "fp": int(fp),
        "fn": int(fn),
        "tn": int(tn),
        "precision": float(precision_score(y, pred, zero_division=0)),
        "recall": float(recall_score(y, pred, zero_division=0)),
        "f1": float(f1_score(y, pred, zero_division=0)),
        "accuracy": float(accuracy_score(y, pred)),
        "balanced_accuracy": float(balanced_accuracy_score(y, pred)),
        "average_precision": float(average_precision_score(y, probabilities)),
        "roc_auc": safe_auc(y, probabilities),
        # This is not claimed as net merchant loss. It is the exact value of
        # legitimate orders that a hard-block policy would place at risk.
        "false_positive_cost_upper_bound_gbp": false_positive_gmv,
        "false_positive_cost_definition": "sum(real order value for legitimate test orders incorrectly flagged; hard-block upper bound)",
        "missed_return_value_gbp": missed_return_value,
        "total_return_value_gbp": total_return_value,
        "captured_return_value_gbp": captured_return_value,
        "captured_return_value_rate": 0.0 if total_return_value <= 0 else captured_return_value / total_return_value,
    }


def export_gradient_boosting(model: GradientBoostingClassifier, feature_names: list[str], top_countries: list[str], threshold: float) -> dict:
    prior = float(model.init_.class_prior_[1])
    init_raw = math.log(prior / (1.0 - prior))
    trees = []
    for estimator in model.estimators_[:, 0]:
        tree = estimator.tree_
        trees.append(
            {
                "children_left": tree.children_left.astype(int).tolist(),
                "children_right": tree.children_right.astype(int).tolist(),
                "feature": tree.feature.astype(int).tolist(),
                "threshold": tree.threshold.astype(float).tolist(),
                "value": tree.value[:, 0, 0].astype(float).tolist(),
            }
        )
    return {
        "format": "codgate-gradient-boosting-json-v1",
        "model_type": "sklearn.ensemble.GradientBoostingClassifier",
        "feature_names": feature_names,
        "top_countries": top_countries,
        "decision_threshold": float(threshold),
        "learning_rate": float(model.learning_rate),
        "init_raw_log_odds": init_raw,
        "trees": trees,
    }


def make_holdout_export(test: pd.DataFrame, probabilities: np.ndarray, threshold: float) -> pd.DataFrame:
    export = test[
        [
            "InvoiceNo",
            "order_date",
            "country",
            "total_gbp",
            "units",
            "line_count",
            "distinct_skus",
            "customer_prior_orders",
            "customer_prior_spend",
            "customer_tenure_days",
            "days_since_last_order",
            "avg_prior_order_value",
            "label_return",
            "returned_value_gbp",
        ]
    ].copy()
    export["case_id"] = [
        hashlib.sha256(f"{inv}|{ts.isoformat()}".encode()).hexdigest()[:20]
        for inv, ts in zip(export["InvoiceNo"], export["order_date"])
    ]
    export["probability"] = probabilities
    export["prediction"] = (probabilities >= threshold).astype(int)
    export = export.drop(columns=["InvoiceNo"])
    cols = ["case_id"] + [c for c in export.columns if c != "case_id"]
    return export[cols]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--outdir", default=Path("artifacts/real_return"), type=Path)
    args = parser.parse_args()
    args.outdir.mkdir(parents=True, exist_ok=True)

    raw_sha = sha256_file(args.input)
    raw = load_raw(args.input)
    cleaned, cleaning_audit = clean_transaction_log(raw)
    sale_rows, match_audit = match_cancellations_to_prior_sales(cleaned)
    orders = add_prior_purchase_features(build_orders(sale_rows))
    train, val, test = chronological_split(orders)
    x_train, x_val, x_test, feature_names, top_countries = build_feature_frames(train, val, test)

    y_train = train["label_return"].to_numpy(dtype=int)
    y_val = val["label_return"].to_numpy(dtype=int)
    y_test = test["label_return"].to_numpy(dtype=int)

    positives = max(int(y_train.sum()), 1)
    negatives = max(int(len(y_train) - y_train.sum()), 1)
    positive_weight = negatives / positives
    sample_weight = np.where(y_train == 1, positive_weight, 1.0)

    model = GradientBoostingClassifier(
        n_estimators=160,
        learning_rate=0.045,
        max_depth=3,
        min_samples_leaf=45,
        subsample=0.85,
        random_state=RANDOM_STATE,
    )
    model.fit(x_train, y_train, sample_weight=sample_weight)

    val_prob = model.predict_proba(x_val)[:, 1]
    threshold, validation_selection = select_threshold(y_val, val_prob)
    test_prob = model.predict_proba(x_test)[:, 1]

    validation_metrics = evaluate_split(val, y_val, val_prob, threshold)
    test_metrics = evaluate_split(test, y_test, test_prob, threshold)

    # Naive baseline: flag nothing. Included so high accuracy from class imbalance
    # cannot be mistaken for a useful risk detector.
    baseline_pred = np.zeros_like(y_test)
    baseline = {
        "name": "always_safe",
        "accuracy": float(accuracy_score(y_test, baseline_pred)),
        "precision": 0.0,
        "recall": 0.0,
        "f1": 0.0,
    }

    matched_rate = 0.0 if match_audit.cancel_units == 0 else match_audit.matched_units / match_audit.cancel_units
    report = {
        "evidence_version": "real-return-v1",
        "loss_class": "ORDER_RETURN_OR_CANCELLATION",
        "source": {
            "dataset": DATASET_NAME,
            "doi": DATASET_DOI,
            "url": DATASET_URL,
            "license": DATASET_LICENSE,
            "raw_sha256": raw_sha,
            "raw_rows": int(len(raw)),
        },
        "label_definition": (
            f"Original positive sale invoice labelled 1 when at least one later C-prefixed cancellation line is conservatively matched "
            f"to the same anonymised customer, SKU and unit price within {RETURN_HORIZON_DAYS} days."
        ),
        "leakage_controls": [
            "Invoice cancellation prefix is outcome-only and never a feature.",
            "Negative quantity is outcome-only and never a feature.",
            "Returned quantity/value and current/future return labels are never features.",
            "Only purchase history available before the current order is used.",
            "Train/validation/test are chronological, not random.",
            f"Orders in the final {RETURN_HORIZON_DAYS} days are censored to avoid incomplete outcome windows.",
        ],
        "cleaning": cleaning_audit,
        "matching": {
            **match_audit.__dict__,
            "matched_cancel_unit_rate": matched_rate,
            "return_horizon_days": RETURN_HORIZON_DAYS,
        },
        "orders": {
            "eligible": int(len(orders)),
            "positives": int(orders["label_return"].sum()),
            "prevalence": float(orders["label_return"].mean()),
            "date_min": orders["order_date"].min().isoformat(),
            "date_max": orders["order_date"].max().isoformat(),
        },
        "split": {
            "method": "chronological_70_15_15",
            "train_rows": int(len(train)),
            "validation_rows": int(len(val)),
            "test_rows": int(len(test)),
            "train_end": train["order_date"].max().isoformat(),
            "validation_start": val["order_date"].min().isoformat(),
            "validation_end": val["order_date"].max().isoformat(),
            "test_start": test["order_date"].min().isoformat(),
            "test_end": test["order_date"].max().isoformat(),
        },
        "model": {
            "type": "GradientBoostingClassifier",
            "random_state": RANDOM_STATE,
            "feature_count": len(feature_names),
            "feature_names": feature_names,
            "threshold_selected_on": "validation_f1",
            "threshold": threshold,
            "validation_selection": validation_selection,
        },
        "validation": validation_metrics,
        "test": test_metrics,
        "baseline": baseline,
        "metric_boundary": {
            "precision_recall_are": "measured once on untouched chronological test orders after threshold selection on validation",
            "false_positive_cost_is": "hard-block upper-bound using actual legitimate order value in the source data; not claimed as realised net merchant loss",
            "currency": "GBP, as recorded by the source retailer",
        },
    }

    model_export = export_gradient_boosting(model, feature_names, top_countries, threshold)
    model_export["evidence_version"] = report["evidence_version"]
    model_export["source_raw_sha256"] = raw_sha

    (args.outdir / "metrics.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    (args.outdir / "model.json").write_text(json.dumps(model_export, separators=(",", ":")), encoding="utf-8")
    holdout = make_holdout_export(test, test_prob, threshold)
    holdout.to_csv(args.outdir / "heldout_orders.csv.gz", index=False, compression="gzip")

    print("REAL RETURN EVIDENCE BUILT")
    print(f"source rows: {len(raw):,}  raw sha256: {raw_sha}")
    print(f"eligible orders: {len(orders):,}  positives: {orders['label_return'].sum():,} ({orders['label_return'].mean():.2%})")
    print(f"cancel-unit match coverage: {matched_rate:.2%}")
    print(f"split: train={len(train):,} val={len(val):,} test={len(test):,}")
    print(f"threshold: {threshold:.4f} (selected on validation only)")
    print(
        "TEST "
        f"precision={test_metrics['precision']:.4f} "
        f"recall={test_metrics['recall']:.4f} "
        f"f1={test_metrics['f1']:.4f} "
        f"AP={test_metrics['average_precision']:.4f} "
        f"balanced_accuracy={test_metrics['balanced_accuracy']:.4f}"
    )
    print(
        "TEST confusion "
        f"TP={test_metrics['tp']} FP={test_metrics['fp']} FN={test_metrics['fn']} TN={test_metrics['tn']}"
    )
    print(
        "FP hard-block upper-bound GBP="
        f"{test_metrics['false_positive_cost_upper_bound_gbp']:.2f}; "
        "missed returned value GBP="
        f"{test_metrics['missed_return_value_gbp']:.2f}"
    )


if __name__ == "__main__":
    main()
