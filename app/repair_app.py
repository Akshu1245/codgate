"""CodGate deployed application: real detector + bounded risk governance.

Primary Track-02 evidence is now the frozen real-data RETURN_TO_SELLER detector
trained/evaluated on the large public Amazon India order export.  The older
Meesho RTO result remains an independent external validation gate and correctly
blocks its weak candidate.  The original 80-row handcrafted set remains only a
software regression fixture.

No ML endpoint in this wrapper creates a Payment Link.  The real detector is
advisory/bounded: it returns STANDARD_FLOW or FLAG_RETURN_RISK/RISK_REVIEW.
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
from .return_risk_runtime import detector_status, score_return_risk


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


class ReturnRiskPayload(BaseModel):
    """Fields available at/near order creation in the real training source."""

    model_config = ConfigDict(extra="ignore")

    order_date: str = Field(min_length=8, max_length=40)
    fulfilment: str = Field(default="", max_length=80)
    sales_channel: str = Field(default="", max_length=120)
    service_level: str = Field(default="", max_length=120)
    style: str = Field(default="", max_length=240)
    sku: str = Field(default="", max_length=600)
    category: str = Field(default="", max_length=240)
    size: str = Field(default="", max_length=160)
    ship_city: str = Field(default="", max_length=160)
    ship_state: str = Field(default="", max_length=160)
    postal_code: str | int | None = ""
    b2b: bool | str | None = False
    quantity: float = Field(default=1, ge=0)
    amount: float = Field(default=0, ge=0)
    item_rows: int = Field(default=1, ge=1, le=100)
    false_positive_cost_per_order_inr: float | None = Field(default=None, ge=0)


def _safe_external_rto_evidence() -> dict:
    try:
        return real_evidence_status()
    except (ValueError, OSError, json.JSONDecodeError, KeyError) as exc:
        return {
            "verdict": "BLOCK_RELEASE",
            "claim": "External Meesho RTO evidence is unavailable or invalid",
            "reasons": [str(exc)],
            "evidence_sufficient_for_production": False,
            "raw_rows_embedded": False,
        }


def _safe_detector_status() -> dict:
    try:
        return detector_status()
    except (ValueError, RuntimeError, OSError, json.JSONDecodeError, KeyError) as exc:
        return {
            "ready": False,
            "loss_class": "RETURN_TO_SELLER",
            "model_version": "amazon-return-risk-v2",
            "error": str(exc),
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
    return analyze_repair(payload.model_dump())


@app.get("/return-risk/status", tags=["real-detector"])
def return_risk_status():
    """Frozen model integrity + real held-out evidence."""
    status = _safe_detector_status()
    if not status.get("ready"):
        raise HTTPException(status_code=503, detail=status.get("error", "return-risk model unavailable"))
    return status


@app.post("/return-risk/score", tags=["real-detector"])
def return_risk_score(payload: ReturnRiskPayload):
    """Score one order with the exact frozen real-data model.

    The result is advisory only.  It never creates a Payment Link.  If a merchant
    supplies ``false_positive_cost_per_order_inr`` CodGate also prices the frozen
    held-out false positives using that explicit merchant assumption; no default
    rupee loss is invented.
    """
    body = payload.model_dump()
    merchant_cost = body.pop("false_positive_cost_per_order_inr", None)
    try:
        return score_return_risk(body, merchant_cost)
    except (ValueError, RuntimeError, OSError, json.JSONDecodeError, KeyError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/evidence/real-rto", tags=["external-validation"])
def real_rto_evidence():
    """Independent small Meesho exact-RTO validation; currently BLOCK_RELEASE."""
    result = _safe_external_rto_evidence()
    if result.get("claim") == "External Meesho RTO evidence is unavailable or invalid":
        raise HTTPException(status_code=503, detail=result["reasons"][0])
    return result


@app.post("/release/check", tags=["risk-governance"])
def release_check(payload: ReleasePayload):
    try:
        return evaluate_release(payload.release_id, [row.model_dump() for row in payload.rows])
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/release/demo/{scenario}", tags=["risk-governance"])
def release_demo(scenario: str):
    """Deterministic regression fixtures; not accuracy evidence."""
    try:
        return demo_release(scenario)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.middleware("http")
async def attach_governance(request: Request, call_next):
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
            scripts = (
                "/static/repair.js",
                "/static/canary.js",
                "/static/real_evidence.js",
                "/static/return_risk.js",
            )
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
                "primary_evidence": _safe_detector_status(),
                "external_rto_validation": _safe_external_rto_evidence(),
                "regression_fixture": {
                    **fixture,
                    "classification": "handcrafted_synthetic_regression_fixture",
                    "claim_allowed": "deterministic software regression only",
                    "claim_forbidden": "real-world or Razorpay production accuracy",
                    "cost_values": "assumptions used for deterministic tests, not measured merchant economics",
                },
                "note": "Primary precision/recall comes from the sealed 5,726-order real holdout. Small RTO data is external validation; the old 80-row file is regression-only.",
            },
            status_code=200,
        )

    if request.method == "GET" and path == "/metrics/live" and response.status_code == 200:
        data = await _json_body(response)
        if data is None:
            return JSONResponse(status_code=500, content={"detail": "Invalid live metrics response"})
        data["legacy_cost_model"] = {
            "classification": "regression-assumption-only",
            "false_block_inr_per_case": 180,
            "missed_rto_inr_per_case": 250,
            "measured_merchant_economics": False,
        }
        data["real_detector_cost_model"] = {
            "default_false_positive_cost_inr": None,
            "merchant_supplied_required": True,
            "source_derived_heldout_false_positive_gmv_inr": _safe_detector_status().get("heldout_test", {}).get("false_positive_order_gmv_at_risk_inr"),
        }
        return JSONResponse(content=data, status_code=200)

    if request.method == "GET" and path in {"/health", "/ready", "/ops/status", "/policy/manifest"} and response.status_code in {200, 503}:
        data = await _json_body(response)
        if data is None:
            return JSONResponse(status_code=500, content={"detail": f"Invalid {path} response"})
        detector = _safe_detector_status()
        external = _safe_external_rto_evidence()
        data["return_risk_detector"] = {
            "ready": detector.get("ready", False),
            "loss_class": detector.get("loss_class", "RETURN_TO_SELLER"),
            "model_version": detector.get("model_version", "amazon-return-risk-v2"),
            "heldout_precision": detector.get("heldout_test", {}).get("precision"),
            "heldout_recall": detector.get("heldout_test", {}).get("recall"),
        }
        data["external_rto_validation"] = {
            "verdict": external.get("verdict"),
            "claim": external.get("claim"),
        }
        data["regression_fixture"] = {"classification": "handcrafted_synthetic_regression_fixture", "integrity_only": True}
        if path in {"/ready", "/ops/status"} and not detector.get("ready", False):
            if path == "/ready":
                return JSONResponse(content=data, status_code=503)
            data["ready"] = False
        return JSONResponse(content=data, status_code=response.status_code)

    if request.method == "GET" and path == "/data/heldout.csv":
        response.headers["X-CodGate-Data-Class"] = "handcrafted-synthetic-regression-fixture"
        response.headers["X-CodGate-Claim-Scope"] = "software-regression-only"
        return response

    return response
