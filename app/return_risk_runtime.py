"""Frozen real-data RETURN_TO_SELLER detector runtime.

This module is dependency-light and deterministic. It loads the compact artifact
produced by ``evidence.amazon_return_risk_v2``, verifies both artifact hashes
against the frozen evidence summary, reproduces the exact training-time feature
encoding/scaling, and evaluates the selected logistic model locally.

Because the selected model was trained with ``class_weight='balanced'``, its
logistic output is intentionally called a *risk score*, not a calibrated return
probability. The detector never creates a Payment Link or changes money-moving
state. A merchant may supply its own false-positive unit cost; CodGate has no
fabricated default merchant-loss number.
"""

from __future__ import annotations

import base64
import hashlib
import json
import lzma
import math
import re
from datetime import date, datetime, timezone
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODEL_PATH = ROOT / "data" / "return_risk_model_v2.xz.b64"
MODEL_PART_PREFIX = "return_risk_model_v2.part"
MODEL_PART_SUFFIX = ".b64"
MODEL_PART_COUNT = 6
EVIDENCE_PATH = ROOT / "data" / "return_risk_evidence_v2.json"


def _norm_text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    return re.sub(r"\s+", " ", str(value).strip().lower())


def _parse_date(value: object) -> datetime:
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, date):
        dt = datetime(value.year, value.month, value.day)
    else:
        text = str(value or "").strip()
        if not text:
            raise ValueError("order_date is required")
        candidates = [text, text.replace("Z", "+00:00")]
        dt = None
        for candidate in candidates:
            try:
                dt = datetime.fromisoformat(candidate)
                break
            except ValueError:
                continue
        if dt is None:
            for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%m/%d/%Y"):
                try:
                    dt = datetime.strptime(text, fmt)
                    break
                except ValueError:
                    continue
        if dt is None:
            raise ValueError("order_date must be ISO YYYY-MM-DD or an ISO datetime")
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def _nonnegative_number(value: object, name: str) -> float:
    try:
        number = float(value or 0)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be numeric") from exc
    if not math.isfinite(number) or number < 0:
        raise ValueError(f"{name} must be a finite non-negative number")
    return number


def _read_model_b64() -> str:
    """Read one frozen artifact or the six Git-safe chunks used in the repo."""
    if MODEL_PATH.exists():
        encoded = MODEL_PATH.read_text(encoding="utf-8").strip()
        if encoded:
            return encoded

    expected = [
        ROOT / "data" / f"{MODEL_PART_PREFIX}{index:02d}{MODEL_PART_SUFFIX}"
        for index in range(MODEL_PART_COUNT)
    ]
    missing = [path.name for path in expected if not path.exists()]
    if missing:
        raise RuntimeError(f"frozen return-risk model chunks missing: {', '.join(missing)}")
    encoded = "".join(path.read_text(encoding="utf-8").strip() for path in expected)
    if not encoded:
        raise RuntimeError("frozen return-risk model artifact is empty")
    return encoded


