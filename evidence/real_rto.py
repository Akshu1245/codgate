"""Train and evaluate a leakage-bounded RTO detector on public Meesho seller data.

This module is evidence tooling, not the CodGate runtime policy. It downloads the
public Kaggle dataset `sahilr05/meesho-orders`, keeps only terminal DELIVERED and
RTO_COMPLETE outcomes, uses only order-time fields, performs a chronological
train/validation/test split, tunes the decision threshold on validation data,
and reports untouched held-out metrics.

Raw third-party data is never committed to this repository. The Kaggle page
currently labels the data files "Data files © Original Authors"; this pipeline
therefore downloads the source for evaluation and exports only derived metrics,
model coefficients and de-identified predictions.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
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

DATASET_SLUG = "sahilr05/meesho-orders"
DATASET_PAGE = "https://www.kaggle.com/datasets/sahilr05/meesho-orders"
DATASET_DOWNLOAD = "https://www.kaggle.com/api/v1/datasets/download/sahilr05/meesho-orders"
FEATURE_BUCKETS = 4096
RANDOM_SEED = 20260905
TERMINAL_DELIVERED = "DELIVERED"
TERMINAL_RTO = "RTO_COMPLETE"
FORBIDDEN_FEATURE_TERMS = {
    "reason",
    "credit",
    "settlement",
    "return",
    "rto",
    "status",
    "delivered",
    "dispatch",
    "shippingcharge",
    "returnshipping",
    "paymentdate",
    "finalsettlement",
}


@dataclass(frozen=True)
class ResolvedColumns:
    status: str
    order_date: str
    order_id: str | None
    state: str | None
    pincode: str | None
    product: str | None
    sku: str | None
    size: str | None
    quantity: str | None
    price: str | None

    @property
    def feature_columns(self) -> list[str]:
        values = [
            self.order_date,
            self.state,
            self.pincode,
            self.product,
            self.sku,
            self.size,
            self.quantity,
            self.price,
        ]
        return [value for value in values if value]


def _norm_name(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value).strip().lower())


def _norm_text(value: object) -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return ""
    return re.sub(r"\s+", " ", str(value).strip().lower())


def _norm_status(value: object) -> str:
    text = _norm_text(value).upper()
    return re.sub(r"[^A-Z0-9]+", "_", text).strip("_")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _pick(columns: Iterable[str], aliases: Iterable[str], required: bool = False) -> str | None:
    by_norm = {_norm_name(column): column for column in columns}
    for alias in aliases:
        found = by_norm.get(_norm_name(alias))
        if found:
            return found
    if required:
        raise ValueError(f"Required column missing. Tried aliases: {list(aliases)}; found: {list(columns)}")
    return None


def resolve_columns(frame: pd.DataFrame) -> ResolvedColumns:
    columns = list(frame.columns)
    return ResolvedColumns(
        status=_pick(columns, ["Reason for Credit Entry", "order status", "status"], required=True),
        order_date=_pick(columns, ["Order Date", "order_date", "ordered date"], required=True),
        order_id=_pick(columns, ["Sub Order No", "Sub Order Number", "sub_order_no", "order id"]),
        state=_pick(columns, ["Customer State", "Cust State", "Reseller State", "state"]),
        pincode=_pick(columns, ["Customer Pincode", "Cust Pincode", "Pincode", "Pin Code"]),
        product=_pick(columns, ["Product Name", "product"]),
        sku=_pick(columns, ["SKU", "Supplier SKU", "Product SKU"]),
        size=_pick(columns, ["Size", "product size"]),
        quantity=_pick(columns, ["Quantity", "Qty"]),
        price=_pick(
            columns,
            [
                "Supplier Listed Price",
                "Supplier Listed Price (Incl. GST)",
                "Product Price",
                "Unit Price",
                "Selling Price",
            ],
        ),
    )


def assert_no_leakage(columns: ResolvedColumns) -> None:
    if columns.status in columns.feature_columns:
        raise AssertionError("Outcome/status column leaked into model features")
    for column in columns.feature_columns:
        normalized = _norm_name(column)
        hits = [term for term in FORBIDDEN_FEATURE_TERMS if term in normalized]
        if hits:
            raise AssertionError(f"Potential post-outcome leakage field {column!r}: {hits}")


def download_dataset(cache_dir: Path) -> Path:
    cache_dir.mkdir(parents=True, exist_ok=True)
    destination = cache_dir / "meesho-orders.zip"
    if destination.exists() and destination.stat().st_size > 1024:
        return destination

    request = urllib.request.Request(
        DATASET_DOWNLOAD,
        headers={"User-Agent": "CodGate-real-evidence/1.0 (+https://github.com/Akshu1245/codgate)"},
    )
    with urllib.request.urlopen(request, timeout=180) as response, destination.open("wb") as handle:
        while True:
            chunk = response.read(1024 * 1024)
            if not chunk:
                break
            handle.write(chunk)

    if not zipfile.is_zipfile(destination):
        raise RuntimeError(f"Kaggle download is not a ZIP archive: {destination}")
    return destination


def _read_csv_from_zip(archive: Path, member: str) -> pd.DataFrame:
    with zipfile.ZipFile(archive) as zf, zf.open(member) as handle:
        try:
            return pd.read_csv(handle, low_memory=False)
        except UnicodeDecodeError:
            handle.seek(0)
            return pd.read_csv(handle, low_memory=False, encoding="latin1")


def load_source_frame(archive: Path) -> tuple[pd.DataFrame, str]:
    with zipfile.ZipFile(archive) as zf:
        members = [name for name in zf.namelist() if name.lower().endswith(".csv")]
    if not members:
        raise RuntimeError("No CSV files found inside Kaggle dataset archive")

    failures: list[str] = []
    for member in members:
        try:
            frame = _read_csv_from_zip(archive, member)
            resolve_columns(frame)
            return frame, member
        except Exception as exc:  # discovery intentionally tries each CSV
            failures.append(f"{member}: {exc}")
    raise RuntimeError("No CSV matched the required Meesho schema. " + " | ".join(failures))


def prepare_terminal_orders(frame: pd.DataFrame, columns: ResolvedColumns) -> pd.DataFrame:
    work = frame.copy()
    work["_status"] = work[columns.status].map(_norm_status)
    work = work[work["_status"].isin({TERMINAL_DELIVERED, TERMINAL_RTO})].copy()
    if work.empty:
        values = frame[columns.status].dropna().astype(str).value_counts().head(20).to_dict()
        raise RuntimeError(f"No terminal DELIVERED/RTO_COMPLETE rows found. Top statuses: {values}")

    work["_order_date"] = pd.to_datetime(work[columns.order_date], errors="coerce", dayfirst=True)
    work = work[work["_order_date"].notna()].copy()
    if len(work) < 200:
        raise RuntimeError(f"Too few terminal rows after date parsing: {len(work)}")

    if columns.order_id:
        # The payment export can contain repeated snapshots. One sub-order should
        # contribute one label only; keeping the last terminal row avoids duplicate
        # weight without exposing the identifier downstream.
        work["_order_id_text"] = work[columns.order_id].astype(str).str.strip()
        work = work.sort_values("_order_date").drop_duplicates("_order_id_text", keep="last")

    work["_target"] = (work["_status"] == TERMINAL_RTO).astype(int)
    if work["_target"].nunique() != 2:
        raise RuntimeError("Terminal dataset does not contain both delivered and RTO labels")
    return work.sort_values("_order_date").reset_index(drop=True)


def _to_number(value: object) -> float | None:
    if value is None:
        return None
    text = re.sub(r"[^0-9.\-]+", "", str(value))
    if not text or text in {"-", ".", "-."}:
        return None
    try:
        number = float(text)
    except ValueError:
        return None
    return number if math.isfinite(number) else None


def _price_bucket(value: object) -> str:
    number = _to_number(value)
    if number is None or number < 0:
        return "missing"
    bounds = [100, 250, 500, 750, 1000, 1500, 2500, 5000]
    for bound in bounds:
        if number <= bound:
            return f"le_{bound}"
    return "gt_5000"


def _quantity_bucket(value: object) -> str:
    number = _to_number(value)
    if number is None or number <= 0:
        return "missing"
    integer = int(round(number))
    if integer >= 5:
        return "5_plus"
    return str(integer)


def row_tokens(row: pd.Series, columns: ResolvedColumns) -> list[str]:
    tokens: list[str] = ["bias=1"]

    if columns.state:
        state = _norm_text(row.get(columns.state))
        if state:
            tokens.append(f"state={state}")
    if columns.pincode:
        digits = re.sub(r"\D", "", str(row.get(columns.pincode) or ""))
        if len(digits) >= 3:
            tokens.append(f"pin3={digits[:3]}")
    if columns.sku:
        sku = _norm_text(row.get(columns.sku))
        if sku:
            tokens.append(f"sku={sku[:80]}")
    if columns.size:
        size = _norm_text(row.get(columns.size))
        if size:
            tokens.append(f"size={size[:30]}")
    if columns.product:
        product = _norm_text(row.get(columns.product))
        words = re.findall(r"[a-z0-9]+", product)
        for word in words[:12]:
            if len(word) >= 2:
                tokens.append(f"product_word={word}")
    if columns.quantity:
        tokens.append(f"qty={_quantity_bucket(row.get(columns.quantity))}")
    if columns.price:
        tokens.append(f"price={_price_bucket(row.get(columns.price))}")

    dt = row.get("_order_date")
    if pd.notna(dt):
        tokens.append(f"dow={int(dt.dayofweek)}")
        tokens.append(f"hour={int(dt.hour)}")
        tokens.append(f"month={int(dt.month)}")
    return tokens


def hash_token(token: str, buckets: int = FEATURE_BUCKETS) -> tuple[int, float]:
    digest = hashlib.sha256(token.encode("utf-8")).digest()
    index = int.from_bytes(digest[:8], "big") % buckets
    sign = 1.0 if digest[8] & 1 else -1.0
    return index, sign


def hashed_matrix(frame: pd.DataFrame, columns: ResolvedColumns, buckets: int = FEATURE_BUCKETS) -> csr_matrix:
    data: list[float] = []
    indices: list[int] = []
    indptr = [0]
    for _, row in frame.iterrows():
        accum: dict[int, float] = {}
        for token in row_tokens(row, columns):
            index, sign = hash_token(token, buckets=buckets)
            accum[index] = accum.get(index, 0.0) + sign
        for index in sorted(accum):
            value = accum[index]
            if value:
                indices.append(index)
                data.append(value)
        indptr.append(len(indices))
    return csr_matrix((data, indices, indptr), shape=(len(frame), buckets), dtype=np.float64)


def chronological_split(frame: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    n = len(frame)
    train_end = max(1, int(n * 0.60))
    val_end = max(train_end + 1, int(n * 0.80))
    train = frame.iloc[:train_end].copy()
    val = frame.iloc[train_end:val_end].copy()
    test = frame.iloc[val_end:].copy()
    for name, split in [("train", train), ("validation", val), ("test", test)]:
        if len(split) < 50:
            raise RuntimeError(f"{name} split too small: {len(split)}")
        if split["_target"].nunique() != 2:
            raise RuntimeError(f"{name} split lacks one class; chronological evaluation is not valid")
    return train, val, test


def choose_threshold(y_true: np.ndarray, probabilities: np.ndarray) -> tuple[float, dict]:
    candidates = np.linspace(0.05, 0.95, 181)
    best: tuple[float, float, float, float] | None = None
    for threshold in candidates:
        predicted = probabilities >= threshold
        precision = precision_score(y_true, predicted, zero_division=0)
        recall = recall_score(y_true, predicted, zero_division=0)
        f1 = f1_score(y_true, predicted, zero_division=0)
        candidate = (float(f1), float(recall), float(precision), float(threshold))
        if best is None or candidate > best:
            best = candidate
    assert best is not None
    f1, recall, precision, threshold = best
    return threshold, {"precision": precision, "recall": recall, "f1": f1}


def _metric_block(y_true: np.ndarray, probabilities: np.ndarray, threshold: float) -> dict:
    predicted = probabilities >= threshold
    tp = int(np.sum((predicted == 1) & (y_true == 1)))
    fp = int(np.sum((predicted == 1) & (y_true == 0)))
    fn = int(np.sum((predicted == 0) & (y_true == 1)))
    tn = int(np.sum((predicted == 0) & (y_true == 0)))
    block = {
        "n": int(len(y_true)),
        "positives": int(np.sum(y_true == 1)),
        "prevalence": float(np.mean(y_true)),
        "threshold": float(threshold),
        "precision": float(precision_score(y_true, predicted, zero_division=0)),
        "recall": float(recall_score(y_true, predicted, zero_division=0)),
        "f1": float(f1_score(y_true, predicted, zero_division=0)),
        "balanced_accuracy": float(balanced_accuracy_score(y_true, predicted)),
        "average_precision": float(average_precision_score(y_true, probabilities)),
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "tn": tn,
    }
    if len(np.unique(y_true)) == 2:
        block["roc_auc"] = float(roc_auc_score(y_true, probabilities))
    return block


def bootstrap_ci(
    y_true: np.ndarray,
    probabilities: np.ndarray,
    threshold: float,
    *,
    rounds: int = 400,
) -> dict:
    rng = np.random.default_rng(RANDOM_SEED)
    values = {"precision": [], "recall": []}
    n = len(y_true)
    for _ in range(rounds):
        sample = rng.integers(0, n, size=n)
        ys = y_true[sample]
        ps = probabilities[sample]
        pred = ps >= threshold
        if np.any(pred):
            values["precision"].append(float(precision_score(ys, pred, zero_division=0)))
        if np.any(ys == 1):
            values["recall"].append(float(recall_score(ys, pred, zero_division=0)))
    output = {}
    for key, series in values.items():
        if series:
            output[key] = [float(np.quantile(series, 0.025)), float(np.quantile(series, 0.975))]
    return output


def _date_range(frame: pd.DataFrame) -> dict:
    return {
        "start": frame["_order_date"].min().isoformat(),
        "end": frame["_order_date"].max().isoformat(),
    }


def _deidentified_predictions(frame: pd.DataFrame, y: np.ndarray, prob: np.ndarray, threshold: float) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "order_date": frame["_order_date"].dt.strftime("%Y-%m-%dT%H:%M:%S").to_numpy(),
            "actual_rto": y.astype(int),
            "predicted_rto": (prob >= threshold).astype(int),
            "rto_probability": np.round(prob, 6),
        }
    )


def train_and_evaluate(frame: pd.DataFrame, columns: ResolvedColumns, source_manifest: dict) -> tuple[dict, dict, pd.DataFrame]:
    assert_no_leakage(columns)
    orders = prepare_terminal_orders(frame, columns)
    train, val, test = chronological_split(orders)

    x_train = hashed_matrix(train, columns)
    x_val = hashed_matrix(val, columns)
    x_test = hashed_matrix(test, columns)
    y_train = train["_target"].to_numpy(dtype=int)
    y_val = val["_target"].to_numpy(dtype=int)
    y_test = test["_target"].to_numpy(dtype=int)

    model = LogisticRegression(
        solver="liblinear",
        class_weight="balanced",
        C=1.0,
        max_iter=1000,
        random_state=RANDOM_SEED,
    )
    model.fit(x_train, y_train)
    val_prob = model.predict_proba(x_val)[:, 1]
    threshold, validation_choice = choose_threshold(y_val, val_prob)
    test_prob = model.predict_proba(x_test)[:, 1]

    report = {
        "schema_version": 1,
        "claim": "Public real-data RTO evidence; not Razorpay production accuracy",
        "loss_class": "Return to Origin (RTO)",
        "source": source_manifest,
        "label_definition": {
            "positive": TERMINAL_RTO,
            "negative": TERMINAL_DELIVERED,
            "excluded": "All non-terminal/intermediate statuses, including CANCELLED/SHIPPED/RTO_INITIATED/RTO_LOCKED when present",
        },
        "feature_policy": {
            "principle": "Only fields available at/near order creation; no settlement, delivery or final-status fields",
            "resolved_columns": {key: value for key, value in columns.__dict__.items() if key != "status" and value},
            "hashed_feature_buckets": FEATURE_BUCKETS,
            "forbidden_terms": sorted(FORBIDDEN_FEATURE_TERMS),
        },
        "dataset": {
            "terminal_orders": int(len(orders)),
            "terminal_rto": int(orders["_target"].sum()),
            "terminal_delivered": int((orders["_target"] == 0).sum()),
            "prevalence": float(orders["_target"].mean()),
            "date_range": _date_range(orders),
        },
        "split": {
            "method": "chronological 60/20/20; no shuffle",
            "train": {"n": len(train), "rto": int(train["_target"].sum()), "date_range": _date_range(train)},
            "validation": {"n": len(val), "rto": int(val["_target"].sum()), "date_range": _date_range(val)},
            "test": {"n": len(test), "rto": int(test["_target"].sum()), "date_range": _date_range(test)},
        },
        "model": {
            "family": "hashed logistic regression",
            "class_weight": "balanced",
            "random_seed": RANDOM_SEED,
            "threshold_selection": "maximize F1 on validation split only",
            "selected_threshold": float(threshold),
            "validation_at_threshold": validation_choice,
        },
        "heldout_test": _metric_block(y_test, test_prob, threshold),
        "heldout_ci95_bootstrap": bootstrap_ci(y_test, test_prob, threshold),
    }

    model_artifact = {
        "schema_version": 1,
        "model_family": "sha256-hashed-logistic-regression",
        "feature_buckets": FEATURE_BUCKETS,
        "intercept": float(model.intercept_[0]),
        "coefficients": [round(float(value), 10) for value in model.coef_[0]],
        "threshold": float(threshold),
        "feature_columns": report["feature_policy"]["resolved_columns"],
        "source_zip_sha256": source_manifest["zip_sha256"],
        "training_note": "Derived from terminal DELIVERED vs RTO_COMPLETE rows; see report.json for split and metrics.",
    }
    predictions = _deidentified_predictions(test, y_test, test_prob, threshold)
    return report, model_artifact, predictions


def report_markdown(report: dict) -> str:
    heldout = report["heldout_test"]
    ci = report.get("heldout_ci95_bootstrap", {})
    p_ci = ci.get("precision", [None, None])
    r_ci = ci.get("recall", [None, None])
    source = report["source"]
    dataset = report["dataset"]
    split = report["split"]
    return f"""# CodGate real RTO evidence\n\nThis report is generated from a third-party public Meesho seller dataset. It is **real-data prototype evidence, not Razorpay production accuracy**.\n\n## Provenance\n- Dataset page: {source['dataset_page']}\n- Kaggle slug: `{source['dataset_slug']}`\n- Download ZIP SHA-256: `{source['zip_sha256']}`\n- CSV member: `{source['csv_member']}`\n- Raw ZIP bytes: {source['zip_bytes']}\n- Raw data is not committed because the dataset page states `Data files © Original Authors`.\n\n## Label and leakage controls\n- Positive: `{TERMINAL_RTO}`\n- Negative: `{TERMINAL_DELIVERED}`\n- Intermediate/non-terminal rows are excluded.\n- Only order-time fields are whitelisted. Settlement, delivery, return-charge and final-status fields are forbidden.\n- Split is chronological 60/20/20 with no shuffle; threshold is selected on validation only.\n\n## Data used\n- Terminal orders: {dataset['terminal_orders']:,}\n- RTO: {dataset['terminal_rto']:,}\n- Delivered: {dataset['terminal_delivered']:,}\n- RTO prevalence: {dataset['prevalence'] * 100:.2f}%\n- Date range: {dataset['date_range']['start']} → {dataset['date_range']['end']}\n- Train / validation / test: {split['train']['n']:,} / {split['validation']['n']:,} / {split['test']['n']:,}\n\n## Untouched held-out test\n- n: {heldout['n']:,}; positives: {heldout['positives']:,}\n- Threshold: {heldout['threshold']:.3f}\n- Precision: {heldout['precision'] * 100:.2f}%{f" (95% bootstrap {p_ci[0] * 100:.2f}%–{p_ci[1] * 100:.2f}%)" if p_ci[0] is not None else ''}\n- Recall: {heldout['recall'] * 100:.2f}%{f" (95% bootstrap {r_ci[0] * 100:.2f}%–{r_ci[1] * 100:.2f}%)" if r_ci[0] is not None else ''}\n- F1: {heldout['f1']:.4f}\n- Average precision (PR-AUC): {heldout['average_precision']:.4f}\n- ROC-AUC: {heldout.get('roc_auc', float('nan')):.4f}\n- Balanced accuracy: {heldout['balanced_accuracy']:.4f}\n- Confusion matrix: TP {heldout['tp']} · FP {heldout['fp']} · FN {heldout['fn']} · TN {heldout['tn']}\n\nThese values are generated by the pipeline; they are not hand-entered into the report.\n"""


