"""POST /orders/score — named rules, simulated Payment Link, append-only audit.jsonl."""

import json
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from .cases import CANONICAL_CASES
from .policy import decide

ROOT = Path(__file__).resolve().parents[1]
AUDIT = ROOT / "audit.jsonl"
STATIC = ROOT / "static"
NOTE = (
    "Razorpay keys not configured — writing plink_SIMULATED. "
    "Swap in a test key to issue a live Payment Link."
)

app = FastAPI(title="CodGate", version="v1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


def _append_audit(result: dict) -> None:
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "order_id": result["order"].get("order_id"),
        "decision": result["decision"],
        "points": result["points"],
        "rules": [r["id"] for r in result["rules"]],
        "pincode": result["order"].get("pincode"),
        "amount": result["order"].get("amount"),
        "payment_link": result.get("payment_link"),
        "policy_version": result["policy_version"],
    }
    with AUDIT.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


@app.get("/", include_in_schema=False)
def desk():
    return FileResponse(STATIC / "index.html")


@app.get("/health")
def health():
    return {"ok": True, "policy": "v1.0"}


@app.get("/cases")
def cases():
    return CANONICAL_CASES


@app.get("/audit")
def read_audit():
    if not AUDIT.exists():
        return []
    rows = []
    for line in AUDIT.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


@app.post("/orders/score")
def score_order(order: dict):
    order.setdefault("order_id", f"ord_api_{int(datetime.now().timestamp())}")
    result = decide(order)
    if result["decision"] == "FORCE_PREPAID":
        result["payment_link"] = f"plink_SIMULATED_{order['order_id']}"
        result["payment_link_note"] = NOTE
    _append_audit(result)
    return result
