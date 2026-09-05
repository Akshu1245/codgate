"""CodGate application wrapper with repair, release and evidence governance.

The transaction policy remains deterministic and side-effect free. This wrapper
adds read-only customer-correctability analysis plus two release controls:
- paired current-vs-candidate Risk Canary, and
- frozen public real-data evidence status.

Neither verifier predicts an order or silently changes a checkout decision.
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
    try:
        return real_evidence_status()
    except (ValueError, OSError, json.JSONDecodeError, KeyError) as exc:
        raise HTTPException(status_code=503, detail=f"Real RTO evidence is invalid: {exc}") from exc


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
    """Attach repair proof and UI helpers without changing decision semantics."""
    response = await call_next(request)

    if request.method == "POST" and request.url.path == "/orders/score" and response.status_code == 200:
        chunks = [chunk async for chunk in response.body_iterator]
        body = b"".join(chunks)
        try:
            data = json.loads(body)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return response
        order = data.get("order")
        if isinstance(order, dict):
            data["risk_repair"] = analyze_repair(order)
        return JSONResponse(content=data, status_code=response.status_code)

    if request.method == "GET" and request.url.path == "/" and response.status_code == 200:
        content_type = response.headers.get("content-type", "")
        if content_type.startswith("text/html"):
            chunks = [chunk async for chunk in response.body_iterator]
            body = b"".join(chunks).decode("utf-8")
            scripts = ("/static/repair.js", "/static/canary.js", "/static/real_evidence.js")
            for script in scripts:
                if script not in body:
                    body = body.replace("</body>", f'<script src="{script}"></script>\n</body>')
            return HTMLResponse(content=body, status_code=response.status_code)

    return response
