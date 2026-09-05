"""Build a real, defense-only credit-card fraud detector and frozen evidence.

Dataset: Worldline + Machine Learning Group, Universite Libre de Bruxelles (ULB)
OpenML dataset id 1597. The public benchmark contains 284,807 real European
card transactions from September 2013, including 492 confirmed frauds.

Evaluation protocol is fixed before looking at the final holdout:
- sort by Time to preserve temporal order;
- first 70% train, next 15% validation, final 15% held out;
- model family and decision threshold are chosen using validation only;
- the final holdout is scored exactly once by the selected configuration;
- Class is never a feature;
- no resampling or synthetic fraud examples are used.

Because the primary dataset description does not state the currency of Amount,
all value-at-risk figures are reported in recorded dataset amount units. We do
not silently label them EUR or INR.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from scipy.io import arff
from sklearn.ensemble import HistGradientBoostingClassifier, RandomForestClassifier
from sklearn.linear_model import LogisticRegression
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
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

DATASET_NAME = "Credit Card Fraud Detection (Worldline + ULB)"
OPENML_ID = 1597
OPENML_ARFF_URL = "https://www.openml.org/data/v1/download/1673544/creditcard.arff"
KAGGLE_DATASET = "mlg-ulb/creditcardfraud"
EXPECTED_ROWS = 284_807
EXPECTED_FRAUDS = 492
RANDOM_STATE = 20260905
TRAIN_FRAC = 0.70
VALIDATION_FRAC = 0.15
FINAL_FRAC = 0.15


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def load_arff(path: Path) -> pd.DataFrame:
    records, _ = arff.loadarff(path)
    df = pd.DataFrame(records)
    df.columns = [str(c) for c in df.columns]
    for c in df.columns:
        if df[c].dtype == object:
            df[c] = df[c].map(lambda v: v.decode("utf-8") if isinstance(v, (bytes, bytearray)) else v)
    for c in df.columns:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return df


def validate_raw(df: pd.DataFrame) -> dict:
    expected_features = ["Time"] + [f"V{i}" for i in range(1, 29)] + ["Amount", "Class"]
    missing = [c for c in expected_features if c not in df.columns]
    extras = [c for c in df.columns if c not in expected_features]
    if missing or extras:
        raise RuntimeError(f"Schema mismatch: missing={missing}, extras={extras}")
    if len(df) != EXPECTED_ROWS:
        raise RuntimeError(f"Expected {EXPECTED_ROWS} rows, got {len(df)}")
    if df.isna().any().any():
        raise RuntimeError("Unexpected missing values in source dataset")
    classes = sorted(df["Class"].unique().tolist())
    if classes != [0.0, 1.0]:
        raise RuntimeError(f"Unexpected Class values: {classes}")
    frauds = int(df["Class"].sum())
    if frauds != EXPECTED_FRAUDS:
        raise RuntimeError(f"Expected {EXPECTED_FRAUDS} frauds, got {frauds}")
    if (df["Amount"] < 0).any():
        raise RuntimeError("Unexpected negative Amount")
    return {
        "rows": int(len(df)),
        "frauds": frauds,
        "legitimate": int(len(df) - frauds),
        "fraud_prevalence": float(frauds / len(df)),
        "time_min": float(df["Time"].min()),
        "time_max": float(df["Time"].max()),
        "amount_min": float(df["Amount"].min()),
        "amount_max": float(df["Amount"].max()),
        "amount_total": float(df["Amount"].sum()),
    }


def add_features(df: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]:
    out = df.drop(columns=["Class"]).copy()
    # Amount is strongly skewed. Keep the raw value and add a monotonic log view.
    out["log_amount"] = np.log1p(out["Amount"].clip(lower=0.0))
    # Time is seconds from the first transaction. Add a 24h cycle without using labels.
    seconds_per_day = 86400.0
    angle = 2.0 * math.pi * (out["Time"] % seconds_per_day) / seconds_per_day
    out["time_day_sin"] = np.sin(angle)
    out["time_day_cos"] = np.cos(angle)
    names = list(out.columns)
    forbidden = {"Class", "class", "label", "target", "fraud"}
    for name in names:
        lowered = name.lower()
        if lowered in {x.lower() for x in forbidden} or "class" in lowered or "label" in lowered or "target" in lowered:
            raise RuntimeError(f"Leakage guard rejected feature: {name}")
    return out.astype(float), names


def chronological_split(df: pd.DataFrame):
    ordered = df.sort_values("Time", kind="mergesort").reset_index(drop=True)
    n = len(ordered)
    train_end = int(n * TRAIN_FRAC)
    val_end = int(n * (TRAIN_FRAC + VALIDATION_FRAC))
    train = ordered.iloc[:train_end].copy()
    val = ordered.iloc[train_end:val_end].copy()
    final = ordered.iloc[val_end:].copy()
    if len(train) + len(val) + len(final) != n:
        raise RuntimeError("Split row conservation failed")
    if not (train["Time"].max() <= val["Time"].min() <= final["Time"].min()):
        raise RuntimeError("Chronological split ordering failed")
    for name, part in (("train", train), ("validation", val), ("final", final)):
        positives = int(part["Class"].sum())
        if positives < 20:
            raise RuntimeError(f"{name} has too few fraud positives: {positives}")
    return train, val, final


def candidate_models(positive_weight: float):
    # Fixed before final-holdout evaluation. Thresholds are selected on validation only.
    capped_weight = float(min(max(positive_weight, 1.0), 35.0))
    return {
        "logistic": Pipeline(
            [
                ("scale", StandardScaler()),
                (
                    "model",
                    LogisticRegression(
                        C=1.0,
                        max_iter=2000,
                        class_weight={0: 1.0, 1: capped_weight},
                        random_state=RANDOM_STATE,
                    ),
                ),
            ]
        ),
        "random_forest": RandomForestClassifier(
            n_estimators=260,
            max_depth=14,
            min_samples_leaf=2,
            max_features="sqrt",
            class_weight={0: 1.0, 1: capped_weight},
            n_jobs=-1,
            random_state=RANDOM_STATE,
        ),
        "hist_gradient_boosting": HistGradientBoostingClassifier(
            max_iter=220,
            learning_rate=0.065,
            max_leaf_nodes=31,
            min_samples_leaf=20,
            l2_regularization=1.0,
            random_state=RANDOM_STATE,
        ),
    }, capped_weight


def threshold_candidates(probabilities: np.ndarray) -> np.ndarray:
    fixed = np.linspace(0.01, 0.99, 197)
    quantiles = np.quantile(probabilities, np.linspace(0.90, 0.9999, 120))
    return np.unique(np.clip(np.concatenate([fixed, quantiles]), 0.0, 1.0))


def choose_threshold(y: np.ndarray, p: np.ndarray) -> tuple[float, dict]:
    """Select threshold on validation only using F1, then precision, then recall."""
    best = None
    for threshold in threshold_candidates(p):
        pred = (p >= threshold).astype(int)
        precision = float(precision_score(y, pred, zero_division=0))
        recall = float(recall_score(y, pred, zero_division=0))
        f1 = float(f1_score(y, pred, zero_division=0))
        # Ignore degenerate thresholds that flag nothing.
        if int(pred.sum()) == 0:
            continue
        key = (f1, precision, recall, -float(threshold))
        if best is None or key > best[0]:
            best = (key, float(threshold), {"precision": precision, "recall": recall, "f1": f1})
    if best is None:
        raise RuntimeError("No usable validation threshold")
    return best[1], best[2]


def metrics(frame: pd.DataFrame, y: np.ndarray, p: np.ndarray, threshold: float) -> dict:
    pred = (p >= threshold).astype(int)
    tn, fp, fn, tp = confusion_matrix(y, pred, labels=[0, 1]).ravel()
    fp_mask = (pred == 1) & (y == 0)
    fn_mask = (pred == 0) & (y == 1)
    tp_mask = (pred == 1) & (y == 1)
    fraud_amount = float(frame.loc[y == 1, "Amount"].sum())
    captured_amount = float(frame.loc[tp_mask, "Amount"].sum())
    return {
        "rows": int(len(frame)),
        "frauds": int(y.sum()),
        "fraud_prevalence": float(y.mean()),
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
        "average_precision": float(average_precision_score(y, p)),
        "roc_auc": float(roc_auc_score(y, p)),
        "flagged_transactions": int(pred.sum()),
        "false_positive_amount_at_risk": float(frame.loc[fp_mask, "Amount"].sum()),
        "false_positive_cost_definition": "sum of recorded Amount for legitimate holdout transactions incorrectly blocked; explicit hard-block upper bound, not claimed realised net loss",
        "missed_fraud_amount": float(frame.loc[fn_mask, "Amount"].sum()),
        "total_fraud_amount": fraud_amount,
        "captured_fraud_amount": captured_amount,
        "captured_fraud_amount_rate": 0.0 if fraud_amount <= 0 else captured_amount / fraud_amount,
    }


def train_select(train: pd.DataFrame, val: pd.DataFrame, feature_names: list[str]):
    x_train = train[feature_names].to_numpy(float)
    y_train = train["Class"].to_numpy(int)
    x_val = val[feature_names].to_numpy(float)
    y_val = val["Class"].to_numpy(int)
    ratio = (len(y_train) - int(y_train.sum())) / max(int(y_train.sum()), 1)
    models, used_weight = candidate_models(math.sqrt(ratio))
    summaries = {}
    selected = None
    for name, model in models.items():
        if name == "hist_gradient_boosting":
            sample_weight = np.where(y_train == 1, used_weight, 1.0)
            model.fit(x_train, y_train, sample_weight=sample_weight)
        else:
            model.fit(x_train, y_train)
        p = model.predict_proba(x_val)[:, 1]
        threshold, threshold_metrics = choose_threshold(y_val, p)
        report = metrics(val, y_val, p, threshold)
        summaries[name] = {
            "threshold": threshold,
            "threshold_selection": threshold_metrics,
            "validation": report,
        }
        key = (report["f1"], report["average_precision"], report["precision"], report["recall"])
        if selected is None or key > selected[0]:
            selected = (key, name, model, threshold)
    assert selected is not None
    return selected[1], selected[2], selected[3], summaries, used_weight


def frozen_holdout(frame: pd.DataFrame, probabilities: np.ndarray, threshold: float) -> pd.DataFrame:
    y = frame["Class"].to_numpy(int)
    pred = (probabilities >= threshold).astype(int)
    out = frame[["Time", "Amount", "Class"]].copy().reset_index(drop=True)
    out["case_id"] = [
        hashlib.sha256(f"{idx}|{t:.6f}|{a:.6f}".encode()).hexdigest()[:20]
        for idx, (t, a) in enumerate(zip(out["Time"], out["Amount"]))
    ]
    out["probability"] = probabilities
    out["prediction"] = pred
    return out[["case_id", "Time", "Amount", "Class", "probability", "prediction"]]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--outdir", default=Path("artifacts/real_fraud"), type=Path)
    args = parser.parse_args()
    args.outdir.mkdir(parents=True, exist_ok=True)

    source_sha = sha256_file(args.input)
    raw = load_arff(args.input)
    raw_audit = validate_raw(raw)
    features, feature_names = add_features(raw)
    modeled = features.copy()
    modeled["Class"] = raw["Class"].astype(int).values
    train, val, final = chronological_split(modeled)

    model_name, model, threshold, candidates, positive_weight = train_select(train, val, feature_names)

    # FINAL HOLDOUT: no model or threshold choice occurs after this point.
    x_final = final[feature_names].to_numpy(float)
    y_final = final["Class"].to_numpy(int)
    final_prob = model.predict_proba(x_final)[:, 1]
    final_metrics = metrics(final, y_final, final_prob, threshold)

    baseline = {
        "name": "always_legitimate",
        "accuracy": float((y_final == 0).mean()),
        "precision": 0.0,
        "recall": 0.0,
        "f1": 0.0,
    }

    report = {
        "evidence_version": "real-fraud-v1",
        "loss_class": "PAYMENT_CARD_FRAUD",
        "source": {
            "dataset": DATASET_NAME,
            "openml_id": OPENML_ID,
            "openml_arff_url": OPENML_ARFF_URL,
            "kaggle_dataset": KAGGLE_DATASET,
            "provenance": "real European card transactions collected/analyzed in a Worldline + ULB Machine Learning Group research collaboration",
            "license": "Open Database License / Database Contents License as stated on Kaggle dataset page",
            "raw_sha256": source_sha,
            **raw_audit,
        },
        "evaluation_protocol": {
            "split": "chronological_70_15_15_by_Time",
            "train_rows": int(len(train)),
            "validation_rows": int(len(val)),
            "final_holdout_rows": int(len(final)),
            "train_frauds": int(train["Class"].sum()),
            "validation_frauds": int(val["Class"].sum()),
            "final_holdout_frauds": int(final["Class"].sum()),
            "train_time_max": float(train["Time"].max()),
            "validation_time_min": float(val["Time"].min()),
            "validation_time_max": float(val["Time"].max()),
            "final_time_min": float(final["Time"].min()),
            "model_selection": "validation only",
            "threshold_selection": "validation F1 only",
            "final_holdout_rule": "selected model+threshold frozen before final scoring; no post-final tuning in this pipeline",
        },
        "leakage_controls": [
            "Class is never present in the feature matrix.",
            "No target-derived features are constructed.",
            "No synthetic fraud rows, SMOTE, label duplication, or test-set resampling is used.",
            "The split is ordered by source Time so later transactions are not used to train earlier predictions.",
            "Model family and threshold are selected on validation only.",
        ],
        "model": {
            "selected": model_name,
            "feature_names": feature_names,
            "feature_count": len(feature_names),
            "positive_training_weight": positive_weight,
            "decision_threshold": threshold,
            "candidate_validation_results": candidates,
        },
        "final_holdout": final_metrics,
        "baseline": baseline,
        "cost_boundary": {
            "false_positive_cost": "hard-block upper bound = legitimate transaction Amount incorrectly blocked",
            "missed_fraud_cost": "recorded Amount on confirmed fraud transactions missed by detector",
            "currency": "not asserted; primary public dataset description names Amount but does not state currency",
            "interpretation": "Amount-at-risk is measurable from source rows; it is not claimed to equal realised merchant net loss or chargeback cost",
        },
    }

    bundle = {
        "evidence_version": report["evidence_version"],
        "source_raw_sha256": source_sha,
        "model_name": model_name,
        "model": model,
        "threshold": threshold,
        "feature_names": feature_names,
    }
    metrics_path = args.outdir / "metrics.json"
    model_path = args.outdir / "model.joblib"
    holdout_path = args.outdir / "final_holdout.csv.gz"
    metrics_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    joblib.dump(bundle, model_path, compress=3)
    frozen_holdout(final, final_prob, threshold).to_csv(holdout_path, index=False, compression="gzip")

    print("REAL FRAUD EVIDENCE BUILT")
    print(f"source rows={len(raw):,} frauds={int(raw['Class'].sum())} raw_sha256={source_sha}")
    print(
        f"split train={len(train):,}/{int(train['Class'].sum())}fraud "
        f"val={len(val):,}/{int(val['Class'].sum())}fraud "
        f"final={len(final):,}/{int(final['Class'].sum())}fraud"
    )
    for name, summary in candidates.items():
        m = summary["validation"]
        print(
            f"VALIDATION {name}: P={m['precision']:.4f} R={m['recall']:.4f} "
            f"F1={m['f1']:.4f} AP={m['average_precision']:.4f} t={summary['threshold']:.5f}"
        )
    print(f"SELECTED {model_name} threshold={threshold:.6f}")
    print(
        f"FINAL P={final_metrics['precision']:.4f} R={final_metrics['recall']:.4f} "
        f"F1={final_metrics['f1']:.4f} AP={final_metrics['average_precision']:.4f} "
        f"BA={final_metrics['balanced_accuracy']:.4f}"
    )
    print(
        f"FINAL TP={final_metrics['tp']} FP={final_metrics['fp']} "
        f"FN={final_metrics['fn']} TN={final_metrics['tn']}"
    )
    print(f"FINAL false-positive Amount at risk={final_metrics['false_positive_amount_at_risk']:.2f}")
    print(f"FINAL missed fraud Amount={final_metrics['missed_fraud_amount']:.2f}")
    print(f"FINAL captured fraud Amount rate={final_metrics['captured_fraud_amount_rate']:.2%}")


if __name__ == "__main__":
    main()
