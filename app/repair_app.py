"""CodGate application wrapper with repair, release and evidence governance.

The transaction policy remains deterministic and side-effect free. This wrapper
adds read-only customer-correctability analysis plus two release controls:
- paired current-vs-candidate Risk Canary, and
- frozen public real-data evidence status.

The wrapper is also the deployed entrypoint. It explicitly classifies the old
80-row handcrafted benchmark as a regression fixture so it cannot be mistaken
for real-world model evidence.
"""

from __future__ import annotations

import json

from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from .canary import demo_release, evaluate_release
from .evidence_gate import real_evidence_status
from .main import OrderPayload, app
from .repair import analyze_repair


class ReleaseRow(BaseModel):
    model_config = ConfigDict(extra="ignore")

    order_id: str = Field(min_length=1, max_length=160)
    merchant_segment: str = Field(default="unsegmented", max_length=80)
    amount: float = Field(default=0, ge=0)
    actual_rto: bool
    current_decision: str
    candidate_decision: str


class ReleasePayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    release_id: str = Field(min_length=1, max_length=160)
    rows: list[ReleaseRow] = Field(min_length=1, max_length=10000)


def _safe_real_evidence() -> dict:
    try:
        return real_evidence_status()
    except (ValueError, OSError, json.JSONDecodeError, KeyError) as exc:
        # Evidence corruption is a release failure, not a reason to take the
        # operational service down. Fail closed for checkout rollout.
        return {
            "verdict": "BLOCK_RELEASE",
            "claim": "Real RTO evidence is unavailable or invalid",
            "reasons": [str(exc)],
            "evidence_sufficient_for_production": False,
            "raw_rows_embedded": False,
        }


async def _json_body(response) -> dict | None:
    chunks = [chunk async for chunk in response.body_iterator]
    body = b"".join(chunks)
    try:
        value = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None
    return value if isinstance(value, dict) else None


@app.post("/orders/repair", tags=["risk-repair"])
def repair_order(payload: OrderPayload):
    """Analyze whether legitimate customer correction can restore COD."""
    return analyze_repair(payload.model_dump())


@app.get("/evidence/real-rto", tags=["razorpay-internal"])
def real_rto_evidence():
    """Return frozen aggregate evidence from the public Meesho RTO evaluation.

    Raw third-party rows are intentionally not served or committed. The result is
    a release-safety verdict over the derived held-out metrics and provenance.
    """
    result = _safe_real_evidence()
    if result.get("claim") == "Real RTO evidence is unavailable or invalid":
        raise HTTPException(status_code=503, detail=result["reasons"][0])
    return result


@app.post("/release/check", tags=["razorpay-internal"])
def release_check(payload: ReleasePayload):
    """Gate a candidate RTO policy/model release using precomputed outputs."""
    try:
        return evaluate_release(payload.release_id, [row.model_dump() for row in payload.rows])
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/release/demo/{scenario}", tags=["razorpay-internal"])
def release_demo(scenario: str):
    """Three deterministic regression fixtures; these are not real-data evidence."""
    try:
        return demo_release(scenario)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.middleware("http")
async def attach_risk_repair(request: Request, call_next):
    """Attach governance metadata/UI helpers without changing decision semantics."""
    response = await call_next(request)
    path = request.url.path

    if request.method == "POST" and path == "/orders/score" and response.status_code == 200:
        data = await _json_body(response)
        if data is None:
            return JSONResponse(status_code=500, content={"detail": "Invalid score response"})
        order = data.get("order")
        if isinstance(order, dict):
            data["risk_repair"] = analyze_repair(order)
        return JSONResponse(content=data, status_code=response.status_code)

    if request.method == "GET" and path == "/" and response.status_code == 200:
        content_type = response.headers.get("content-type", "")
        if content_type.startswith("text/html"):
            chunks = [chunk async for chunk in response.body_iterator]
            body = b"".join(chunks).decode("utf-8")
            scripts = ("/static/repair.js", "/static/canary.js", "/static/real_evidence.js")
            for script in scripts:
                if script not in body:
                    body = body.replace("</body>", f'<script src="{script}"></script>\n</body>')
            return HTMLResponse(content=body, status_code=response.status_code)

    if request.method == "GET" and path == "/metrics" and response.status_code == 200:
        fixture = await _json_body(response)
        if fixture is None:
            return JSONResponse(status_code=500, content={"detail": "Invalid regression fixture metrics"})
        return JSONResponse(
            content={
                "primary_evidence": _safe_real_evidence(),
                "regression_fixture": {
                    **fixture,
                    "classification": "handcrafted_synthetic_regression_fixture",
                    "claim_allowed": "deterministic software regression only",
                    "claim_forbidden": "real-world or Razorpay production accuracy",
                    "cost_values": "assumptions used for deterministic tests, not measured merchant economics",
                },
                "note": "Primary evidence is the reproduced public-data holdout. The 80-row CSV is retained only to catch policy/test drift.",
            },
            status_code=200,
        )

    if request.method == "GET" and path == "/metrics/live" and response.status_code == 200:
        data = await _json_body(response)
        if data is None:
            return JSONResponse(status_code=500, content={"detail": "Invalid live metrics response"})
        data["cost_model"] = {
            "classification": "assumption",
            "false_block_inr_per_case": 180,
            "missed_rto_inr_per_case": 250,
            "measured_merchant_economics": False,
            "note": "Precision/recall can use observed outcomes; rupee loss fields remain scenario assumptions until merchant-specific economics are configured.",
        }
        return JSONResponse(content=data, status_code=200)

    if request.method == "GET" and path in {"/health", "/ready", "/ops/status", "/policy/manifest"} and response.status_code in {200, 503}:
        data = await _json_body(response)
        if data is None:
            return JSONResponse(status_code=500, content={"detail": f"Invalid {path} response"})
        evidence = _safe_real_evidence()
        data["real_rto_evidence"] = {
            "verdict": evidence.get("verdict"),
            "claim": evidence.get("claim"),
            "release_authorized": False,
            "reason": "Standalone evidence never authorizes SHIP; this public candidate is currently BLOCK_RELEASE.",
        }
        data["regression_fixture"] = {
            "classification": "handcrafted_synthetic_regression_fixture",
            "integrity_only": True,
        }
        if path == "/ops/status":
            data["release_authorized"] = False
            data["primary_evidence"] = evidence
        return JSONResponse(content=data, status_code=response.status_code)

    if request.method == "GET" and path == "/data/heldout.csv":
        response.headers["X-CodGate-Data-Class"] = "handcrafted-synthetic-regression-fixture"
        response.headers["X-CodGate-Claim-Scope"] = "software-regression-only"
        return response

    return response
