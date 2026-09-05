"""Robust entrypoint for building CodGate's external Meesho RTO evidence.

The source archive is intentionally used as-is: no synthetic expansion, SMOTE,
row duplication or label invention. The selected Meesho export currently has
139 terminal DELIVERED/RTO_COMPLETE orders. That is enough for prototype
held-out evidence, but not enough for a production-accuracy claim, so the report
preserves the small test size and bootstrap confidence intervals.
"""

from __future__ import annotations

import argparse
import csv
import json
import zipfile
from dataclasses import replace
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression

from . import real_rto as core

MIN_TERMINAL_ORDERS = 100
MIN_SPLIT_ROWS = 20


def parse_order_dates(series: pd.Series) -> pd.Series:
    """Parse mixed source dates without letting the first row dictate one format."""
    return pd.to_datetime(series, errors="coerce", dayfirst=True, format="mixed")


def terminal_mask(frame: pd.DataFrame, columns: core.ResolvedColumns) -> pd.Series:
    statuses = frame[columns.status].map(core._norm_status)
    return statuses.isin({core.TERMINAL_DELIVERED, core.TERMINAL_RTO})


def enrich_resolved_columns(frame: pd.DataFrame, columns: core.ResolvedColumns) -> core.ResolvedColumns:
    """Resolve published price variants without admitting post-outcome columns."""
    if columns.price:
        return columns
    price = core._pick(
        list(frame.columns),
        [
            "Supplier Listed Price (Incl. GST + Commission)",
            "Supplier Listed Price (Incl GST and Commision)",
            "Supplier Listed Price",
        ],
    )
    return replace(columns, price=price)


def candidate_quality(frame: pd.DataFrame, columns: core.ResolvedColumns) -> dict:
    mask = terminal_mask(frame, columns)
    dates = parse_order_dates(frame.loc[mask, columns.order_date])
    statuses = frame.loc[mask, columns.status].map(core._norm_status)
    valid = dates.notna()
    valid_statuses = statuses.loc[valid]
    return {
        "rows": int(len(frame)),
        "terminal_rows": int(mask.sum()),
        "terminal_with_valid_date": int(valid.sum()),
        "delivered_with_valid_date": int((valid_statuses == core.TERMINAL_DELIVERED).sum()),
        "rto_with_valid_date": int((valid_statuses == core.TERMINAL_RTO).sum()),
    }


def select_source_frame(archive: Path) -> tuple[pd.DataFrame, str, core.ResolvedColumns, list[dict]]:
    with zipfile.ZipFile(archive) as zf:
        members = sorted(name for name in zf.namelist() if name.lower().endswith(".csv"))
    if not members:
        raise RuntimeError("No CSV files found inside Kaggle dataset archive")

    diagnostics: list[dict] = []
    candidates: list[tuple[int, int, str, pd.DataFrame, core.ResolvedColumns]] = []
    for member in members:
        try:
            frame = core._read_csv_from_zip(archive, member)
            columns = enrich_resolved_columns(frame, core.resolve_columns(frame))
            core.assert_no_leakage(columns)
            quality = candidate_quality(frame, columns)
            diagnostics.append(
                {
                    "member": member,
                    **quality,
                    "columns": list(frame.columns),
                    "resolved_features": columns.feature_columns,
                }
            )
            both_classes = min(quality["delivered_with_valid_date"], quality["rto_with_valid_date"])
            candidates.append((quality["terminal_with_valid_date"], both_classes, member, frame, columns))
        except Exception as exc:
            diagnostics.append({"member": member, "error": str(exc)})

    if not candidates:
        raise RuntimeError("No CSV in the Kaggle archive matches the published Meesho schema")

    candidates.sort(key=lambda item: (item[0], item[1], item[2]), reverse=True)
    valid_count, both_classes, member, frame, columns = candidates[0]
    if valid_count < MIN_TERMINAL_ORDERS or both_classes < 1:
        raise RuntimeError(
            "No source member has a meaningful terminal DELIVERED/RTO population: "
            + json.dumps(diagnostics, default=str)
        )

    parsed = parse_order_dates(frame[columns.order_date])
    frame = frame.copy()
    frame[columns.order_date] = parsed.dt.strftime("%Y-%m-%d %H:%M:%S")
    return frame, member, columns, diagnostics


