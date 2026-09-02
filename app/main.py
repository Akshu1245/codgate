"""CodGate HTTP desk: score, enforce, receipt, audit, outcomes and metrics."""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, Header, HTTPException, Response
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

from .cases import CANONICAL_CASES
from .ops import (
    IdempotencyConflict,
    append_chained_audit,
    canonical_json,
    decision_receipt,
    default_execution_mode,
    lookup_idempotency,
    policy_manifest,
    read_jsonl,
    request_fingerprint,
    resolve_execution_mode,
    store_idempotency,
    verify_audit_chain,
)
from .payment import issue_payment_link
from .policy import decide
from .score import (
    CSV_PATH,
    FALSE_BLOCK_INR,
    HELDOUT_SHA256,
    MISSED_RTO_INR,
    evaluate,
    frozen_block,
)

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "static"
DATA_DIR = Path(os.getenv("CODGATE_DATA_DIR", str(ROOT))).expanduser()
DATA_DIR.mkdir(parents=True, exist_ok=True)
AUDIT = DATA_DIR / "audit.jsonl"
OUTCOMES = DATA_DIR / "outcomes.jsonl"
PAYMENT_EVENTS = DATA_DIR / "payment_events.jsonl"
IDEMPOTENCY_PATH = DATA_DIR / "idempotency.jsonl"

WHAT_BROKE = [
    "Complete address on a high-RTO pin scores 47 — under 50 — so Siwan with a house number still ships COD (h42–h45, h80).",
    "Metro prepaid veterans still RTO; credits drive score to 0 (h13, h14). Prior RTO on a veteran phone is cancelled by C1–C4 (h71–h73).",
    "Temple drops that delivered get blocked (h25–h27, h34–h35). Short mid-pin addresses over-block (h60, h62). Landmark + high ticket on a metro pin is the ugly FP (h76).",
]

app = FastAPI(
    title="CodGate",
    version="v1.0",
    description="Auditable COD RTO policy gate. The model signal is upstream; this service governs the action.",
)
app.mount("/static", StaticFiles(directory=STATIC), name="static")


class OrderPayload(BaseModel):
    """HTTP-boundary validation only; decide() still receives a plain dict."""

    model_config = ConfigDict(extra="ignore")

    order_id: str | None = Field(default=None, max_length=160)
    pincode: str | int | None = ""
    address: str | None = ""
    amount: float | int | None = 0
    account_age_days: int = Field(default=0, ge=0)
    prepaid_orders: int = Field(default=0, ge=0)
    prior_rto_count: int = Field(default=0, ge=0)
    orders_count: int = Field(default=0, ge=0)
    phone: str | int | None = ""
    customer_name: str | None = ""


class OutcomePayload(BaseModel):
    """Observed fulfilment outcome; stored separately from the frozen benchmark."""

    model_config = ConfigDict(extra="ignore")

    outcome: Literal["DELIVERED", "RTO"]
    source: str | None = Field(default="courier", max_length=60)
    note: str | None = Field(default=None, max_length=280)