def run(cache_dir: Path, output_dir: Path) -> dict:
    archive = download_dataset(cache_dir)
    frame, member = load_source_frame(archive)
    columns = resolve_columns(frame)
    assert_no_leakage(columns)

    source_manifest = {
        "dataset_slug": DATASET_SLUG,
        "dataset_page": DATASET_PAGE,
        "download_url": DATASET_DOWNLOAD,
        "zip_sha256": _sha256(archive),
        "zip_bytes": archive.stat().st_size,
        "csv_member": member,
        "source_rows": int(len(frame)),
        "dataset_page_claim": "Kaggle page describes it as real Meesho supplier order data",
        "dataset_page_license_text": "Data files © Original Authors",
    }
    report, model_artifact, predictions = train_and_evaluate(frame, columns, source_manifest)

    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "report.json").write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    (output_dir / "report.md").write_text(report_markdown(report), encoding="utf-8")
    (output_dir / "model.json").write_text(json.dumps(model_artifact, separators=(",", ":")), encoding="utf-8")
    predictions.to_csv(output_dir / "heldout_predictions.csv", index=False, quoting=csv.QUOTE_MINIMAL)
    (output_dir / "source-manifest.json").write_text(json.dumps(source_manifest, indent=2, sort_keys=True), encoding="utf-8")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache-dir", type=Path, default=Path(".cache/real-rto"))
    parser.add_argument("--output-dir", type=Path, default=Path("artifacts/real-rto-evidence"))
    args = parser.parse_args()
    report = run(args.cache_dir, args.output_dir)
    heldout = report["heldout_test"]
    print(report_markdown(report))
    print(
        "REAL_RTO_METRICS "
        + json.dumps(
            {
                "n": heldout["n"],
                "precision": heldout["precision"],
                "recall": heldout["recall"],
                "f1": heldout["f1"],
                "average_precision": heldout["average_precision"],
                "tp": heldout["tp"],
                "fp": heldout["fp"],
                "fn": heldout["fn"],
                "tn": heldout["tn"],
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
