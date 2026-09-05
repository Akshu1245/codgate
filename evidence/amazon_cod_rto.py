"""Build exact-COD return-to-seller evidence from a public Amazon India seller export.

Source: Kaggle `pranalibose/amazon-seller-order-status-prediction`.
The dataset card describes a real small leather seller (Boss Leathers) on the
Amazon India marketplace and includes an explicit `cod` field plus terminal
`Delivered to buyer` / `Returned to seller` outcomes.

This module deliberately keeps the final chronological test split untouched.
Hyperparameters and the decision threshold are selected using train/validation
only. Buyer names and order identifiers are never model features. Raw third-
party rows are never committed or emitted in artifacts.
"""

from __future__ import annotations

import argparse
import csv
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
from scipy.sparse import csr_matrix
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (
    average_precision_score,
    balanced_accuracy_score,
    f1_score,
    precision_score,
    recall_score,
    roc_auc_score,
)

DATASET_SLUG = "pranalibose/amazon-seller-order-status-prediction"
DATASET_PAGE = "https://www.kaggle.com/datasets/pranalibose/amazon-seller-order-status-prediction"
DATASET_DOWNLOAD = "https://www.kaggle.com/api/v1/datasets/download/pranalibose/amazon-seller-order-status-prediction"
RANDOM_SEED = 20260905
FEATURE_BUCKETS = 16384
POSITIVE = "RETURNED_TO_SELLER"
NEGATIVE = "DELIVERED_TO_BUYER"


@dataclass(frozen=True)
class Columns:
    order_no: str
    order_date: str
    buyer: str | None
    ship_city: str
    ship_state: str
    sku: str
    description: str
    quantity: str
    item_total: str
    shipping_fee: str
    cod: str
    order_status: str


def _norm_name(value: object) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value).strip().lower())


def _norm_text(value: object) -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return ""
    return re.sub(r"\s+", " ", str(value).strip().lower())


def _pick(columns: Iterable[str], aliases: Iterable[str], *, required: bool = True) -> str | None:
    by_norm = {_norm_name(column): column for column in columns}
    for alias in aliases:
        match = by_norm.get(_norm_name(alias))
        if match:
            return match
    if required:
        raise ValueError(f"missing required column; aliases={list(aliases)} found={list(columns)}")
    return None


