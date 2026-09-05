"""Build the primary real return-to-seller detector on Amazon India sales data.

The public Kaggle dataset `thedevastator/unlock-profits-with-e-commerce-sales-data`
contains the Amazon Sale Report with 128k+ rows and explicit terminal statuses such
as `Shipped - Delivered to Buyer` and `Shipped - Returned to Seller`.

The pipeline is leakage-bounded and order-level:
- only terminal delivered/returned statuses are kept;
- duplicate item rows are aggregated by Order ID before splitting;
- no Status/Courier Status/order identifier is a feature;
- train/validation/test are chronological date blocks;
- target-encoding maps are learned from train only;
- train rows use leave-one-out encodings;
- hyperparameters and threshold are selected on validation only;
- the final test block is touched once after selection.

Raw third-party rows are never emitted or committed.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import math
import re
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (
    average_precision_score,
    balanced_accuracy_score,
    f1_score,
    precision_score,
    recall_score,
    roc_auc_score,
)
from sklearn.preprocessing import StandardScaler

DATASET_SLUG = "thedevastator/unlock-profits-with-e-commerce-sales-data"
DATASET_PAGE = "https://www.kaggle.com/datasets/thedevastator/unlock-profits-with-e-commerce-sales-data/data"
DATASET_DOWNLOAD = "https://www.kaggle.com/api/v1/datasets/download/thedevastator/unlock-profits-with-e-commerce-sales-data"
TARGET_MEMBER = "Amazon Sale Report.csv"
RANDOM_SEED = 20260905
POSITIVE = "RETURNED_TO_SELLER"
NEGATIVE = "DELIVERED_TO_BUYER"


@dataclass(frozen=True)
class Columns:
    order_id: str
    order_date: str
    status: str
    fulfilment: str | None
    sales_channel: str | None
    service_level: str | None
    style: str | None
    sku: str | None
    category: str | None
    size: str | None
    quantity: str | None
    amount: str | None
    ship_city: str | None
    ship_state: str | None
    ship_postal: str | None
    b2b: str | None


def _norm_name(value: object) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value).strip().lower())


def _norm_text(value: object) -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return ""
    return re.sub(r"\s+", " ", str(value).strip().lower())


def _pick(columns: Iterable[str], aliases: Iterable[str], *, required: bool = False) -> str | None:
    by_norm = {_norm_name(column): column for column in columns}
    for alias in aliases:
        match = by_norm.get(_norm_name(alias))
        if match:
            return match
    if required:
        raise ValueError(f"required column missing aliases={list(aliases)} found={list(columns)}")
    return None


def resolve_columns(frame: pd.DataFrame) -> Columns:
    cols = list(frame.columns)
    return Columns(
        order_id=_pick(cols, ["Order ID", "order_id", "amazon-order-id"], required=True),
        order_date=_pick(cols, ["Date", "order_date", "purchase-date"], required=True),
        status=_pick(cols, ["Status", "order_status"], required=True),
        fulfilment=_pick(cols, ["Fulfilment", "fulfillment"]),
        sales_channel=_pick(cols, ["Sales Channel", "sales_channel"]),
        service_level=_pick(cols, ["ship-service-level", "ship service level"]),
        style=_pick(cols, ["Style"]),
        sku=_pick(cols, ["SKU"]),
        category=_pick(cols, ["Category"]),
        size=_pick(cols, ["Size"]),
        quantity=_pick(cols, ["Qty", "Quantity"]),
        amount=_pick(cols, ["Amount", "item_total"]),
        ship_city=_pick(cols, ["ship-city", "ship city"]),
        ship_state=_pick(cols, ["ship-state", "ship state"]),
        ship_postal=_pick(cols, ["ship-postal-code", "ship postal code"]),
        b2b=_pick(cols, ["B2B"]),
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def download_dataset(cache_dir: Path) -> Path:
    cache_dir.mkdir(parents=True, exist_ok=True)
    destination = cache_dir / "amazon-india-sales.zip"
    if destination.exists() and destination.stat().st_size > 1024 and zipfile.is_zipfile(destination):
        return destination
    request = urllib.request.Request(
        DATASET_DOWNLOAD,
        headers={"User-Agent": "CodGate-real-return-risk/1.0 (+https://github.com/Akshu1245/codgate)"},
    )
    with urllib.request.urlopen(request, timeout=300) as response, destination.open("wb") as handle:
        while True:
            chunk = response.read(1024 * 1024)
            if not chunk:
                break
            handle.write(chunk)
    if not zipfile.is_zipfile(destination):
        raise RuntimeError("Kaggle Amazon India download is not a ZIP archive")
    return destination


def load_source(archive: Path) -> tuple[pd.DataFrame, str, Columns, list[str]]:
    with zipfile.ZipFile(archive) as zf:
        members = zf.namelist()
        csv_members = [m for m in members if m.lower().endswith(".csv")]
        candidates = [m for m in csv_members if Path(m).name.lower() == TARGET_MEMBER.lower()]
        if not candidates:
            candidates = [m for m in csv_members if "amazon" in Path(m).name.lower() and "sale" in Path(m).name.lower()]
        if not candidates:
            raise RuntimeError(f"Amazon Sale Report.csv not found; members={members[:50]}")
        member = sorted(candidates, key=lambda m: len(m))[0]
        payload = zf.read(member)
    last_error = None
    for encoding in ("utf-8", "utf-8-sig", "latin1"):
        try:
            frame = pd.read_csv(io.BytesIO(payload), encoding=encoding, low_memory=False)
            return frame, member, resolve_columns(frame), members
        except (UnicodeDecodeError, ValueError) as exc:
            last_error = exc
    raise RuntimeError(f"failed to read Amazon sale report: {last_error}")


def _status(value: object) -> str:
    text = _norm_text(value)
    if "returned" in text and "seller" in text:
        return POSITIVE
    if "delivered" in text and "buyer" in text:
        return NEGATIVE
    return text.upper().replace(" ", "_")


def _number(value: object) -> float:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return 0.0
    text = re.sub(r"[^0-9.\-]+", "", str(value))
    if not text or text in {"-", ".", "-."}:
        return 0.0
    try:
        number = float(text)
    except ValueError:
        return 0.0
    return number if math.isfinite(number) else 0.0


def _first_nonempty(series: pd.Series) -> str:
    for value in series:
        text = _norm_text(value)
        if text:
            return text
    return ""


def _combo(series: pd.Series, limit: int = 12) -> str:
    values = sorted({_norm_text(value) for value in series if _norm_text(value)})
    return "|".join(values[:limit])


def prepare_orders(frame: pd.DataFrame, columns: Columns) -> tuple[pd.DataFrame, dict]:
    work = frame.copy()
    work["_status"] = work[columns.status].map(_status)
    terminal = work[work["_status"].isin({POSITIVE, NEGATIVE})].copy()
    terminal["_order_date"] = pd.to_datetime(terminal[columns.order_date], errors="coerce", dayfirst=False, format="mixed")
    terminal = terminal[terminal["_order_date"].notna()].copy()
    terminal["_order_id"] = terminal[columns.order_id].astype(str).str.strip()
    terminal = terminal[terminal["_order_id"].ne("") & terminal["_order_id"].ne("nan")].copy()
    terminal["_target"] = (terminal["_status"] == POSITIVE).astype(int)

    conflicts = terminal.groupby("_order_id")["_target"].nunique()
    conflict_ids = set(conflicts[conflicts > 1].index)
    if conflict_ids:
        terminal = terminal[~terminal["_order_id"].isin(conflict_ids)].copy()

    def field(row_group: pd.DataFrame, name: str | None, combo: bool = False) -> str:
        if not name:
            return ""
        return _combo(row_group[name]) if combo else _first_nonempty(row_group[name])

    rows: list[dict] = []
    for order_id, group in terminal.groupby("_order_id", sort=False):
        target = int(group["_target"].iloc[0])
        date = group["_order_date"].min()
        qty = sum(_number(v) for v in group[columns.quantity]) if columns.quantity else float(len(group))
        amount = sum(_number(v) for v in group[columns.amount]) if columns.amount else 0.0
        postal = field(group, columns.ship_postal)
        digits = re.sub(r"\D", "", postal)
        rows.append(
            {
                "_order_id": order_id,
                "_order_date": date,
                "_target": target,
                "fulfilment": field(group, columns.fulfilment),
                "sales_channel": field(group, columns.sales_channel),
                "service_level": field(group, columns.service_level),
                "style": field(group, columns.style, combo=True),
                "sku": field(group, columns.sku, combo=True),
                "category": field(group, columns.category, combo=True),
                "size": field(group, columns.size, combo=True),
                "ship_city": field(group, columns.ship_city),
                "ship_state": field(group, columns.ship_state),
                "pin3": digits[:3] if len(digits) >= 3 else "",
                "b2b": field(group, columns.b2b),
                "quantity": float(qty),
                "amount": float(amount),
                "item_rows": int(len(group)),
            }
        )
    orders = pd.DataFrame(rows).sort_values(["_order_date", "_order_id"]).reset_index(drop=True)
    if len(orders) < 1000 or orders["_target"].sum() < 100:
        raise RuntimeError(
            f"terminal order population unexpectedly small: orders={len(orders)} returns={int(orders['_target'].sum())}"
        )
    diagnostics = {
        "raw_rows": int(len(frame)),
        "terminal_item_rows": int(len(terminal)),
        "terminal_unique_orders": int(len(orders)),
        "returned_to_seller_orders": int(orders["_target"].sum()),
        "delivered_to_buyer_orders": int((orders["_target"] == 0).sum()),
        "conflicting_order_ids_dropped": int(len(conflict_ids)),
    }
    return orders, diagnostics


CATEGORICAL = [
    "fulfilment",
    "sales_channel",
    "service_level",
    "style",
    "sku",
    "category",
    "size",
    "ship_city",
    "ship_state",
    "pin3",
    "b2b",
]


def chronological_split(frame: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    days = sorted(pd.Timestamp(v) for v in frame["_order_date"].dt.normalize().unique())
    if len(days) < 10:
        raise RuntimeError("not enough distinct dates for chronological evaluation")
    train_cut = days[max(1, int(len(days) * 0.60))]
    val_cut = days[max(2, int(len(days) * 0.80))]
    train = frame[frame["_order_date"].dt.normalize() < train_cut].copy()
    validation = frame[
        (frame["_order_date"].dt.normalize() >= train_cut)
        & (frame["_order_date"].dt.normalize() < val_cut)
    ].copy()
    test = frame[frame["_order_date"].dt.normalize() >= val_cut].copy()
    for name, part in (("train", train), ("validation", validation), ("test", test)):
        if len(part) < 100:
            raise RuntimeError(f"{name} chronological split too small: {len(part)}")
        if part["_target"].nunique() != 2:
            raise RuntimeError(f"{name} split lacks one class")
    return train, validation, test


def _category_stats(train: pd.DataFrame, column: str, alpha: float) -> tuple[dict[str, tuple[int, float]], float]:
    global_rate = float(train["_target"].mean())
    grouped = train.groupby(column, dropna=False)["_target"].agg(["count", "sum"])
    mapping = {str(idx): (int(row["count"]), float(row["sum"])) for idx, row in grouped.iterrows()}
    return mapping, global_rate


def _encode_part(
    part: pd.DataFrame,
    train: pd.DataFrame,
    alpha: float,
    *,
    leave_one_out: bool,
) -> tuple[np.ndarray, dict]:
    features: list[np.ndarray] = []
    artifact_maps: dict[str, dict] = {}
    global_rate = float(train["_target"].mean())
    n_train = max(1, len(train))
    for column in CATEGORICAL:
        mapping, _ = _category_stats(train, column, alpha)
        rates = np.empty(len(part), dtype=float)
        freqs = np.empty(len(part), dtype=float)
        values = part[column].fillna("").astype(str).to_numpy()
        targets = part["_target"].to_numpy(dtype=float)
        for i, value in enumerate(values):
            count, positive = mapping.get(value, (0, 0.0))
            if leave_one_out:
                adjusted_count = max(0, count - 1)
                adjusted_positive = max(0.0, positive - targets[i])
                rates[i] = (adjusted_positive + alpha * global_rate) / (adjusted_count + alpha)
                freqs[i] = math.log1p(adjusted_count) / math.log1p(n_train)
            else:
                rates[i] = (positive + alpha * global_rate) / (count + alpha)
                freqs[i] = math.log1p(count) / math.log1p(n_train)
        features.extend([rates, freqs])
        artifact_maps[column] = {
            key: {
                "count": count,
                "returned": positive,
                "smoothed_rate": (positive + alpha * global_rate) / (count + alpha),
            }
            for key, (count, positive) in mapping.items()
        }

    amount = np.log1p(np.clip(part["amount"].to_numpy(dtype=float), 0, None))
    quantity = np.log1p(np.clip(part["quantity"].to_numpy(dtype=float), 0, None))
    item_rows = np.log1p(np.clip(part["item_rows"].to_numpy(dtype=float), 0, None))
    dow = part["_order_date"].dt.dayofweek.to_numpy(dtype=float)
    month = part["_order_date"].dt.month.to_numpy(dtype=float)
    amount_per_item = np.log1p(
        np.divide(
            np.clip(part["amount"].to_numpy(dtype=float), 0, None),
            np.maximum(1.0, part["quantity"].to_numpy(dtype=float)),
        )
    )
    features.extend(
        [
            amount,
            quantity,
            item_rows,
            amount_per_item,
            np.sin(2 * np.pi * dow / 7),
            np.cos(2 * np.pi * dow / 7),
            np.sin(2 * np.pi * month / 12),
            np.cos(2 * np.pi * month / 12),
        ]
    )
    matrix = np.column_stack(features)
    artifact = {"global_return_rate": global_rate, "alpha": alpha, "maps": artifact_maps}
    return matrix, artifact


def _choose_threshold(y_true: np.ndarray, probabilities: np.ndarray) -> tuple[float, dict]:
    best: tuple[float, float, float, float] | None = None
    for raw in np.linspace(0.01, 0.60, 237):
        threshold = round(float(raw), 4)
        pred = probabilities >= threshold
        f1 = float(f1_score(y_true, pred, zero_division=0))
        recall = float(recall_score(y_true, pred, zero_division=0))
        precision = float(precision_score(y_true, pred, zero_division=0))
        candidate = (f1, recall, precision, threshold)
        if best is None or candidate > best:
            best = candidate
    assert best is not None
    f1, recall, precision, threshold = best
    return threshold, {"f1": f1, "recall": recall, "precision": precision}


def _metrics(part: pd.DataFrame, probabilities: np.ndarray, threshold: float) -> dict:
    y = part["_target"].to_numpy(dtype=int)
    pred = probabilities >= threshold
    tp = int(np.sum((pred == 1) & (y == 1)))
    fp = int(np.sum((pred == 1) & (y == 0)))
    fn = int(np.sum((pred == 0) & (y == 1)))
    tn = int(np.sum((pred == 0) & (y == 0)))
    fp_mask = (pred == 1) & (y == 0)
    fn_mask = (pred == 0) & (y == 1)
    return {
        "n": int(len(y)),
        "positives": int(np.sum(y)),
        "prevalence": float(np.mean(y)),
        "threshold": float(threshold),
        "precision": float(precision_score(y, pred, zero_division=0)),
        "recall": float(recall_score(y, pred, zero_division=0)),
        "f1": float(f1_score(y, pred, zero_division=0)),
        "balanced_accuracy": float(balanced_accuracy_score(y, pred)),
        "average_precision": float(average_precision_score(y, probabilities)),
        "roc_auc": float(roc_auc_score(y, probabilities)),
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "tn": tn,
        "false_positive_order_gmv_at_risk_inr": round(float(part.loc[fp_mask, "amount"].sum()), 2),
        "missed_return_order_gmv_inr": round(float(part.loc[fn_mask, "amount"].sum()), 2),
    }


def _bootstrap_ci(part: pd.DataFrame, probabilities: np.ndarray, threshold: float, rounds: int = 2000) -> dict:
    y = part["_target"].to_numpy(dtype=int)
    rng = np.random.default_rng(RANDOM_SEED)
    precision_values: list[float] = []
    recall_values: list[float] = []
    for _ in range(rounds):
        idx = rng.integers(0, len(y), size=len(y))
        ys = y[idx]
        if len(np.unique(ys)) < 2:
            continue
        pred = probabilities[idx] >= threshold
        precision_values.append(float(precision_score(ys, pred, zero_division=0)))
        recall_values.append(float(recall_score(ys, pred, zero_division=0)))
    return {
        "precision": [float(np.quantile(precision_values, 0.025)), float(np.quantile(precision_values, 0.975))],
        "recall": [float(np.quantile(recall_values, 0.025)), float(np.quantile(recall_values, 0.975))],
    }


def train_and_evaluate(orders: pd.DataFrame, source: dict, diagnostics: dict) -> tuple[dict, dict]:
    train, validation, test = chronological_split(orders)
    y_train = train["_target"].to_numpy(dtype=int)
    y_val = validation["_target"].to_numpy(dtype=int)

    trials: list[dict] = []
    best = None
    for alpha in (5.0, 15.0, 30.0, 60.0):
        train_x_raw, encoding_artifact = _encode_part(train, train, alpha, leave_one_out=True)
        val_x_raw, _ = _encode_part(validation, train, alpha, leave_one_out=False)
        scaler = StandardScaler().fit(train_x_raw)
        train_x = scaler.transform(train_x_raw)
        val_x = scaler.transform(val_x_raw)
        for class_weight in (None, "balanced"):
            for c in (0.05, 0.2, 1.0, 5.0):
                model = LogisticRegression(
                    C=c,
                    class_weight=class_weight,
                    max_iter=2000,
                    solver="liblinear",
                    random_state=RANDOM_SEED,
                )
                model.fit(train_x, y_train)
                val_prob = model.predict_proba(val_x)[:, 1]
                threshold, tm = _choose_threshold(y_val, val_prob)
                ap = float(average_precision_score(y_val, val_prob))
                auc = float(roc_auc_score(y_val, val_prob))
                trial = {
                    "alpha": alpha,
                    "C": c,
                    "class_weight": class_weight or "none",
                    "validation_average_precision": ap,
                    "validation_roc_auc": auc,
                    "threshold": threshold,
                    "validation_f1": tm["f1"],
                    "validation_recall": tm["recall"],
                    "validation_precision": tm["precision"],
                }
                trials.append(trial)
                key = (ap, tm["f1"], tm["recall"], tm["precision"], -abs(alpha - 15.0), -abs(c - 1.0))
                if best is None or key > best[0]:
                    best = (key, trial, model, scaler, encoding_artifact)
    assert best is not None
    _key, selected, model, scaler, encoding_artifact = best
    alpha = float(selected["alpha"])
    test_x_raw, _ = _encode_part(test, train, alpha, leave_one_out=False)
    test_x = scaler.transform(test_x_raw)
    test_prob = model.predict_proba(test_x)[:, 1]
    threshold = float(selected["threshold"])
    heldout = _metrics(test, test_prob, threshold)
    ci = _bootstrap_ci(test, test_prob, threshold)

    def split_block(part: pd.DataFrame) -> dict:
        return {
            "n": int(len(part)),
            "returned_to_seller": int(part["_target"].sum()),
            "delivered_to_buyer": int((part["_target"] == 0).sum()),
            "date_range": {
                "start": part["_order_date"].min().isoformat(),
                "end": part["_order_date"].max().isoformat(),
            },
        }

    report = {
        "schema_version": 1,
        "loss_class": "RETURN_TO_SELLER",
        "claim": "Real public Amazon India return-risk prototype evidence; not Amazon/Razorpay production accuracy",
        "source": source,
        "source_diagnostics": diagnostics,
        "label_definition": {
            "positive": "Shipped - Returned to Seller",
            "negative": "Shipped - Delivered to Buyer",
            "excluded": "all cancelled, in-transit, pending, rejected, damaged and other non-terminal statuses",
        },
        "leakage_controls": {
            "order_id_feature": False,
            "status_feature": False,
            "courier_status_feature": False,
            "duplicate_item_rows_split_across_sets": False,
            "target_encoding_train_only": True,
            "train_target_encoding_leave_one_out": True,
            "test_used_for_model_selection": False,
        },
        "dataset": {
            "terminal_orders": int(len(orders)),
            "returned_to_seller": int(orders["_target"].sum()),
            "delivered_to_buyer": int((orders["_target"] == 0).sum()),
            "return_prevalence": float(orders["_target"].mean()),
            "date_range": {
                "start": orders["_order_date"].min().isoformat(),
                "end": orders["_order_date"].max().isoformat(),
            },
        },
        "split": {
            "method": "chronological date blocks 60/20/20; no shuffled row split, SMOTE, augmentation or duplicate expansion",
            "train": split_block(train),
            "validation": split_block(validation),
            "test": split_block(test),
        },
        "selection": {
            "criterion": "validation average precision, then validation F1/recall/precision; threshold selected only on validation",
            "selected": selected,
            "trials": trials,
        },
        "heldout_test": heldout,
        "heldout_ci95_bootstrap": ci,
        "false_positive_cost_disclosure": {
            "realized_profit_loss_identifiable_from_source": False,
            "reported_real_monetary_exposure": "false_positive_order_gmv_at_risk_inr",
            "why": "The source contains order amount but not contribution margin or conversion after a risk intervention.",
        },
        "limitations": [
            "The dataset is a public reprint of Amazon India marketplace sales data and is not an official Razorpay dataset.",
            "Returned-to-seller is the measured loss class; the large source does not expose payment method, so the headline detector is return-risk rather than COD-only RTO.",
            "A separate Boss Leathers source audit proves a real COD + returned-to-seller slice exists, but its 47 COD rows are intentionally not used as the final accuracy benchmark.",
            "False-positive GMV exposure is source-derived; realized margin loss is not invented.",
        ],
    }
    model_artifact = {
        "schema_version": 1,
        "family": "target_encoded_logistic_regression",
        "loss_class": "RETURN_TO_SELLER",
        "threshold": threshold,
        "selected": selected,
        "categorical_columns": CATEGORICAL,
        "encoding": encoding_artifact,
        "scaler_mean": scaler.mean_.tolist(),
        "scaler_scale": scaler.scale_.tolist(),
        "intercept": float(model.intercept_[0]),
        "coefficients": model.coef_[0].tolist(),
        "source_zip_sha256": source["zip_sha256"],
    }
    return report, model_artifact


def report_markdown(report: dict) -> str:
    ds = report["dataset"]
    test = report["heldout_test"]
    ci = report["heldout_ci95_bootstrap"]
    return "\n".join(
        [
            "# CodGate primary real return-risk evidence",
            "",
            "Loss class: **RETURN_TO_SELLER**.",
            "",
            f"- Source terminal orders: {ds['terminal_orders']}",
            f"- Returned to seller: {ds['returned_to_seller']}",
            f"- Delivered to buyer: {ds['delivered_to_buyer']}",
            f"- Held-out n: {test['n']} ({test['positives']} returns)",
            f"- Precision: {test['precision']:.2%} (bootstrap 95% {ci['precision'][0]:.2%}–{ci['precision'][1]:.2%})",
            f"- Recall: {test['recall']:.2%} (bootstrap 95% {ci['recall'][0]:.2%}–{ci['recall'][1]:.2%})",
            f"- F1: {test['f1']:.4f}",
            f"- PR-AUC: {test['average_precision']:.4f}",
            f"- ROC-AUC: {test['roc_auc']:.4f}",
            f"- Confusion: TP {test['tp']} · FP {test['fp']} · FN {test['fn']} · TN {test['tn']}",
            f"- False-positive order GMV subjected to intervention: ₹{test['false_positive_order_gmv_at_risk_inr']:.2f}",
            f"- Missed-return order GMV: ₹{test['missed_return_order_gmv_inr']:.2f}",
            "",
            "The test block is chronological and untouched by model/threshold selection.",
        ]
    )


def run(cache_dir: Path, output_dir: Path) -> dict:
    archive = download_dataset(cache_dir)
    frame, member, columns, members = load_source(archive)
    orders, diagnostics = prepare_orders(frame, columns)
    source = {
        "dataset_slug": DATASET_SLUG,
        "dataset_page": DATASET_PAGE,
        "member": member,
        "zip_sha256": _sha256(archive),
        "zip_bytes": int(archive.stat().st_size),
        "raw_member_rows": int(len(frame)),
        "archive_member_count": int(len(members)),
        "provenance_note": "Kaggle reprint of e-commerce/Amazon India sales data; not a Razorpay-owned source",
    }
    report, model = train_and_evaluate(orders, source, diagnostics)
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "report.json").write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    (output_dir / "report.md").write_text(report_markdown(report), encoding="utf-8")
    (output_dir / "model.json").write_text(json.dumps(model, separators=(",", ":")), encoding="utf-8")
    print(report_markdown(report))
    print("AMAZON_RETURN_RISK_METRICS " + json.dumps(report["heldout_test"], sort_keys=True))
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache-dir", type=Path, default=Path(".cache/amazon-return-risk"))
    parser.add_argument("--output-dir", type=Path, default=Path("artifacts/amazon-return-risk"))
    args = parser.parse_args()
    run(args.cache_dir, args.output_dir)


if __name__ == "__main__":
    main()
