"""CodGate real return-risk detector v2 with a sealed order-level holdout.

Why v2 exists:
The first chronological probe proved the source pipeline but exposed a validation
selection defect: a fixed probability threshold grid allowed a model with zero
validation F1 to win on a tiny average-precision difference. That probe is not
used as the final benchmark.

This v2 protocol is frozen before its final test is read:
- terminal Amazon India orders are aggregated by Order ID first;
- a stable SHA-256 bucket assigns 20% of unique orders to a sealed final test;
- the remaining 80% is split into train/validation without consulting test labels;
- target encodings are learned from train only (leave-one-out on train rows);
- model family/hyperparameters and threshold are chosen only on validation;
- threshold candidates are the exact validation score ordering, not an arbitrary
  numeric probability range;
- final test metrics are computed once after selection;
- no Status, Courier Status, Order ID, SMOTE or synthetic examples are features.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
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

from . import amazon_return_risk as source

RANDOM_SEED = 20260905
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
    "state_category",
    "city_category",
    "pin3_category",
    "category_size",
    "state_service",
]


def _bucket(order_id: str, namespace: str) -> int:
    digest = hashlib.sha256(f"{namespace}|{order_id}".encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big") % 10_000


def add_interactions(frame: pd.DataFrame) -> pd.DataFrame:
    work = frame.copy()
    for col in ("ship_state", "ship_city", "pin3", "category", "size", "service_level"):
        work[col] = work[col].fillna("").astype(str)
    work["state_category"] = work["ship_state"] + "|" + work["category"]
    work["city_category"] = work["ship_city"] + "|" + work["category"]
    work["pin3_category"] = work["pin3"] + "|" + work["category"]
    work["category_size"] = work["category"] + "|" + work["size"]
    work["state_service"] = work["ship_state"] + "|" + work["service_level"]
    return work


def sealed_split(frame: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    work = add_interactions(frame)
    final_mask = work["_order_id"].map(lambda value: _bucket(str(value), "codgate-final-v2") < 2000)
    final_test = work[final_mask].copy()
    development = work[~final_mask].copy()
    val_mask = development["_order_id"].map(lambda value: _bucket(str(value), "codgate-validation-v2") < 2500)
    validation = development[val_mask].copy()
    train = development[~val_mask].copy()

    for name, part in (("train", train), ("validation", validation), ("final_test", final_test)):
        positives = int(part["_target"].sum())
        negatives = int((part["_target"] == 0).sum())
        if len(part) < 1000 or positives < 50 or negatives < 500:
            raise RuntimeError(f"{name} split insufficient: n={len(part)} positives={positives} negatives={negatives}")
    return train, validation, final_test


def _stats(train: pd.DataFrame, column: str) -> dict[str, tuple[int, float]]:
    grouped = train.groupby(column, dropna=False)["_target"].agg(["count", "sum"])
    return {str(index): (int(row["count"]), float(row["sum"])) for index, row in grouped.iterrows()}


def encode(
    part: pd.DataFrame,
    train: pd.DataFrame,
    alpha: float,
    *,
    leave_one_out: bool,
) -> tuple[np.ndarray, dict]:
    global_rate = float(train["_target"].mean())
    n_train = max(1, len(train))
    columns: list[np.ndarray] = []
    maps: dict[str, dict] = {}
    targets = part["_target"].to_numpy(dtype=float)

    for column in CATEGORICAL:
        mapping = _stats(train, column)
        values = part[column].fillna("").astype(str).to_numpy()
        rate = np.empty(len(part), dtype=float)
        freq = np.empty(len(part), dtype=float)
        for i, value in enumerate(values):
            count, positive = mapping.get(value, (0, 0.0))
            if leave_one_out:
                count = max(0, count - 1)
                positive = max(0.0, positive - targets[i])
            rate[i] = (positive + alpha * global_rate) / (count + alpha)
            freq[i] = math.log1p(count) / math.log1p(n_train)
        columns.extend([rate, freq])
        maps[column] = {
            key: {
                "count": count,
                "returned": positive,
                "rate": (positive + alpha * global_rate) / (count + alpha),
            }
            for key, (count, positive) in mapping.items()
        }

    amount_raw = np.clip(part["amount"].to_numpy(dtype=float), 0, None)
    quantity_raw = np.clip(part["quantity"].to_numpy(dtype=float), 0, None)
    rows_raw = np.clip(part["item_rows"].to_numpy(dtype=float), 0, None)
    dow = part["_order_date"].dt.dayofweek.to_numpy(dtype=float)
    month = part["_order_date"].dt.month.to_numpy(dtype=float)
    amount_per_item = amount_raw / np.maximum(1.0, quantity_raw)
    columns.extend(
        [
            np.log1p(amount_raw),
            np.log1p(quantity_raw),
            np.log1p(rows_raw),
            np.log1p(amount_per_item),
            np.sin(2 * np.pi * dow / 7),
            np.cos(2 * np.pi * dow / 7),
            np.sin(2 * np.pi * month / 12),
            np.cos(2 * np.pi * month / 12),
        ]
    )
    return np.column_stack(columns), {"global_return_rate": global_rate, "alpha": alpha, "maps": maps}


def choose_threshold(y_true: np.ndarray, probabilities: np.ndarray) -> tuple[float, dict]:
    """Choose the exact validation-score cutoff maximizing F1.

    Sorting once avoids calibration assumptions. A candidate is evaluated only
    when the next score is different, so each threshold corresponds to a real
    decision set on validation data.
    """
    order = np.argsort(-probabilities, kind="mergesort")
    scores = probabilities[order]
    labels = y_true[order]
    positives = int(labels.sum())
    if positives <= 0:
        raise RuntimeError("validation split has no positive returns")
    cumulative_tp = np.cumsum(labels)
    best = None
    n = len(labels)
    for i in range(n):
        if i + 1 < n and scores[i + 1] == scores[i]:
            continue
        predicted = i + 1
        tp = int(cumulative_tp[i])
        fp = predicted - tp
        precision = tp / predicted
        recall = tp / positives
        f1 = 0.0 if precision + recall == 0 else 2 * precision * recall / (precision + recall)
        intervention_rate = predicted / n
        candidate = (f1, precision, recall, -intervention_rate, float(scores[i]))
        if best is None or candidate > best:
            best = candidate
    assert best is not None
    f1, precision, recall, neg_rate, threshold = best
    return threshold, {
        "f1": float(f1),
        "precision": float(precision),
        "recall": float(recall),
        "intervention_rate": float(-neg_rate),
    }


def metric_block(part: pd.DataFrame, probabilities: np.ndarray, threshold: float) -> dict:
    y = part["_target"].to_numpy(dtype=int)
    pred = probabilities >= threshold
    tp = int(np.sum((pred == 1) & (y == 1)))
    fp = int(np.sum((pred == 1) & (y == 0)))
    fn = int(np.sum((pred == 0) & (y == 1)))
    tn = int(np.sum((pred == 0) & (y == 0)))
    fp_mask = (pred == 1) & (y == 0)
    fn_mask = (pred == 0) & (y == 1)
    prevalence = float(np.mean(y))
    precision = float(precision_score(y, pred, zero_division=0))
    return {
        "n": int(len(y)),
        "positives": int(y.sum()),
        "prevalence": prevalence,
        "threshold": float(threshold),
        "precision": precision,
        "precision_lift_vs_prevalence": None if prevalence == 0 else precision / prevalence,
        "recall": float(recall_score(y, pred, zero_division=0)),
        "f1": float(f1_score(y, pred, zero_division=0)),
        "balanced_accuracy": float(balanced_accuracy_score(y, pred)),
        "average_precision": float(average_precision_score(y, probabilities)),
        "average_precision_lift_vs_prevalence": float(average_precision_score(y, probabilities) / prevalence),
        "roc_auc": float(roc_auc_score(y, probabilities)),
        "intervention_rate": float(np.mean(pred)),
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "tn": tn,
        "false_positive_order_gmv_at_risk_inr": round(float(part.loc[fp_mask, "amount"].sum()), 2),
        "missed_return_order_gmv_inr": round(float(part.loc[fn_mask, "amount"].sum()), 2),
    }


def top_fraction_metrics(part: pd.DataFrame, probabilities: np.ndarray, fraction: float) -> dict:
    y = part["_target"].to_numpy(dtype=int)
    k = max(1, int(round(len(y) * fraction)))
    idx = np.argsort(-probabilities, kind="mergesort")[:k]
    captured = int(y[idx].sum())
    positives = int(y.sum())
    return {
        "fraction": fraction,
        "orders": k,
        "returns_captured": captured,
        "precision": captured / k,
        "recall": 0.0 if positives == 0 else captured / positives,
        "lift_vs_prevalence": 0.0 if y.mean() == 0 else (captured / k) / y.mean(),
    }


def bootstrap_ci(part: pd.DataFrame, probabilities: np.ndarray, threshold: float, rounds: int = 2000) -> dict:
    y = part["_target"].to_numpy(dtype=int)
    rng = np.random.default_rng(RANDOM_SEED)
    ps: list[float] = []
    rs: list[float] = []
    for _ in range(rounds):
        idx = rng.integers(0, len(y), size=len(y))
        sample_y = y[idx]
        if sample_y.sum() == 0:
            continue
        sample_pred = probabilities[idx] >= threshold
        ps.append(float(precision_score(sample_y, sample_pred, zero_division=0)))
        rs.append(float(recall_score(sample_y, sample_pred, zero_division=0)))
    return {
        "precision": [float(np.quantile(ps, 0.025)), float(np.quantile(ps, 0.975))],
        "recall": [float(np.quantile(rs, 0.025)), float(np.quantile(rs, 0.975))],
    }


def train_and_evaluate(orders: pd.DataFrame, source_meta: dict, diagnostics: dict) -> tuple[dict, dict]:
    train, validation, final_test = sealed_split(orders)
    y_train = train["_target"].to_numpy(dtype=int)
    y_val = validation["_target"].to_numpy(dtype=int)
    trials: list[dict] = []
    best = None

    for alpha in (5.0, 15.0, 30.0, 60.0):
        train_raw, encoding_artifact = encode(train, train, alpha, leave_one_out=True)
        val_raw, _ = encode(validation, train, alpha, leave_one_out=False)
        scaler = StandardScaler().fit(train_raw)
        x_train = scaler.transform(train_raw)
        x_val = scaler.transform(val_raw)

        logistic_configs = [
            ("logistic", {"C": c, "class_weight": weight})
            for weight in (None, "balanced")
            for c in (0.05, 0.2, 1.0, 5.0)
        ]
        hist_configs = [
            (
                "hist_gradient_boosting",
                {
                    "learning_rate": lr,
                    "max_leaf_nodes": leaves,
                    "max_iter": 160,
                    "class_weight": weight,
                },
            )
            for weight in (None, "balanced")
            for lr in (0.03, 0.08)
            for leaves in (7, 15, 31)
        ]

        for family, config in logistic_configs + hist_configs:
            if family == "logistic":
                model = LogisticRegression(
                    C=float(config["C"]),
                    class_weight=config["class_weight"],
                    max_iter=3000,
                    solver="liblinear",
                    random_state=RANDOM_SEED,
                )
            else:
                model = HistGradientBoostingClassifier(
                    learning_rate=float(config["learning_rate"]),
                    max_leaf_nodes=int(config["max_leaf_nodes"]),
                    max_iter=int(config["max_iter"]),
                    class_weight=config["class_weight"],
                    l2_regularization=1.0,
                    random_state=RANDOM_SEED,
                )
            model.fit(x_train, y_train)
            val_prob = model.predict_proba(x_val)[:, 1]
            threshold, threshold_metrics = choose_threshold(y_val, val_prob)
            ap = float(average_precision_score(y_val, val_prob))
            auc = float(roc_auc_score(y_val, val_prob))
            trial = {
                "alpha": alpha,
                "family": family,
                "config": {key: ("none" if value is None else value) for key, value in config.items()},
                "validation_average_precision": ap,
                "validation_roc_auc": auc,
                "threshold": float(threshold),
                "validation_f1": threshold_metrics["f1"],
                "validation_precision": threshold_metrics["precision"],
                "validation_recall": threshold_metrics["recall"],
                "validation_intervention_rate": threshold_metrics["intervention_rate"],
            }
            trials.append(trial)
            key = (
                trial["validation_f1"],
                trial["validation_average_precision"],
                trial["validation_roc_auc"],
                trial["validation_precision"],
                trial["validation_recall"],
                -trial["validation_intervention_rate"],
            )
            if best is None or key > best[0]:
                best = (key, trial, model, scaler, encoding_artifact)

    assert best is not None
    _key, selected, model, scaler, encoding_artifact = best
    alpha = float(selected["alpha"])
    final_raw, _ = encode(final_test, train, alpha, leave_one_out=False)
    x_final = scaler.transform(final_raw)
    final_prob = model.predict_proba(x_final)[:, 1]
    threshold = float(selected["threshold"])
    heldout = metric_block(final_test, final_prob, threshold)
    ci = bootstrap_ci(final_test, final_prob, threshold)

    def split_info(part: pd.DataFrame) -> dict:
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
        "schema_version": 2,
        "loss_class": "RETURN_TO_SELLER",
        "claim": "Real public Amazon India return-risk detector prototype; not Amazon/Razorpay production accuracy",
        "protocol_status": "final holdout v2 is sealed by stable order-id hash before model selection",
        "source": source_meta,
        "source_diagnostics": diagnostics,
        "dataset": {
            "terminal_orders": int(len(orders)),
            "returned_to_seller": int(orders["_target"].sum()),
            "delivered_to_buyer": int((orders["_target"] == 0).sum()),
            "return_prevalence": float(orders["_target"].mean()),
        },
        "split": {
            "method": "stable SHA-256 order-level buckets: 20% sealed final test; remaining development 75/25 train/validation",
            "train": split_info(train),
            "validation": split_info(validation),
            "final_test": split_info(final_test),
        },
        "selection": {
            "criterion": "validation F1 first, then PR-AUC/ROC-AUC/precision/recall; final-test labels never consulted",
            "selected": selected,
            "trials": trials,
        },
        "heldout_test": heldout,
        "heldout_ci95_bootstrap": ci,
        "ranking_checks": {
            "top_5_percent": top_fraction_metrics(final_test, final_prob, 0.05),
            "top_10_percent": top_fraction_metrics(final_test, final_prob, 0.10),
            "top_20_percent": top_fraction_metrics(final_test, final_prob, 0.20),
        },
        "leakage_controls": {
            "order_id_feature": False,
            "status_feature": False,
            "courier_status_feature": False,
            "duplicate_item_rows_split_across_sets": False,
            "target_encoding_train_only": True,
            "train_target_encoding_leave_one_out": True,
            "final_test_used_for_model_selection": False,
            "synthetic_rows_or_smote": False,
        },
        "false_positive_cost_disclosure": {
            "realized_profit_loss_identifiable_from_source": False,
            "source_derived_exposure": "false_positive_order_gmv_at_risk_inr",
            "production_requirement": "merchant must supply approved margin/conversion cost parameters before ₹ profit-loss claims are enabled",
        },
        "limitations": [
            "Public Amazon India marketplace dataset; not Razorpay-owned production traffic.",
            "Large dataset exposes return-to-seller outcome but not payment mode, so final detector loss class is RETURN_TO_SELLER rather than COD-only RTO.",
            "Exact COD domain evidence is audited separately on the 47-row Boss Leathers slice and is never used as the accuracy benchmark.",
            "False-positive GMV exposure is real source amount; realized merchant margin loss is intentionally not fabricated.",
        ],
    }

    model_artifact = {
        "schema_version": 2,
        "family": selected["family"],
        "selected": selected,
        "threshold": threshold,
        "categorical_columns": CATEGORICAL,
        "encoding": encoding_artifact,
        "scaler_mean": scaler.mean_.tolist(),
        "scaler_scale": scaler.scale_.tolist(),
        "source_zip_sha256": source_meta["zip_sha256"],
    }
    if selected["family"] == "logistic":
        model_artifact["intercept"] = float(model.intercept_[0])
        model_artifact["coefficients"] = model.coef_[0].tolist()
    else:
        model_artifact["runtime_note"] = "Evidence-selected HistGradientBoosting model; runtime export is built only after the held-out result is accepted."
    return report, model_artifact


def report_markdown(report: dict) -> str:
    test = report["heldout_test"]
    ci = report["heldout_ci95_bootstrap"]
    selected = report["selection"]["selected"]
    return "\n".join(
        [
            "# CodGate real return-risk detector v2",
            "",
            f"- Real terminal orders: {report['dataset']['terminal_orders']}",
            f"- Final held-out orders: {test['n']} ({test['positives']} returns)",
            f"- Selected on validation: {selected['family']}",
            f"- Precision: {test['precision']:.2%} (95% bootstrap {ci['precision'][0]:.2%}–{ci['precision'][1]:.2%})",
            f"- Recall: {test['recall']:.2%} (95% bootstrap {ci['recall'][0]:.2%}–{ci['recall'][1]:.2%})",
            f"- Precision lift vs prevalence: {test['precision_lift_vs_prevalence']:.2f}×",
            f"- F1: {test['f1']:.4f}",
            f"- PR-AUC: {test['average_precision']:.4f}",
            f"- ROC-AUC: {test['roc_auc']:.4f}",
            f"- Confusion: TP {test['tp']} · FP {test['fp']} · FN {test['fn']} · TN {test['tn']}",
            f"- False-positive order GMV exposed to intervention: ₹{test['false_positive_order_gmv_at_risk_inr']:.2f}",
            f"- Missed-return order GMV: ₹{test['missed_return_order_gmv_inr']:.2f}",
            "",
            "The final test was assigned before model selection and was not used for hyperparameter or threshold selection.",
        ]
    )


def run(cache_dir: Path, output_dir: Path) -> dict:
    archive = source.download_dataset(cache_dir)
    frame, member, columns, members = source.load_source(archive)
    orders, diagnostics = source.prepare_orders(frame, columns)
    source_meta = {
        "dataset_slug": source.DATASET_SLUG,
        "dataset_page": source.DATASET_PAGE,
        "member": member,
        "zip_sha256": source._sha256(archive),
        "zip_bytes": int(archive.stat().st_size),
        "raw_member_rows": int(len(frame)),
        "archive_member_count": int(len(members)),
    }
    report, model = train_and_evaluate(orders, source_meta, diagnostics)
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "report.json").write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    (output_dir / "report.md").write_text(report_markdown(report), encoding="utf-8")
    (output_dir / "model.json").write_text(json.dumps(model, separators=(",", ":")), encoding="utf-8")
    print(report_markdown(report))
    print("AMAZON_RETURN_RISK_V2_METRICS " + json.dumps(report["heldout_test"], sort_keys=True))
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache-dir", type=Path, default=Path(".cache/amazon-return-risk"))
    parser.add_argument("--output-dir", type=Path, default=Path("artifacts/amazon-return-risk-v2"))
    args = parser.parse_args()
    run(args.cache_dir, args.output_dir)


if __name__ == "__main__":
    main()