def resolve_columns(frame: pd.DataFrame) -> Columns:
    cols = list(frame.columns)
    return Columns(
        order_no=_pick(cols, ["order_no", "order no", "order number", "amazon-order-id"]),
        order_date=_pick(cols, ["order_date", "order date", "purchase-date"]),
        buyer=_pick(cols, ["buyer", "buyer name"], required=False),
        ship_city=_pick(cols, ["ship_city", "ship city", "shipping city"]),
        ship_state=_pick(cols, ["ship_state", "ship state", "shipping state"]),
        sku=_pick(cols, ["sku", "sku - unique", "sku unique"]),
        description=_pick(cols, ["description", "product description", "product-name"]),
        quantity=_pick(cols, ["quantity", "qty"]),
        item_total=_pick(cols, ["item_total", "item total", "item-price", "amount"]),
        shipping_fee=_pick(cols, ["shipping_fee", "shipping fee", "shipping-price"]),
        cod=_pick(cols, ["cod", "cash on delivery", "payment mode", "payment_method"]),
        order_status=_pick(cols, ["order_status", "order status", "status"]),
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def download_dataset(cache_dir: Path) -> Path:
    cache_dir.mkdir(parents=True, exist_ok=True)
    destination = cache_dir / "amazon-seller-order-status.zip"
    if destination.exists() and destination.stat().st_size > 1024 and zipfile.is_zipfile(destination):
        return destination
    request = urllib.request.Request(
        DATASET_DOWNLOAD,
        headers={"User-Agent": "CodGate-exact-cod-evidence/1.0 (+https://github.com/Akshu1245/codgate)"},
    )
    with urllib.request.urlopen(request, timeout=180) as response, destination.open("wb") as handle:
        while True:
            chunk = response.read(1024 * 1024)
            if not chunk:
                break
            handle.write(chunk)
    if not zipfile.is_zipfile(destination):
        raise RuntimeError("Kaggle download is not a ZIP archive")
    return destination


def _read_member(archive: Path, member: str) -> pd.DataFrame:
    suffix = Path(member).suffix.lower()
    with zipfile.ZipFile(archive) as zf:
        payload = zf.read(member)
    if suffix in {".xlsx", ".xlsm"}:
        return pd.read_excel(io.BytesIO(payload), engine="openpyxl")
    if suffix == ".xls":
        # Modern public exports are normally xlsx. Keep the error explicit rather
        # than silently attempting to reinterpret a legacy binary workbook.
        raise RuntimeError("legacy .xls member is unsupported; source must be converted upstream")
    if suffix == ".csv":
        for encoding in ("utf-8", "utf-8-sig", "latin1"):
            try:
                return pd.read_csv(io.BytesIO(payload), encoding=encoding, low_memory=False)
            except UnicodeDecodeError:
                continue
    raise RuntimeError(f"unsupported source member: {member}")


def load_source(archive: Path) -> tuple[pd.DataFrame, str, Columns, list[dict]]:
    with zipfile.ZipFile(archive) as zf:
        members = [m for m in zf.namelist() if Path(m).suffix.lower() in {".xlsx", ".xlsm", ".xls", ".csv"}]
    diagnostics: list[dict] = []
    candidates: list[tuple[int, str, pd.DataFrame, Columns]] = []
    for member in sorted(members):
        try:
            frame = _read_member(archive, member)
            columns = resolve_columns(frame)
            terminal = frame[columns.order_status].map(_status).isin({POSITIVE, NEGATIVE})
            cod = frame[columns.cod].map(_is_cod)
            exact = int((terminal & cod).sum())
            diagnostics.append(
                {
                    "member": member,
                    "rows": int(len(frame)),
                    "terminal_rows": int(terminal.sum()),
                    "cod_terminal_rows": exact,
                    "columns": list(map(str, frame.columns)),
                }
            )
            candidates.append((exact, member, frame, columns))
        except Exception as exc:
            diagnostics.append({"member": member, "error": str(exc)})
    if not candidates:
        raise RuntimeError("no source member matched the published Amazon seller schema: " + json.dumps(diagnostics))
    candidates.sort(key=lambda item: (item[0], len(item[2])), reverse=True)
    exact, member, frame, columns = candidates[0]
    if exact < 50:
        raise RuntimeError("exact COD terminal population is too small: " + json.dumps(diagnostics))
    return frame, member, columns, diagnostics


def _status(value: object) -> str:
    text = re.sub(r"[^a-z]+", "_", _norm_text(value)).strip("_")
    if "returned" in text and "seller" in text:
        return POSITIVE
    if "delivered" in text and "buyer" in text:
        return NEGATIVE
    return text.upper()


def _is_cod(value: object) -> bool:
    text = _norm_text(value)
    if not text:
        return False
    if text in {"1", "true", "yes", "y", "cod"}:
        return True
    return "cash" in text and "delivery" in text


def _money(value: object) -> float:
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


def _parse_dates(series: pd.Series) -> pd.Series:
    parsed = pd.to_datetime(series, errors="coerce", dayfirst=True, format="mixed")
    if parsed.notna().mean() < 0.8:
        # Some Excel exports hold true datetime objects mixed with strings.
        parsed = pd.to_datetime(series.astype(str), errors="coerce", dayfirst=True, format="mixed")
    return parsed


def prepare_cod_terminal(frame: pd.DataFrame, columns: Columns) -> pd.DataFrame:
    work = frame.copy()
    work["_status"] = work[columns.order_status].map(_status)
    work["_cod"] = work[columns.cod].map(_is_cod)
    work = work[work["_cod"] & work["_status"].isin({POSITIVE, NEGATIVE})].copy()
    work["_order_date"] = _parse_dates(work[columns.order_date])
    work = work[work["_order_date"].notna()].copy()
    work["_order_key"] = work[columns.order_no].astype(str).str.strip()
    work = work[work["_order_key"].ne("") & work["_order_key"].ne("nan")].copy()
    work = work.sort_values(["_order_date", "_order_key"]).drop_duplicates("_order_key", keep="last")
    work["_target"] = (work["_status"] == POSITIVE).astype(int)
    work["_item_total"] = work[columns.item_total].map(_money)
    work["_shipping_fee"] = work[columns.shipping_fee].map(_money)
    if len(work) < 50 or work["_target"].nunique() != 2:
        raise RuntimeError(
            f"COD terminal data insufficient after cleanup: rows={len(work)} status={work['_status'].value_counts().to_dict()}"
        )
    return work.reset_index(drop=True)


def chronological_split(frame: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Split by contiguous time order; final test remains untouched."""
    ordered = frame.sort_values(["_order_date", "_order_key"]).reset_index(drop=True)
    n = len(ordered)
    train_end = int(n * 0.60)
    val_end = int(n * 0.80)
    train = ordered.iloc[:train_end].copy()
    validation = ordered.iloc[train_end:val_end].copy()
    test = ordered.iloc[val_end:].copy()
    for name, part in (("train", train), ("validation", validation), ("test", test)):
        if len(part) < 10:
            raise RuntimeError(f"{name} split too small: {len(part)}")
        if part["_target"].nunique() != 2:
            raise RuntimeError(f"{name} split lacks one outcome class; chronological evidence is invalid")
    return train, validation, test


def _bucket(value: float, bounds: list[float], prefix: str) -> str:
    for bound in bounds:
        if value <= bound:
            return f"{prefix}=le_{int(bound)}"
    return f"{prefix}=gt_{int(bounds[-1])}"


def row_tokens(row: pd.Series, columns: Columns) -> list[str]:
    state = _norm_text(row.get(columns.ship_state))
    city = _norm_text(row.get(columns.ship_city))
    sku = _norm_text(row.get(columns.sku))
    description = _norm_text(row.get(columns.description))
    quantity = max(0, int(round(_money(row.get(columns.quantity)))))
    item_total = _money(row.get(columns.item_total))
    shipping_fee = _money(row.get(columns.shipping_fee))

    tokens = ["bias=1", "cod=1"]
    if state:
        tokens.append(f"state={state[:80]}")
    if city:
        tokens.append(f"city={city[:100]}")
    if sku:
        tokens.append(f"sku={sku[:120]}")
    if state and sku:
        tokens.append(f"state_sku={state[:50]}|{sku[:80]}")
    if city and sku:
        tokens.append(f"city_sku={city[:60]}|{sku[:80]}")

    words = re.findall(r"[a-z0-9]+", description)
    words = [word for word in words if len(word) >= 2][:40]
    for word in words:
        tokens.append(f"desc={word}")
    for left, right in zip(words, words[1:]):
        tokens.append(f"desc2={left}_{right}")

    tokens.append(f"qty={min(quantity, 6)}" if quantity else "qty=missing")
    tokens.append(_bucket(item_total, [250, 400, 500, 700, 1000, 1500, 2500, 5000], "amount"))
    tokens.append(_bucket(shipping_fee, [0, 40, 60, 80, 100, 150, 250], "shipfee"))

    dt = row.get("_order_date")
    if pd.notna(dt):
        tokens.extend(
            [
                f"dow={int(dt.dayofweek)}",
                f"month={int(dt.month)}",
                f"dom_band={int((dt.day - 1) // 7)}",
            ]
        )
        if state:
            tokens.append(f"state_dow={state[:50]}|{int(dt.dayofweek)}")
    return tokens


def hash_token(token: str, buckets: int = FEATURE_BUCKETS) -> tuple[int, float]:
    digest = hashlib.sha256(token.encode("utf-8")).digest()
    index = int.from_bytes(digest[:8], "big") % buckets
    sign = 1.0 if digest[8] & 1 else -1.0
    return index, sign


def hashed_matrix(frame: pd.DataFrame, columns: Columns, buckets: int = FEATURE_BUCKETS) -> csr_matrix:
    data: list[float] = []
    indices: list[int] = []
    indptr = [0]
    for _, row in frame.iterrows():
        accum: dict[int, float] = {}
        for token in row_tokens(row, columns):
            idx, sign = hash_token(token, buckets)
            accum[idx] = accum.get(idx, 0.0) + sign
        for idx in sorted(accum):
            value = accum[idx]
            if value:
                indices.append(idx)
                data.append(value)
        indptr.append(len(indices))
    return csr_matrix((data, indices, indptr), shape=(len(frame), buckets), dtype=np.float64)


def _choose_threshold(y_true: np.ndarray, probabilities: np.ndarray) -> tuple[float, dict]:
    best: tuple[float, float, float, float] | None = None
    for raw in np.linspace(0.02, 0.80, 157):
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


def _metrics(frame: pd.DataFrame, probabilities: np.ndarray, threshold: float) -> dict:
    y = frame["_target"].to_numpy(dtype=int)
    pred = probabilities >= threshold
    tp = int(np.sum((pred == 1) & (y == 1)))
    fp = int(np.sum((pred == 1) & (y == 0)))
    fn = int(np.sum((pred == 0) & (y == 1)))
    tn = int(np.sum((pred == 0) & (y == 0)))
    fp_mask = (pred == 1) & (y == 0)
    fn_mask = (pred == 0) & (y == 1)
    block = {
        "n": int(len(y)),
        "positives": int(np.sum(y == 1)),
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
        # Real monetary quantities from the source rows. These are exposure
        # measures, not invented profit-loss assumptions.
        "false_positive_gmv_at_risk_inr": round(float(frame.loc[fp_mask, "_item_total"].sum()), 2),
        "false_positive_source_shipping_fee_inr": round(float(frame.loc[fp_mask, "_shipping_fee"].sum()), 2),
        "missed_return_gmv_inr": round(float(frame.loc[fn_mask, "_item_total"].sum()), 2),
        "missed_return_source_shipping_fee_inr": round(float(frame.loc[fn_mask, "_shipping_fee"].sum()), 2),
    }
    return block


def _bootstrap_ci(frame: pd.DataFrame, probabilities: np.ndarray, threshold: float, rounds: int = 2000) -> dict:
    y = frame["_target"].to_numpy(dtype=int)
    rng = np.random.default_rng(RANDOM_SEED)
    precision_values: list[float] = []
    recall_values: list[float] = []
    for _ in range(rounds):
        idx = rng.integers(0, len(y), size=len(y))
        ys = y[idx]
        if len(np.unique(ys)) < 2:
            continue
        ps = probabilities[idx] >= threshold
        precision_values.append(float(precision_score(ys, ps, zero_division=0)))
        recall_values.append(float(recall_score(ys, ps, zero_division=0)))
    def interval(values: list[float]) -> list[float]:
        return [float(np.quantile(values, 0.025)), float(np.quantile(values, 0.975))]
    return {"precision": interval(precision_values), "recall": interval(recall_values)}


def train_and_evaluate(frame: pd.DataFrame, columns: Columns, source: dict) -> tuple[dict, dict, pd.DataFrame]:
    train, validation, test = chronological_split(frame)
    x_train = hashed_matrix(train, columns)
    x_val = hashed_matrix(validation, columns)
    x_test = hashed_matrix(test, columns)
    y_train = train["_target"].to_numpy(dtype=int)
    y_val = validation["_target"].to_numpy(dtype=int)

    trials: list[dict] = []
    best: tuple[float, float, float, float, float, str, LogisticRegression] | None = None
    for class_weight in (None, "balanced"):
        for c in (0.05, 0.1, 0.25, 0.5, 1.0, 2.0, 5.0, 10.0):
            model = LogisticRegression(
                C=c,
                class_weight=class_weight,
                max_iter=3000,
                solver="liblinear",
                random_state=RANDOM_SEED,
            )
            model.fit(x_train, y_train)
            val_prob = model.predict_proba(x_val)[:, 1]
            threshold, threshold_metrics = _choose_threshold(y_val, val_prob)
            ap = float(average_precision_score(y_val, val_prob))
            auc = float(roc_auc_score(y_val, val_prob))
            trial = {
                "C": c,
                "class_weight": class_weight or "none",
                "validation_average_precision": ap,
                "validation_roc_auc": auc,
                "threshold": threshold,
                **{f"validation_{k}": v for k, v in threshold_metrics.items()},
            }
            trials.append(trial)
            key = (
                ap,
                threshold_metrics["f1"],
                threshold_metrics["recall"],
                threshold_metrics["precision"],
                -abs(c - 1.0),
                class_weight or "none",
                model,
            )
            if best is None or key[:-1] > best[:-1]:
                best = key
    assert best is not None
    _ap, _f1, _recall, _precision, _c_tie, _weight_name, model = best
    selected = next(
        trial
        for trial in trials
        if trial["validation_average_precision"] == _ap
        and trial["validation_f1"] == _f1
        and trial["validation_recall"] == _recall
        and trial["validation_precision"] == _precision
        and (-(abs(float(trial["C"]) - 1.0))) == _c_tie
        and trial["class_weight"] == _weight_name
    )
    threshold = float(selected["threshold"])
    test_prob = model.predict_proba(x_test)[:, 1]
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
        "claim": "Exact-COD real seller prototype evidence; not Amazon/Razorpay-wide production accuracy",
        "source": source,
        "label_definition": {
            "positive": "Returned to seller",
            "negative": "Delivered to buyer",
            "population_filter": "cod == Cash On Delivery",
        },
        "privacy_and_leakage": {
            "buyer_used_as_feature": False,
            "order_no_used_as_feature": False,
            "order_status_used_as_feature": False,
            "post_outcome_fields_used": False,
            "raw_rows_embedded": False,
        },
        "feature_policy": {
            "features": [
                "order date calendar features",
                "ship city",
                "ship state",
                "SKU",
                "product description tokens/bigrams",
                "quantity",
                "item total",
                "shipping fee",
            ],
            "hash_buckets": FEATURE_BUCKETS,
        },
        "dataset": {
            "cod_terminal_orders": int(len(frame)),
            "returned_to_seller": int(frame["_target"].sum()),
            "delivered_to_buyer": int((frame["_target"] == 0).sum()),
            "return_prevalence": float(frame["_target"].mean()),
            "date_range": {
                "start": frame["_order_date"].min().isoformat(),
                "end": frame["_order_date"].max().isoformat(),
            },
        },
        "split": {
            "method": "chronological 60/20/20; no shuffle, SMOTE, duplication or augmentation",
            "train": split_block(train),
            "validation": split_block(validation),
            "test": split_block(test),
        },
        "selection": {
            "criterion": "validation average precision; threshold maximizes validation F1; final test never consulted",
            "selected": selected,
            "trials": trials,
        },
        "heldout_test": heldout,
        "heldout_ci95_bootstrap": ci,
        "false_positive_cost_disclosure": {
            "realized_profit_loss_identifiable_from_source": False,
            "why": "The public seller export has order value and shipping fee but not contribution margin or prepaid-conversion behavior.",
            "reported_instead": [
                "false_positive_gmv_at_risk_inr",
                "false_positive_source_shipping_fee_inr",
            ],
        },
        "limitations": [
            "This is one Boss Leathers Amazon India seller export, not Amazon-wide or Razorpay-wide traffic.",
            "Only COD orders with terminal Delivered-to-buyer or Returned-to-seller labels are evaluated.",
            "The final chronological test split is untouched by hyperparameter and threshold selection.",
            "A real false-positive profit cost cannot be inferred without margin and prepaid-conversion data; the report uses source-derived GMV exposure instead of inventing a rupee loss constant.",
        ],
    }

    model_artifact = {
        "schema_version": 1,
        "family": "hashed_logistic_regression",
        "feature_buckets": FEATURE_BUCKETS,
        "threshold": threshold,
        "intercept": float(model.intercept_[0]),
        "coefficients": [float(v) for v in model.coef_[0]],
        "source_zip_sha256": source["zip_sha256"],
        "evaluation_scope": "COD only: Delivered to buyer vs Returned to seller",
    }

    predictions = pd.DataFrame(
        {
            "row_sha256": [hashlib.sha256(v.encode("utf-8")).hexdigest() for v in test["_order_key"].astype(str)],
            "actual_rto": test["_target"].astype(int).to_numpy(),
            "probability": test_prob,
            "predicted_rto": (test_prob >= threshold).astype(int),
            "item_total_inr": test["_item_total"].to_numpy(),
            "shipping_fee_inr": test["_shipping_fee"].to_numpy(),
        }
    )
    return report, model_artifact, predictions


def report_markdown(report: dict) -> str:
    ds = report["dataset"]
    test = report["heldout_test"]
    ci = report["heldout_ci95_bootstrap"]
    source = report["source"]
    return "\n".join(
        [
            "# CodGate exact COD RTO evidence",
            "",
            "Real-data prototype evidence from a public Boss Leathers Amazon India seller export; not Amazon/Razorpay-wide production accuracy.",
            "",
            f"- Source: {source['dataset_page']}",
            f"- Source ZIP SHA-256: `{source['zip_sha256']}`",
            f"- Source member: `{source['member']}`",
            f"- COD terminal orders: {ds['cod_terminal_orders']}",
            f"- Returned to seller: {ds['returned_to_seller']}",
            f"- Delivered to buyer: {ds['delivered_to_buyer']}",
            f"- Final held-out n: {test['n']} ({test['positives']} returns)",
            f"- Precision: {test['precision']:.2%} (bootstrap 95% {ci['precision'][0]:.2%}–{ci['precision'][1]:.2%})",
            f"- Recall: {test['recall']:.2%} (bootstrap 95% {ci['recall'][0]:.2%}–{ci['recall'][1]:.2%})",
            f"- F1: {test['f1']:.4f}",
            f"- PR-AUC: {test['average_precision']:.4f}",
            f"- ROC-AUC: {test['roc_auc']:.4f}",
            f"- Confusion: TP {test['tp']} · FP {test['fp']} · FN {test['fn']} · TN {test['tn']}",
            f"- False-positive COD GMV at risk: ₹{test['false_positive_gmv_at_risk_inr']:.2f}",
            f"- Missed-return source shipping fee: ₹{test['missed_return_source_shipping_fee_inr']:.2f}",
            "",
            "False-positive realized profit loss is not claimed because the source does not contain margin or prepaid-conversion behavior.",
        ]
    )


def run(cache_dir: Path, output_dir: Path) -> dict:
    archive = download_dataset(cache_dir)
    frame, member, columns, diagnostics = load_source(archive)
    prepared = prepare_cod_terminal(frame, columns)
    source = {
        "dataset_slug": DATASET_SLUG,
        "dataset_page": DATASET_PAGE,
        "download_url": DATASET_DOWNLOAD,
        "member": member,
        "zip_sha256": _sha256(archive),
        "zip_bytes": int(archive.stat().st_size),
        "source_rows": int(len(frame)),
        "license": "CC0: Public Domain (per Kaggle dataset card)",
        "dataset_card_context": "Boss Leathers Amazon India seller orders; objective is Delivered to buyer vs Returned to seller",
    }
    report, model, predictions = train_and_evaluate(prepared, columns, source)
    report["source_diagnostics"] = diagnostics
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "report.json").write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    (output_dir / "report.md").write_text(report_markdown(report), encoding="utf-8")
    (output_dir / "model.json").write_text(json.dumps(model, separators=(",", ":")), encoding="utf-8")
    predictions.to_csv(output_dir / "heldout_predictions.csv", index=False, quoting=csv.QUOTE_MINIMAL)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache-dir", type=Path, default=Path(".cache/amazon-cod-rto"))
    parser.add_argument("--output-dir", type=Path, default=Path("artifacts/amazon-cod-rto"))
    args = parser.parse_args()
    report = run(args.cache_dir, args.output_dir)
    print(report_markdown(report))
    print("SOURCE_DIAGNOSTICS " + json.dumps(report["source_diagnostics"], default=str))
    print("AMAZON_COD_RTO_METRICS " + json.dumps(report["heldout_test"], sort_keys=True))


if __name__ == "__main__":
    main()