@lru_cache(maxsize=1)
def evidence_summary() -> dict:
    return json.loads(EVIDENCE_PATH.read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def load_model() -> dict:
    evidence = evidence_summary()
    encoded = _read_model_b64()
    try:
        compressed = base64.b64decode(encoded, validate=True)
    except Exception as exc:  # pragma: no cover
        raise RuntimeError("frozen return-risk model base64 is invalid") from exc
    compressed_sha = hashlib.sha256(compressed).hexdigest()
    if compressed_sha != evidence["runtime_model_compressed_sha256"]:
        raise RuntimeError(
            "frozen return-risk model compressed SHA-256 mismatch: "
            f"{compressed_sha} != {evidence['runtime_model_compressed_sha256']}"
        )
    try:
        raw = lzma.decompress(compressed)
    except lzma.LZMAError as exc:  # pragma: no cover
        raise RuntimeError("frozen return-risk model xz payload is invalid") from exc
    raw_sha = hashlib.sha256(raw).hexdigest()
    if raw_sha != evidence["runtime_model_sha256"]:
        raise RuntimeError(
            "frozen return-risk model JSON SHA-256 mismatch: "
            f"{raw_sha} != {evidence['runtime_model_sha256']}"
        )
    model = json.loads(raw)
    if model.get("v") != 2 or model.get("f") != "logistic" or model.get("l") != "RETURN_TO_SELLER":
        raise RuntimeError("unsupported frozen return-risk model contract")
    if model.get("s") != evidence["source"]["zip_sha256"]:
        raise RuntimeError("return-risk model source SHA does not match evidence source")
    if abs(float(model.get("t")) - float(evidence["heldout_test"]["threshold"])) > 1e-15:
        raise RuntimeError("return-risk model threshold does not match frozen evidence")
    if len(model.get("w", [])) != 40 or len(model.get("u", [])) != 40 or len(model.get("q", [])) != 40:
        raise RuntimeError("return-risk model feature vector must be 40-dimensional")
    return model


def _runtime_row(order: dict) -> tuple[dict, datetime]:
    dt = _parse_date(order.get("order_date"))
    postal = re.sub(r"\D", "", str(order.get("postal_code") or order.get("pincode") or ""))
    row = {
        "fulfilment": _norm_text(order.get("fulfilment")),
        "sales_channel": _norm_text(order.get("sales_channel")),
        "service_level": _norm_text(order.get("service_level")),
        "style": _norm_text(order.get("style")),
        "sku": _norm_text(order.get("sku")),
        "category": _norm_text(order.get("category")),
        "size": _norm_text(order.get("size")),
        "ship_city": _norm_text(order.get("ship_city")),
        "ship_state": _norm_text(order.get("ship_state")),
        "pin3": postal[:3] if len(postal) >= 3 else "",
        "b2b": _norm_text(order.get("b2b")),
    }
    row["state_category"] = row["ship_state"] + "|" + row["category"]
    row["city_category"] = row["ship_city"] + "|" + row["category"]
    row["pin3_category"] = row["pin3"] + "|" + row["category"]
    row["category_size"] = row["category"] + "|" + row["size"]
    row["state_service"] = row["ship_state"] + "|" + row["service_level"]
    return row, dt


def _feature_vector(order: dict, model: dict) -> list[float]:
    row, dt = _runtime_row(order)
    features: list[float] = []
    global_rate = float(model["g"])
    n_train = max(1, int(model["n"]))
    maps = model["m"]

    for column in model["c"]:
        value = row.get(column, "")
        entry = maps.get(column, {}).get(value)
        if entry is None:
            count = 0
            rate = global_rate
        else:
            count = int(entry[0])
            rate = float(entry[1])
        frequency = math.log1p(count) / math.log1p(n_train)
        features.extend([rate, frequency])

    amount = _nonnegative_number(order.get("amount"), "amount")
    quantity = _nonnegative_number(order.get("quantity", 1), "quantity")
    item_rows = _nonnegative_number(order.get("item_rows", 1), "item_rows")
    amount_per_item = amount / max(1.0, quantity)
    dow = float(dt.weekday())
    month = float(dt.month)
    features.extend(
        [
            math.log1p(amount),
            math.log1p(quantity),
            math.log1p(item_rows),
            math.log1p(amount_per_item),
            math.sin(2 * math.pi * dow / 7),
            math.cos(2 * math.pi * dow / 7),
            math.sin(2 * math.pi * month / 12),
            math.cos(2 * math.pi * month / 12),
        ]
    )
    if len(features) != 40:
        raise RuntimeError(f"return-risk feature vector drifted: {len(features)} != 40")
    return features


def score_return_risk(order: dict, false_positive_cost_per_order_inr: float | None = None) -> dict:
    """Score one order with the exact frozen v2 real-data detector."""
    model = load_model()
    evidence = evidence_summary()
    raw = _feature_vector(order, model)
    standardized = []
    for value, mean, scale in zip(raw, model["u"], model["q"]):
        denominator = float(scale) if float(scale) != 0 else 1.0
        standardized.append((float(value) - float(mean)) / denominator)
    logit = float(model["i"]) + sum(float(weight) * value for weight, value in zip(model["w"], standardized))
    if logit >= 0:
        risk_score = 1.0 / (1.0 + math.exp(-logit))
    else:
        exp_logit = math.exp(logit)
        risk_score = exp_logit / (1.0 + exp_logit)
    threshold = float(model["t"])
    predicted_return = risk_score >= threshold

    merchant_cost = None
    heldout_false_positive_cost = None
    cost_if_wrong = None
    if false_positive_cost_per_order_inr is not None:
        merchant_cost = _nonnegative_number(false_positive_cost_per_order_inr, "false_positive_cost_per_order_inr")
        heldout_false_positive_cost = round(merchant_cost * int(evidence["heldout_test"]["fp"]), 2)
        if predicted_return:
            cost_if_wrong = merchant_cost

    return {
        "loss_class": "RETURN_TO_SELLER",
        "model_version": "amazon-return-risk-v2",
        "risk_score": risk_score,
        "score_is_calibrated_probability": False,
        "score_semantics": "weighted-logistic ranking/decision score; compare with frozen threshold, do not read as an absolute return probability",
        "threshold": threshold,
        "predicted_return": predicted_return,
        "decision": "FLAG_RETURN_RISK" if predicted_return else "STANDARD_FLOW",
        "action": "RISK_REVIEW" if predicted_return else "NO_RISK_INTERVENTION",
        "execution": "advisory_only",
        "false_positive_cost_per_order_inr": merchant_cost,
        "false_positive_cost_if_wrong_inr": cost_if_wrong,
        "heldout_modeled_false_positive_cost_inr": heldout_false_positive_cost,
        "source_zip_sha256": model["s"],
        "runtime_model_sha256": evidence["runtime_model_sha256"],
        "evidence": {
            "heldout_n": evidence["heldout_test"]["n"],
            "heldout_precision": evidence["heldout_test"]["precision"],
            "heldout_recall": evidence["heldout_test"]["recall"],
            "precision_ci95": evidence["heldout_ci95_bootstrap"]["precision"],
            "recall_ci95": evidence["heldout_ci95_bootstrap"]["recall"],
            "base_return_rate": evidence["heldout_test"]["prevalence"],
            "precision_lift": evidence["heldout_test"]["precision_lift_vs_prevalence"],
        },
        "note": "Real-data return-risk detector; advisory only. Merchant false-positive cost is reported only when explicitly supplied.",
    }


def detector_status() -> dict:
    model = load_model()
    evidence = evidence_summary()
    return {
        "ready": True,
        "loss_class": "RETURN_TO_SELLER",
        "model_version": "amazon-return-risk-v2",
        "family": model["f"],
        "class_weight": "balanced",
        "score_is_calibrated_probability": False,
        "threshold": model["t"],
        "source_zip_sha256": model["s"],
        "runtime_model_sha256": evidence["runtime_model_sha256"],
        "dataset": evidence["dataset"],
        "heldout_test": evidence["heldout_test"],
        "heldout_ci95_bootstrap": evidence["heldout_ci95_bootstrap"],
        "ranking_checks": evidence["ranking_checks"],
        "false_positive_cost_disclosure": evidence["false_positive_cost_disclosure"],
        "limitations": evidence["limitations"],
    }
