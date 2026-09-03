"""CodGate application wrapper with Counterfactual Risk Repair + Risk Canary.

The frozen transaction policy remains in app.main/app.policy. This wrapper only
adds read-only repair analysis and an internal release verifier that compares
precomputed CURRENT vs CANDIDATE decisions. It never implements a second RTO
model or silently changes a transaction decision.
"""

from __future__ import annotations

import json

from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from .canary import demo_release, evaluate_release
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


@app.post("/release/check", tags=["razorpay-internal"])
def release_check(payload: ReleasePayload):
    """Gate a candidate RTO policy/model release using precomputed outputs."""
    try:
        return evaluate_release(payload.release_id, [row.model_dump() for row in payload.rows])
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/release/demo/{scenario}", tags=["razorpay-internal"])
def release_demo(scenario: str):
    """Three judge fixtures: safe candidate, wide candidate, bad candidate."""
    try:
        return demo_release(scenario)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.middleware("http")
async def attach_risk_repair(request: Request, call_next):
    """Attach repair proof without changing decision or execution semantics."""
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
            if "/static/repair.js" not in body:
                body = body.replace("</body>", '<script src="/static/repair.js"></script>\n</body>')
            if "/static/canary.js" not in body:
                body = body.replace("</body>", '<script src="/static/canary.js"></script>\n</body>')
            return HTMLResponse(content=body, status_code=response.status_code)

    return response