def _append_jsonl(path: Path, entry: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(canonical_json(entry) + "\n")
        handle.flush()


def _payment_paid(link_id: str) -> bool:
    return any(
        row.get("event") == "paid" and row.get("payment_link") == link_id
        for row in read_jsonl(PAYMENT_EVENTS)
    )


def _known_payment_link(link_id: str) -> bool:
    return any(row.get("payment_link") == link_id for row in read_jsonl(AUDIT))


def _latest_decision(order_id: str) -> dict | None:
    for row in reversed(read_jsonl(AUDIT)):
        if row.get("order_id") == order_id and row.get("decision") in {"ALLOW_COD", "FORCE_PREPAID", "STOP"}:
            return row
    return None


def _latest_outcome(order_id: str) -> dict | None:
    for row in reversed(read_jsonl(OUTCOMES)):
        if row.get("order_id") == order_id and row.get("outcome") in {"DELIVERED", "RTO"}:
            return row
    return None


def _payment_provider_status() -> dict:
    key_id = os.getenv("RAZORPAY_KEY_ID", "").strip()
    key_secret = os.getenv("RAZORPAY_KEY_SECRET", "").strip()
    test_ready = bool(key_id.startswith("rzp_test_") and key_secret)
    return {
        "mode": "razorpay_test" if test_ready else "simulated",
        "test_keys_present": test_ready,
        "live_keys_accepted": False,
        "note": (
            "Razorpay test Payment Links will be issued in enforce mode."
            if test_ready
            else "No usable Razorpay test keys are present; plink_SIMULATED is explicit."
        ),
    }


def _action_for(result: dict, mode: str) -> str:
    if result["decision"] == "STOP":
        return "STOP"
    if mode == "shadow":
        return "OBSERVE_ONLY"
    return result["decision"]


def _response_payload(
    *,
    result: dict,
    order: dict,
    mode: str,
    receipt_id: str,
    request_sha256: str,
    payment_link_url: str | None,
    payment_link_mode: str | None,
    payment_link_note: str | None,
    audit_entry_hash: str | None,
    idempotent_replay: bool,
) -> dict:
    manifest = policy_manifest()
    return {
        **result,
        "order": order,
        "execution_mode": mode,
        "action": _action_for(result, mode),
        "would_issue_payment_link": result["decision"] == "FORCE_PREPAID",
        "receipt_id": receipt_id,
        "request_sha256": request_sha256,
        "policy_source_sha256": manifest["policy_source_sha256"],
        "audit_entry_hash": audit_entry_hash,
        "idempotent_replay": idempotent_replay,
        "payment_link_url": payment_link_url,
        "payment_link_mode": payment_link_mode,
        "payment_link_note": payment_link_note,
    }


def _live_metrics() -> dict:
    latest_decisions: dict[str, dict] = {}
    for row in read_jsonl(AUDIT):
        order_id = str(row.get("order_id") or "")
        if order_id and row.get("decision") in {"ALLOW_COD", "FORCE_PREPAID", "STOP"}:
            latest_decisions[order_id] = row

    latest_outcomes: dict[str, dict] = {}
    for row in read_jsonl(OUTCOMES):
        order_id = str(row.get("order_id") or "")
        if order_id and row.get("outcome") in {"DELIVERED", "RTO"}:
            latest_outcomes[order_id] = row

    eligible = {order_id: row for order_id, row in latest_decisions.items() if row.get("decision") != "STOP"}
    matched = [(row, latest_outcomes[order_id]) for order_id, row in eligible.items() if order_id in latest_outcomes]

    tp = fp = fn = tn = 0
    for decision, outcome in matched:
        predicted = decision.get("decision") == "FORCE_PREPAID"
        actual = outcome.get("outcome") == "RTO"
        if predicted and actual:
            tp += 1
        elif predicted and not actual:
            fp += 1
        elif not predicted and actual:
            fn += 1
        else:
            tn += 1

    precision = None if tp + fp == 0 else tp / (tp + fp)
    recall = None if tp + fn == 0 else tp / (tp + fn)

    paid_links = {
        str(row.get("payment_link"))
        for row in read_jsonl(PAYMENT_EVENTS)
        if row.get("event") == "paid" and row.get("payment_link")
    }
    enforced_forces = [
        row
        for row in eligible.values()
        if row.get("decision") == "FORCE_PREPAID"
        and row.get("execution_mode") == "enforce"
        and row.get("payment_link")
    ]
    prepaid_paid = sum(1 for row in enforced_forces if str(row.get("payment_link")) in paid_links)

    return {
        "eligible_decisions": len(eligible),
        "observed_outcomes": len(matched),
        "coverage": 0 if not eligible else len(matched) / len(eligible),
        "precision": precision,
        "recall": recall,
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "tn": tn,
        "false_block_inr": fp * FALSE_BLOCK_INR,
        "missed_rto_inr": fn * MISSED_RTO_INR,
        "shadow_decisions": sum(1 for row in eligible.values() if row.get("execution_mode") == "shadow"),
        "enforced_force_prepaid": len(enforced_forces),
        "prepaid_paid": prepaid_paid,
        "prepaid_conversion_rate": None if not enforced_forces else prepaid_paid / len(enforced_forces),
        "note": "Observed-outcome metrics are separate from the frozen held-out benchmark and never edit its labels.",
    }


@app.get("/", include_in_schema=False)
def desk():
    """Inject a tiny operational overlay without creating a second frontend policy."""
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    html = html.replace("</body>", '<script src="/static/ops.js"></script>\n</body>')
    return HTMLResponse(html)


@app.get("/pay/{link_id}", include_in_schema=False)
def pay_page(link_id: str):
    return FileResponse(STATIC / "pay.html")


@app.get("/health")
def health():
    manifest = policy_manifest()
    return {
        "ok": True,
        "policy": "v1.0",
        "execution_mode_default": default_execution_mode(),
        "policy_source_sha256": manifest["policy_source_sha256"],
        "heldout_sha256": HELDOUT_SHA256,
        "runtime_store": str(DATA_DIR),
    }


@app.get("/ready")
def ready(response: Response):
    report = evaluate()
    audit = verify_audit_chain(AUDIT)
    outcomes = verify_audit_chain(OUTCOMES)
    is_ready = bool(report["sha_matches"] and audit["verified"] and outcomes["verified"])
    if not is_ready:
        response.status_code = 503
    return {
        "ready": is_ready,
        "heldout_sha_matches": report["sha_matches"],
        "audit_chain_verified": audit["verified"],
        "audit_coverage": audit["coverage"],
        "outcome_chain_verified": outcomes["verified"],
        "payment_provider": _payment_provider_status()["mode"],
        "runtime_store": str(DATA_DIR),
    }


@app.get("/cases")
def cases():
    return CANONICAL_CASES


@app.get("/policy/manifest")
def policy_provenance():
    return {**policy_manifest(), "heldout_sha256": HELDOUT_SHA256}


@app.get("/ops/status")
def ops_status():
    report = evaluate()
    audit = verify_audit_chain(AUDIT)
    outcomes = verify_audit_chain(OUTCOMES)
    return {
        "ready": bool(report["sha_matches"] and audit["verified"] and outcomes["verified"]),
        "execution_mode_default": default_execution_mode(),
        "rollout_modes": ["shadow", "enforce"],
        "idempotency_header": "Idempotency-Key",
        "mode_header": "X-CodGate-Mode",
        "runtime_store": str(DATA_DIR),
        "payment_provider": _payment_provider_status(),
        "policy": {**policy_manifest(), "heldout_sha256": HELDOUT_SHA256},
        "audit": audit,
        "outcomes": outcomes,
        "live_metrics": _live_metrics(),
    }


@app.post("/orders/score")
def score_order(
    payload: OrderPayload,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key", max_length=200),
    x_codgate_mode: str | None = Header(default=None, alias="X-CodGate-Mode", max_length=20),
):
    try:
        mode = resolve_execution_mode(x_codgate_mode)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    order = payload.model_dump()
    if not order.get("order_id"):
        order["order_id"] = f"ord_api_{uuid.uuid4().hex[:12]}"

    request_sha256 = request_fingerprint(order, mode)

    if idempotency_key:
        try:
            replay = lookup_idempotency(IDEMPOTENCY_PATH, idempotency_key, request_sha256)
        except IdempotencyConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        if replay is not None:
            result = decide(order)
            result["payment_link"] = replay.get("payment_link")
            return _response_payload(
                result=result,
                order=order,
                mode=mode,
                receipt_id=str(replay["receipt_id"]),
                request_sha256=request_sha256,
                payment_link_url=replay.get("payment_link_url"),
                payment_link_mode=replay.get("payment_link_mode"),
                payment_link_note=replay.get("payment_link_note"),
                audit_entry_hash=replay.get("audit_entry_hash"),
                idempotent_replay=True,
            )

    result = decide(order)
    receipt_id = decision_receipt(order, result)

    payment_link_url = None
    payment_link_mode = None
    payment_link_note = None
    if result["decision"] == "FORCE_PREPAID":
        if mode == "enforce":
            issued = issue_payment_link(order, receipt_id=receipt_id)
            result["payment_link"] = issued.link_id
            payment_link_url = issued.url
            payment_link_mode = issued.mode
            payment_link_note = issued.note
        else:
            payment_link_mode = "shadow"
            payment_link_note = "Shadow mode: FORCE_PREPAID was recommended but no Payment Link was issued."

    audit_entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "event": "decision",
        "receipt_id": receipt_id,
        "request_sha256": request_sha256,
        "order_id": order.get("order_id"),
        "decision": result["decision"],
        "action": _action_for(result, mode),
        "execution_mode": mode,
        "points": result["points"],
        "threshold": result["threshold"],
        "rules": [rule["id"] for rule in result["rules"]],
        "pincode": order.get("pincode"),
        "amount": order.get("amount"),
        "would_issue_payment_link": result["decision"] == "FORCE_PREPAID",
        "payment_link": result.get("payment_link"),
        "payment_link_mode": payment_link_mode,
        "policy_version": result["policy_version"],
        "policy_source_sha256": policy_manifest()["policy_source_sha256"],
    }
    chained = append_chained_audit(AUDIT, audit_entry)

    response_payload = _response_payload(
        result=result,
        order=order,
        mode=mode,
        receipt_id=receipt_id,
        request_sha256=request_sha256,
        payment_link_url=payment_link_url,
        payment_link_mode=payment_link_mode,
        payment_link_note=payment_link_note,
        audit_entry_hash=chained["entry_hash"],
        idempotent_replay=False,
    )

    if idempotency_key:
        store_idempotency(
            IDEMPOTENCY_PATH,
            idempotency_key,
            request_sha256,
            {
                "receipt_id": receipt_id,
                "payment_link": result.get("payment_link"),
                "payment_link_url": payment_link_url,
                "payment_link_mode": payment_link_mode,
                "payment_link_note": payment_link_note,
                "audit_entry_hash": chained["entry_hash"],
            },
        )

    return response_payload


