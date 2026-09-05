"""Build real, leakage-safe return/cancellation-risk evidence from UCI Online Retail.

Source:
  Chen, D. (2015). Online Retail [Dataset]. UCI Machine Learning Repository.
  DOI: 10.24432/C5BW33 · License: CC BY 4.0

The raw log mixes original sale lines with later C-prefixed cancellation lines.
The cancellation marker, negative quantity and future return outcome are NEVER
model inputs. Cancellation units are conservatively matched back to earlier
sales, the original sale invoice becomes the labelled example, features are
computed as-of the original order timestamp, and the final test is strictly
chronological.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from datetime import timedelta
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import GradientBoostingClassifier, HistGradientBoostingClassifier, RandomForestClassifier
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
TOP_SKUS = 30

SERVICE_CODES = {
    "POST", "D", "M", "DOT", "BANK CHARGES", "AMAZONFEE", "CRUK",
    "S", "B", "PADS", "ADJUST", "ADJUST2", "TEST001", "TEST002",
}
FORBIDDEN_FEATURE_TOKENS = {
    "invoice", "cancel", "return", "label", "target", "returned", "outcome",
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
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def load_raw(path: Path) -> pd.DataFrame:
    df = pd.read_excel(path)
    df = df.rename(columns={"Invoice": "InvoiceNo", "Price": "UnitPrice", "Customer ID": "CustomerID"})
    required = ["InvoiceNo", "StockCode", "Description", "Quantity", "InvoiceDate", "UnitPrice", "CustomerID", "Country"]
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise ValueError(f"Dataset schema mismatch; missing {missing}")
    df = df[required].copy()
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
    audit = {"raw_rows": int(len(raw))}
    df = raw.drop_duplicates().copy()
    audit["exact_duplicates_removed"] = int(len(raw) - len(df))
    valid = df[["InvoiceNo", "StockCode", "Quantity", "InvoiceDate", "UnitPrice", "CustomerID"]].notna().all(axis=1)
    audit["rows_missing_essential_removed"] = int((~valid).sum())
    df = df.loc[valid].copy()
    service = df["StockCode"].isin(SERVICE_CODES)
    audit["service_adjustment_rows_removed"] = int(service.sum())
    df = df.loc[~service].copy()
    bad_price = df["UnitPrice"] <= 0
    audit["nonpositive_price_rows_removed"] = int(bad_price.sum())
    df = df.loc[~bad_price].copy()
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
    """Conservatively LIFO-match cancellation units to prior identical sales."""
    work = df.sort_values(["InvoiceDate", "InvoiceNo", "StockCode"]).copy()
    sales = work.loc[~work["is_cancel_outcome"]].copy()
    returned_qty: dict[int, float] = defaultdict(float)
    first_return_date: dict[int, pd.Timestamp] = {}
    pools: dict[tuple[int, str, float], list[list]] = defaultdict(list)
    audit = MatchAudit()

    for row in work.itertuples():
        key = (int(row.CustomerID), str(row.StockCode), round(float(row.UnitPrice), 2))
        ts = row.InvoiceDate
        if not bool(row.is_cancel_outcome):
            audit.sale_rows += 1
            pools[key].append([row.Index, ts, float(row.Quantity)])
            continue

        audit.cancel_rows += 1
        needed = int(round(abs(float(row.Quantity))))
        audit.cancel_units += needed
        original = needed
        for rec in reversed(pools.get(key, [])):
            if needed <= 0:
                break
            sale_idx, sale_ts, remaining = rec
            age = (ts - sale_ts).total_seconds() / 86400
            if age < 0:
                continue
            if age > RETURN_HORIZON_DAYS:
                break
            if remaining <= 0:
                continue
            allocated = min(float(needed), float(remaining))
            rec[2] -= allocated
            returned_qty[sale_idx] += allocated
            if sale_idx not in first_return_date or ts < first_return_date[sale_idx]:
                first_return_date[sale_idx] = ts
            audit.matched_units += int(round(allocated))
            needed -= int(round(allocated))
        if needed < original:
            audit.matched_cancel_rows += 1
        audit.unmatched_units += needed

    sales["returned_qty"] = [returned_qty.get(i, 0.0) for i in sales.index]
    sales["first_return_date"] = [first_return_date.get(i, pd.NaT) for i in sales.index]
    sales["line_value_gbp"] = sales["Quantity"] * sales["UnitPrice"]
    sales["returned_value_gbp"] = sales["returned_qty"] * sales["UnitPrice"]
    sales["returned_sku_marker"] = np.where(sales["returned_qty"] > 0, sales["StockCode"], None)
    return sales, audit


def _tuple_unique(values) -> tuple[str, ...]:
    return tuple(sorted({str(v) for v in values if pd.notna(v) and v is not None}))


def build_orders(sales: pd.DataFrame) -> pd.DataFrame:
    max_observed = sales["InvoiceDate"].max()
    cutoff = max_observed - timedelta(days=RETURN_HORIZON_DAYS)
    eligible = sales.loc[sales["InvoiceDate"] <= cutoff].copy()
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
            sku_list=("StockCode", _tuple_unique),
            mean_unit_price=("UnitPrice", "mean"),
            max_unit_price=("UnitPrice", "max"),
            returned_value_gbp=("returned_value_gbp", "sum"),
            returned_units=("returned_qty", "sum"),
            returned_sku_list=("returned_sku_marker", _tuple_unique),
            first_return_date=("first_return_date", "min"),
        )
        .reset_index()
    )
    orders = orders[(orders["total_gbp"] > 0) & (orders["units"] > 0)].copy()
    orders["label_return"] = (orders["returned_units"] > 0).astype(int)
    orders["avg_item_value"] = orders["total_gbp"] / orders["units"].clip(lower=1)
    orders["basket_density"] = orders["units"] / orders["distinct_skus"].clip(lower=1)
    orders["max_price_share"] = orders["max_unit_price"] / orders["total_gbp"].clip(lower=0.01)
    return orders.sort_values(["order_date", "InvoiceNo"]).reset_index(drop=True)


def add_asof_history_features(orders: pd.DataFrame) -> pd.DataFrame:
    """Build histories using only facts observable before each current order."""
    return_events = []
    for row in orders.loc[orders["label_return"] == 1].itertuples(index=False):
        if pd.isna(row.first_return_date):
            continue
        return_events.append(
            (
                row.first_return_date,
                int(row.customer_id),
                str(row.country),
                tuple(row.returned_sku_list),
                float(row.returned_value_gbp),
            )
        )
    return_events.sort(key=lambda x: x[0])
    event_i = 0

    customer_purchase: dict[int, dict] = {}
    customer_known_returns: Counter[int] = Counter()
    customer_known_return_value: defaultdict[int, float] = defaultdict(float)
    product_prior_orders: Counter[str] = Counter()
    product_known_returns: Counter[str] = Counter()
    country_prior_orders: Counter[str] = Counter()
    country_known_returns: Counter[str] = Counter()

    rows = []
    for row in orders.itertuples(index=False):
        ts = row.order_date
        while event_i < len(return_events) and return_events[event_i][0] < ts:
            _, cust, country, returned_skus, value = return_events[event_i]
            customer_known_returns[cust] += 1
            customer_known_return_value[cust] += value
            country_known_returns[country] += 1
            for sku in returned_skus:
                product_known_returns[sku] += 1
            event_i += 1

        cust = int(row.customer_id)
        country = str(row.country)
        state = customer_purchase.get(cust)
        if state is None:
            prior_orders = 0
            prior_spend = 0.0
            tenure = 0.0
            since_last = -1.0
            avg_prior = 0.0
        else:
            prior_orders = int(state["orders"])
            prior_spend = float(state["spend"])
            tenure = max(0.0, (ts - state["first"]).total_seconds() / 86400)
            since_last = max(0.0, (ts - state["last"]).total_seconds() / 86400)
            avg_prior = prior_spend / max(prior_orders, 1)

        known_returns = int(customer_known_returns[cust])
        customer_known_rate = known_returns / max(prior_orders, 1) if prior_orders else 0.0

        sku_rates = []
        sku_support = []
        for sku in row.sku_list:
            support = int(product_prior_orders[sku])
            rate = product_known_returns[sku] / max(support, 1) if support else 0.0
            sku_rates.append(float(rate))
            sku_support.append(float(support))

        country_support = int(country_prior_orders[country])
        country_known_rate = country_known_returns[country] / max(country_support, 1) if country_support else 0.0

        rec = row._asdict()
        rec.update(
            customer_prior_orders=prior_orders,
            customer_prior_spend=prior_spend,
            customer_tenure_days=tenure,
            days_since_last_order=since_last,
            avg_prior_order_value=avg_prior,
            customer_known_return_events=known_returns,
            customer_known_return_rate=customer_known_rate,
            customer_known_return_value=float(customer_known_return_value[cust]),
            customer_had_known_return=int(known_returns > 0),
            product_known_return_rate_mean=float(np.mean(sku_rates)) if sku_rates else 0.0,
            product_known_return_rate_max=float(np.max(sku_rates)) if sku_rates else 0.0,
            product_prior_order_support_mean=float(np.mean(sku_support)) if sku_support else 0.0,
            product_prior_order_support_max=float(np.max(sku_support)) if sku_support else 0.0,
            country_prior_orders=country_support,
            country_known_return_rate=float(country_known_rate),
            order_hour=int(ts.hour),
            order_day_of_week=int(ts.dayofweek),
            order_month=int(ts.month),
            is_weekend=int(ts.dayofweek >= 5),
            is_new_customer=int(prior_orders == 0),
        )
        rows.append(rec)

        if state is None:
            customer_purchase[cust] = {"orders": 1, "spend": float(row.total_gbp), "first": ts, "last": ts}
        else:
            state["orders"] += 1
            state["spend"] += float(row.total_gbp)
            state["last"] = ts
        country_prior_orders[country] += 1
        for sku in row.sku_list:
            product_prior_orders[sku] += 1

    return pd.DataFrame(rows)


def chronological_split(df: pd.DataFrame):
    n = len(df)
    a, b = int(n * 0.70), int(n * 0.85)
    train, val, test = df.iloc[:a].copy(), df.iloc[a:b].copy(), df.iloc[b:].copy()
    if min(train["label_return"].sum(), val["label_return"].sum(), test["label_return"].sum()) <= 0:
        raise RuntimeError("A chronological split has no positive returns")
    if not (train["order_date"].max() <= val["order_date"].min() <= test["order_date"].min()):
        raise RuntimeError("Chronological split invariant failed")
    return train, val, test


def build_feature_frames(train: pd.DataFrame, val: pd.DataFrame, test: pd.DataFrame):
    top_countries = train["country"].value_counts().head(TOP_COUNTRIES).index.tolist()
    sku_counter = Counter(sku for skus in train["sku_list"] for sku in skus)
    top_skus = [sku for sku, _ in sku_counter.most_common(TOP_SKUS)]
    numeric = [
        "total_gbp", "units", "line_count", "distinct_skus", "mean_unit_price", "max_unit_price",
        "avg_item_value", "basket_density", "max_price_share", "customer_prior_orders", "customer_prior_spend",
        "customer_tenure_days", "days_since_last_order", "avg_prior_order_value", "customer_known_return_events",
        "customer_known_return_rate", "customer_known_return_value", "customer_had_known_return",
        "product_known_return_rate_mean", "product_known_return_rate_max", "product_prior_order_support_mean",
        "product_prior_order_support_max", "country_prior_orders", "country_known_return_rate", "order_hour",
        "order_day_of_week", "order_month", "is_weekend", "is_new_customer",
    ]

    def safe_token(text: str) -> str:
        return "".join(ch if ch.isalnum() else "_" for ch in text).strip("_")

    def transform(frame: pd.DataFrame) -> pd.DataFrame:
        out = frame[numeric].astype(float).replace([np.inf, -np.inf], np.nan).fillna(0.0).copy()
        out["log_total_gbp"] = np.log1p(out["total_gbp"].clip(lower=0))
        out["log_units"] = np.log1p(out["units"].clip(lower=0))
        out["log_prior_spend"] = np.log1p(out["customer_prior_spend"].clip(lower=0))
        out["log_prior_orders"] = np.log1p(out["customer_prior_orders"].clip(lower=0))
        for country in top_countries:
            out[f"country__{safe_token(country)}"] = (frame["country"] == country).astype(float).values
        out["country__OTHER"] = (~frame["country"].isin(top_countries)).astype(float).values
        sku_sets = frame["sku_list"].apply(set)
        for sku in top_skus:
            out[f"sku__{safe_token(sku)}"] = sku_sets.apply(lambda s, sku=sku: float(sku in s)).values
        return out

    x_train, x_val, x_test = transform(train), transform(val), transform(test)
    names = list(x_train.columns)
    if names != list(x_val.columns) or names != list(x_test.columns):
        raise RuntimeError("Feature schema drift")
    for name in names:
        if any(token in name.lower() for token in FORBIDDEN_FEATURE_TOKENS):
            raise RuntimeError(f"Leakage guard rejected feature {name}")
    return x_train, x_val, x_test, names, top_countries, top_skus


def select_threshold(y, p):
    best = None
    for threshold in np.linspace(0.02, 0.98, 193):
        pred = (p >= threshold).astype(int)
        precision = precision_score(y, pred, zero_division=0)
        recall = recall_score(y, pred, zero_division=0)
        f1 = f1_score(y, pred, zero_division=0)
        key = (f1, precision, recall, -float(threshold))
        if best is None or key > best[0]:
            best = (key, float(threshold), {"precision": float(precision), "recall": float(recall), "f1": float(f1)})
    return best[1], best[2]


def evaluate_split(frame, y, p, threshold):
    pred = (p >= threshold).astype(int)
    tn, fp, fn, tp = confusion_matrix(y, pred, labels=[0, 1]).ravel()
    fp_mask, fn_mask, tp_mask = (pred == 1) & (y == 0), (pred == 0) & (y == 1), (pred == 1) & (y == 1)
    return_value = float(frame.loc[y == 1, "returned_value_gbp"].sum())
    captured = float(frame.loc[tp_mask, "returned_value_gbp"].sum())
    return {
        "rows": int(len(frame)), "positives": int(y.sum()), "prevalence": float(y.mean()), "threshold": float(threshold),
        "tp": int(tp), "fp": int(fp), "fn": int(fn), "tn": int(tn),
        "precision": float(precision_score(y, pred, zero_division=0)),
        "recall": float(recall_score(y, pred, zero_division=0)),
        "f1": float(f1_score(y, pred, zero_division=0)),
        "accuracy": float(accuracy_score(y, pred)),
        "balanced_accuracy": float(balanced_accuracy_score(y, pred)),
        "average_precision": float(average_precision_score(y, p)),
        "roc_auc": float(roc_auc_score(y, p)),
        "false_positive_cost_upper_bound_gbp": float(frame.loc[fp_mask, "total_gbp"].sum()),
        "false_positive_cost_definition": "sum(real order value for legitimate test orders incorrectly flagged; hard-block upper bound, not claimed realised loss)",
        "missed_return_value_gbp": float(frame.loc[fn_mask, "returned_value_gbp"].sum()),
        "total_return_value_gbp": return_value,
        "captured_return_value_gbp": captured,
        "captured_return_value_rate": 0.0 if return_value <= 0 else captured / return_value,
    }


def candidate_models():
    return {
        "gradient_boosting": GradientBoostingClassifier(
            n_estimators=220, learning_rate=0.04, max_depth=3, min_samples_leaf=35, subsample=0.85, random_state=RANDOM_STATE
        ),
        "random_forest": RandomForestClassifier(
            n_estimators=320, max_depth=13, min_samples_leaf=6, max_features="sqrt", class_weight="balanced_subsample",
            n_jobs=-1, random_state=RANDOM_STATE,
        ),
        "hist_gradient_boosting": HistGradientBoostingClassifier(
            max_iter=260, learning_rate=0.05, max_leaf_nodes=31, min_samples_leaf=20, l2_regularization=1.0,
            random_state=RANDOM_STATE,
        ),
    }


def train_select(x_train, y_train, x_val, y_val, val_frame):
    positives, negatives = max(int(y_train.sum()), 1), max(int(len(y_train) - y_train.sum()), 1)
    weight = negatives / positives
    sample_weight = np.where(y_train == 1, weight, 1.0)
    summaries = {}
    best = None
    for name, model in candidate_models().items():
        fit_kwargs = {} if name == "random_forest" else {"sample_weight": sample_weight}
        model.fit(x_train, y_train, **fit_kwargs)
        p = model.predict_proba(x_val)[:, 1]
        threshold, selected = select_threshold(y_val, p)
        metrics = evaluate_split(val_frame, y_val, p, threshold)
        summaries[name] = {"threshold": threshold, "selection": selected, "validation": metrics}
        key = (metrics["f1"], metrics["average_precision"], metrics["precision"], metrics["recall"])
        if best is None or key > best[0]:
            best = (key, name, model, threshold)
    return best[1], best[2], best[3], summaries


def holdout_export(test: pd.DataFrame, x_test: pd.DataFrame, p: np.ndarray, threshold: float) -> pd.DataFrame:
    meta = test[[
        "InvoiceNo", "order_date", "country", "total_gbp", "units", "line_count", "distinct_skus",
        "customer_prior_orders", "customer_prior_spend", "customer_tenure_days", "days_since_last_order",
        "customer_known_return_events", "customer_known_return_rate", "product_known_return_rate_mean",
        "product_known_return_rate_max", "label_return", "returned_value_gbp",
    ]].copy()
    meta["case_id"] = [hashlib.sha256(f"{inv}|{ts.isoformat()}".encode()).hexdigest()[:20] for inv, ts in zip(meta["InvoiceNo"], meta["order_date"])]
    meta = meta.drop(columns=["InvoiceNo"]).reset_index(drop=True)
    feature_copy = x_test.reset_index(drop=True).add_prefix("f__")
    out = pd.concat([meta, feature_copy], axis=1)
    out["probability"] = p
    out["prediction"] = (p >= threshold).astype(int)
    cols = ["case_id"] + [c for c in out.columns if c != "case_id"]
    return out[cols]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", type=Path, required=True)
    ap.add_argument("--outdir", type=Path, default=Path("artifacts/real_return"))
    args = ap.parse_args()
    args.outdir.mkdir(parents=True, exist_ok=True)

    raw_sha = sha256_file(args.input)
    raw = load_raw(args.input)
    cleaned, cleaning = clean_transaction_log(raw)
    sales, matching = match_cancellations_to_prior_sales(cleaned)
    orders = add_asof_history_features(build_orders(sales))
    train, val, test = chronological_split(orders)
    x_train, x_val, x_test, feature_names, top_countries, top_skus = build_feature_frames(train, val, test)
    y_train = train["label_return"].to_numpy(int)
    y_val = val["label_return"].to_numpy(int)
    y_test = test["label_return"].to_numpy(int)

    model_name, model, threshold, candidates = train_select(x_train, y_train, x_val, y_val, val)
    test_p = model.predict_proba(x_test)[:, 1]
    test_metrics = evaluate_split(test, y_test, test_p, threshold)
    val_p = model.predict_proba(x_val)[:, 1]
    validation_metrics = evaluate_split(val, y_val, val_p, threshold)

    baseline = {
        "name": "always_safe", "accuracy": float((y_test == 0).mean()), "precision": 0.0, "recall": 0.0, "f1": 0.0
    }
    match_rate = 0.0 if matching.cancel_units == 0 else matching.matched_units / matching.cancel_units
    report = {
        "evidence_version": "real-return-v2",
        "loss_class": "ORDER_RETURN_OR_CANCELLATION",
        "source": {"dataset": DATASET_NAME, "doi": DATASET_DOI, "url": DATASET_URL, "license": DATASET_LICENSE,
                   "raw_sha256": raw_sha, "raw_rows": int(len(raw))},
        "label_definition": f"Original sale invoice labelled positive when at least one later C-prefixed cancellation unit is conservatively matched to the same anonymised customer, SKU and price within {RETURN_HORIZON_DAYS} days.",
        "leakage_controls": [
            "C-prefixed invoice marker is outcome-only and never a feature.",
            "Negative quantity is outcome-only and never a feature.",
            "Current/future return labels and returned value are never model features.",
            "Customer/product/country return-history features include only cancellation events observed before the current order timestamp.",
            "Purchase-history features are computed before the current order updates state.",
            "Train/validation/test are chronological; threshold and model family are chosen on validation only.",
            f"Final {RETURN_HORIZON_DAYS} days are censored to avoid incomplete return windows.",
        ],
        "cleaning": cleaning,
        "matching": {**asdict(matching), "matched_cancel_unit_rate": match_rate, "return_horizon_days": RETURN_HORIZON_DAYS},
        "orders": {"eligible": int(len(orders)), "positives": int(orders["label_return"].sum()), "prevalence": float(orders["label_return"].mean()),
                   "date_min": orders["order_date"].min().isoformat(), "date_max": orders["order_date"].max().isoformat()},
        "split": {"method": "chronological_70_15_15", "train_rows": len(train), "validation_rows": len(val), "test_rows": len(test),
                  "train_end": train["order_date"].max().isoformat(), "validation_start": val["order_date"].min().isoformat(),
                  "validation_end": val["order_date"].max().isoformat(), "test_start": test["order_date"].min().isoformat(), "test_end": test["order_date"].max().isoformat()},
        "model": {"selected": model_name, "random_state": RANDOM_STATE, "feature_count": len(feature_names), "feature_names": feature_names,
                  "top_countries": top_countries, "top_skus": top_skus, "threshold": threshold, "selection_rule": "highest validation F1; AP/precision/recall tie-breakers",
                  "candidates": candidates},
        "validation": validation_metrics,
        "test": test_metrics,
        "baseline": baseline,
        "metric_boundary": {
            "precision_recall_are": "untouched chronological test results after all model-family and threshold selection on validation",
            "false_positive_cost_is": "hard-block upper bound from actual legitimate order value in GBP; not net merchant loss",
            "currency": "GBP as recorded by the source retailer",
        },
    }

    bundle = {
        "evidence_version": report["evidence_version"], "source_raw_sha256": raw_sha, "model_name": model_name,
        "model": model, "threshold": threshold, "feature_names": feature_names, "top_countries": top_countries, "top_skus": top_skus,
    }
    (args.outdir / "metrics.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    joblib.dump(bundle, args.outdir / "model.joblib", compress=3)
    holdout_export(test, x_test, test_p, threshold).to_csv(args.outdir / "heldout_orders.csv.gz", index=False, compression="gzip")

    print("REAL RETURN EVIDENCE V2 BUILT")
    print(f"source rows={len(raw):,} sha256={raw_sha}")
    print(f"eligible orders={len(orders):,} positives={orders['label_return'].sum():,} prevalence={orders['label_return'].mean():.2%}")
    print(f"cancel-unit match coverage={match_rate:.2%}")
    for name, summary in candidates.items():
        vm = summary["validation"]
        print(f"CANDIDATE {name}: P={vm['precision']:.4f} R={vm['recall']:.4f} F1={vm['f1']:.4f} AP={vm['average_precision']:.4f} t={summary['threshold']:.3f}")
    print(f"SELECTED {model_name} threshold={threshold:.3f}")
    print(f"TEST P={test_metrics['precision']:.4f} R={test_metrics['recall']:.4f} F1={test_metrics['f1']:.4f} AP={test_metrics['average_precision']:.4f} BA={test_metrics['balanced_accuracy']:.4f}")
    print(f"TEST TP={test_metrics['tp']} FP={test_metrics['fp']} FN={test_metrics['fn']} TN={test_metrics['tn']}")
    print(f"FP hard-block upper-bound GBP={test_metrics['false_positive_cost_upper_bound_gbp']:.2f}")
    print(f"missed returned value GBP={test_metrics['missed_return_value_gbp']:.2f}")


if __name__ == "__main__":
    main()