def prepare_terminal_orders(frame: pd.DataFrame, columns: core.ResolvedColumns) -> pd.DataFrame:
    work = frame.copy()
    work["_status"] = work[columns.status].map(core._norm_status)
    work = work[work["_status"].isin({core.TERMINAL_DELIVERED, core.TERMINAL_RTO})].copy()
    work["_order_date"] = pd.to_datetime(work[columns.order_date], errors="coerce", format="mixed")
    work = work[work["_order_date"].notna()].copy()

    if columns.order_id:
        work["_order_id_text"] = work[columns.order_id].astype(str).str.strip()
        work = work.sort_values("_order_date").drop_duplicates("_order_id_text", keep="last")

    work["_target"] = (work["_status"] == core.TERMINAL_RTO).astype(int)
    if len(work) < MIN_TERMINAL_ORDERS:
        raise RuntimeError(f"Too few real terminal rows after cleaning: {len(work)}")
    if work["_target"].nunique() != 2:
        raise RuntimeError("Terminal dataset does not contain both delivered and RTO labels")
    return work.sort_values("_order_date").reset_index(drop=True)


def chronological_split(frame: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Fixed 60/20/20 chronological held-out split; never shuffled or resampled."""
    n = len(frame)
    train_end = int(n * 0.60)
    val_end = int(n * 0.80)
    train = frame.iloc[:train_end].copy()
    val = frame.iloc[train_end:val_end].copy()
    test = frame.iloc[val_end:].copy()
    for name, split in (("train", train), ("validation", val), ("test", test)):
        if len(split) < MIN_SPLIT_ROWS:
            raise RuntimeError(f"{name} split too small for honest reporting: {len(split)}")
        if split["_target"].nunique() != 2:
            counts = split["_target"].value_counts().to_dict()
            raise RuntimeError(f"{name} chronological split lacks one class: {counts}")
    return train, val, test


def train_and_evaluate(
    frame: pd.DataFrame,
    columns: core.ResolvedColumns,
    source_manifest: dict,
) -> tuple[dict, dict, pd.DataFrame]:
    core.assert_no_leakage(columns)
    orders = prepare_terminal_orders(frame, columns)
    train, val, test = chronological_split(orders)

    x_train = core.hashed_matrix(train, columns)
    x_val = core.hashed_matrix(val, columns)
    x_test = core.hashed_matrix(test, columns)
    y_train = train["_target"].to_numpy(dtype=int)
    y_val = val["_target"].to_numpy(dtype=int)
    y_test = test["_target"].to_numpy(dtype=int)

    model = LogisticRegression(
        solver="liblinear",
        class_weight="balanced",
        C=1.0,
        max_iter=1000,
        random_state=core.RANDOM_SEED,
    )
    model.fit(x_train, y_train)
    val_prob = model.predict_proba(x_val)[:, 1]
    threshold, validation_choice = core.choose_threshold(y_val, val_prob)
    test_prob = model.predict_proba(x_test)[:, 1]

    report = {
        "schema_version": 1,
        "claim": "Small public real-data RTO prototype evidence; not Razorpay production accuracy",
        "loss_class": "Return to Origin (RTO)",
        "source": source_manifest,
        "limitations": [
            "The exact-RTO public source has only 139 terminal outcomes after filtering.",
            "Held-out confidence intervals are therefore wide and must be shown with point estimates.",
            "This dataset is supplier-specific and cannot establish Razorpay-wide production accuracy.",
            "No synthetic rows, SMOTE, duplicate expansion or post-outcome features are used.",
        ],
        "label_definition": {
            "positive": core.TERMINAL_RTO,
            "negative": core.TERMINAL_DELIVERED,
            "excluded": "All non-terminal/intermediate statuses, including CANCELLED/SHIPPED/RTO_INITIATED/RTO_LOCKED when present",
        },
        "feature_policy": {
            "principle": "Only fields available at/near order creation; no settlement, delivery or final-status fields",
            "resolved_columns": {
                key: value for key, value in columns.__dict__.items() if key != "status" and value
            },
            "hashed_feature_buckets": core.FEATURE_BUCKETS,
            "forbidden_terms": sorted(core.FORBIDDEN_FEATURE_TERMS),
        },
        "dataset": {
            "terminal_orders": int(len(orders)),
            "terminal_rto": int(orders["_target"].sum()),
            "terminal_delivered": int((orders["_target"] == 0).sum()),
            "prevalence": float(orders["_target"].mean()),
            "date_range": core._date_range(orders),
        },
        "split": {
            "method": "chronological 60/20/20; no shuffle, resampling or augmentation",
            "train": {"n": len(train), "rto": int(train["_target"].sum()), "date_range": core._date_range(train)},
            "validation": {"n": len(val), "rto": int(val["_target"].sum()), "date_range": core._date_range(val)},
            "test": {"n": len(test), "rto": int(test["_target"].sum()), "date_range": core._date_range(test)},
        },
        "model": {
            "family": "hashed logistic regression",
            "class_weight": "balanced",
            "random_seed": core.RANDOM_SEED,
            "threshold_selection": "maximize F1 on validation split only",
            "selected_threshold": float(threshold),
            "validation_at_threshold": validation_choice,
        },
        "heldout_test": core._metric_block(y_test, test_prob, threshold),
        "heldout_ci95_bootstrap": core.bootstrap_ci(y_test, test_prob, threshold, rounds=1000),
    }

    model_artifact = {
        "schema_version": 1,
        "model_family": "sha256-hashed-logistic-regression",
        "feature_buckets": core.FEATURE_BUCKETS,
        "intercept": float(model.intercept_[0]),
        "coefficients": [round(float(value), 10) for value in model.coef_[0]],
        "threshold": float(threshold),
        "feature_columns": report["feature_policy"]["resolved_columns"],
        "source_zip_sha256": source_manifest["zip_sha256"],
        "training_note": "No raw third-party rows are embedded in this artifact.",
    }
    predictions = core._deidentified_predictions(test, y_test, test_prob, threshold)
    return report, model_artifact, predictions


def run(cache_dir: Path, output_dir: Path) -> dict:
    archive = core.download_dataset(cache_dir)
    frame, member, columns, diagnostics = select_source_frame(archive)

    source_manifest = {
        "dataset_slug": core.DATASET_SLUG,
        "dataset_page": core.DATASET_PAGE,
        "download_url": core.DATASET_DOWNLOAD,
        "zip_sha256": core._sha256(archive),
        "zip_bytes": archive.stat().st_size,
        "csv_member": member,
        "source_rows": int(len(frame)),
        "dataset_page_claim": "Kaggle page describes it as real Meesho supplier order data",
        "dataset_page_license_text": "Data files © Original Authors",
        "source_selection": "CSV with largest terminal DELIVERED/RTO_COMPLETE population after mixed-date parsing",
    }

    report, model_artifact, predictions = train_and_evaluate(frame, columns, source_manifest)
    report["source_diagnostics"] = diagnostics

    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "report.json").write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    (output_dir / "report.md").write_text(core.report_markdown(report), encoding="utf-8")
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
    print(core.report_markdown(report))
    print("LIMITATIONS " + json.dumps(report["limitations"]))
    print("SOURCE_DIAGNOSTICS " + json.dumps(report["source_diagnostics"], default=str))
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