@app.post("/orders/{order_id}/outcome")
def record_outcome(order_id: str, payload: OutcomePayload):
    decision = _latest_decision(order_id)
    if decision is None:
        raise HTTPException(status_code=404, detail="No CodGate decision exists for this order_id.")

    existing = _latest_outcome(order_id)
    if existing is not None:
        if existing.get("outcome") == payload.outcome:
            return {**existing, "idempotent_replay": True}
        raise HTTPException(status_code=409, detail="This order already has a different observed outcome.")

    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "event": "outcome",
        "order_id": order_id,
        "receipt_id": decision.get("receipt_id"),
        "policy_version": decision.get("policy_version"),
        "decision": decision.get("decision"),
        "execution_mode": decision.get("execution_mode", "legacy"),
        "outcome": payload.outcome,
        "source": payload.source or "courier",
        "note": payload.note,
    }
    chained = append_chained_audit(OUTCOMES, entry)
    return {**chained, "idempotent_replay": False}


@app.get("/outcomes")
def read_outcomes():
    return list(reversed(read_jsonl(OUTCOMES)))


@app.get("/outcomes/verify")
def verify_outcomes():
    return verify_audit_chain(OUTCOMES)


@app.get("/outcomes.jsonl")
def download_outcomes():
    if not OUTCOMES.exists():
        OUTCOMES.touch()
    return FileResponse(OUTCOMES, media_type="application/jsonl", filename="outcomes.jsonl")


