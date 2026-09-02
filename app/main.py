"""CodGate HTTP desk: score, Payment Link, append-only audit, metrics and static UI."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .cases import CANONICAL_CASES
from .payment import issue_payment_link
from .policy import decide
from .score import CSV_PATH, HELDOUT_SHA256, evaluate, frozen_block

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "static"
AUDIT = ROOT / "audit.jsonl"
PAYMENT_EVENTS = ROOT / "payment_events.jsonl"

WHAT_BROKE = [
    "Complete address on a high-RTO pin scores 47 — under 50 — so Siwan with a house number still ships COD (h42–h45, h80).",
    "Metro prepaid veterans still RTO; credits drive score to 0 (h13, h14). Prior RTO on a veteran phone is cancelled by C1–C4 (h71–h73).",
    "Temple drops that delivered get blocked (h25–h27, h34–h35). Short mid-pin addresses over-block (h60, h62). Landmark + high ticket on a metro pin is the ugly FP (h76).",
]

app = FastAPI(title="CodGate", version="v1.0")
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


def _append_jsonl(path: Path, entry: dict) -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False, separators=(",", ":")) + "\n")


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def _payment_paid(link_id: str) -> bool:
    return any(
        row.get("event") == "paid" and row.get("payment_link") == link_id
        for row in _read_jsonl(PAYMENT_EVENTS)
    )


@app.get("/", include_in_schema=False)
def desk():
    return FileResponse(STATIC / "index.html")


@app.get("/pay/{link_id}", include_in_schema=False)
def pay_page(link_id: str):
    return FileResponse(STATIC / "pay.html")


@app.get("/health")
def health():
    return {"ok": True, "policy": "v1.0", "heldout_sha256": HELDOUT_SHA256}


@app.get("/cases")
def cases():
    return CANONICAL_CASES


@app.post("/orders/score")
def score_order(payload: OrderPayload):
    order = payload.model_dump()
    if not order.get("order_id"):
        order["order_id"] = f"ord_api_{int(datetime.now().timestamp())}"
    result = decide(order)

    payment_link_url = None
    payment_link_mode = None
    payment_link_note = None
    if result["decision"] == "FORCE_PREPAID":
        issued = issue_payment_link(order)
        result["payment_link"] = issued.link_id
        payment_link_url = issued.url
        payment_link_mode = issued.mode
        payment_link_note = issued.note

    audit_entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "order_id": order.get("order_id"),
        "decision": result["decision"],
        "points": result["points"],
        "threshold": result["threshold"],
        "rules": [rule["id"] for rule in result["rules"]],
        "pincode": order.get("pincode"),
        "amount": order.get("amount"),
        "payment_link": result.get("payment_link"),
        "payment_link_mode": payment_link_mode,
        "policy_version": result["policy_version"],
    }
    _append_jsonl(AUDIT, audit_entry)

    return {
        **result,
        "order": order,
        "payment_link_url": payment_link_url,
        "payment_link_mode": payment_link_mode,
        "payment_link_note": payment_link_note,
    }


@app.get("/audit")
def read_audit():
    return list(reversed(_read_jsonl(AUDIT)))


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


@app.get("/data/heldout.csv")
def heldout_csv():
    return FileResponse(CSV_PATH, media_type="text/csv", filename="heldout.csv")


@app.get("/payment-links/{link_id}")
def payment_link_state(link_id: str):
    return {"payment_link": link_id, "paid": _payment_paid(link_id), "simulated": link_id.startswith("plink_SIMULATED_")}


@app.post("/payment-links/{link_id}/paid")
def mark_payment_paid(link_id: str):
    if not link_id.startswith("plink_SIMULATED_"):
        raise HTTPException(status_code=409, detail="Only simulated links can be marked paid here.")
    if not _payment_paid(link_id):
        _append_jsonl(
            PAYMENT_EVENTS,
            {"ts": datetime.now(timezone.utc).isoformat(), "event": "paid", "payment_link": link_id},
        )
    return {"payment_link": link_id, "paid": True, "message": "Payment recorded. COD will not be collected."}