@app.get("/audit")
def read_audit():
    return list(reversed(read_jsonl(AUDIT)))


@app.get("/audit/verify")
def verify_audit():
    return verify_audit_chain(AUDIT)


@app.get("/audit.jsonl")
def download_audit():
    if not AUDIT.exists():
        AUDIT.touch()
    return FileResponse(AUDIT, media_type="application/jsonl", filename="audit.jsonl")


@app.get("/metrics")
def metrics():
    report = evaluate()
    return {
        **report,
        "expected_sha256": HELDOUT_SHA256,
        "frozen_block": frozen_block(report),
        "what_broke": WHAT_BROKE,
    }


@app.get("/metrics/live")
def live_metrics():
    return _live_metrics()


@app.get("/data/heldout.csv")
def heldout_csv():
    return FileResponse(CSV_PATH, media_type="text/csv", filename="heldout.csv")


@app.get("/payment-links/{link_id}")
def payment_link_state(link_id: str):
    return {
        "payment_link": link_id,
        "known": _known_payment_link(link_id),
        "paid": _payment_paid(link_id),
        "simulated": link_id.startswith("plink_SIMULATED_"),
    }


@app.post("/payment-links/{link_id}/paid")
def mark_payment_paid(link_id: str):
    if not link_id.startswith("plink_SIMULATED_"):
        raise HTTPException(status_code=409, detail="Only simulated links can be marked paid here.")
    if not _known_payment_link(link_id):
        raise HTTPException(status_code=404, detail="This simulated Payment Link was not issued by CodGate.")
    if not _payment_paid(link_id):
        _append_jsonl(
            PAYMENT_EVENTS,
            {"ts": datetime.now(timezone.utc).isoformat(), "event": "paid", "payment_link": link_id},
        )
    return {"payment_link": link_id, "paid": True, "message": "Payment recorded. COD will not be collected."}
