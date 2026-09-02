"""CodGate application wrapper with Counterfactual Risk Repair.

The frozen policy remains in app.main/app.policy. This wrapper only augments
successful score responses with a deterministic repair analysis and exposes a
read-only /orders/repair endpoint.
"""

from __future__ import annotations

import json

from fastapi import Request
from fastapi.responses import HTMLResponse, JSONResponse

from .main import OrderPayload, app
from .repair import analyze_repair


@app.post("/orders/repair", tags=["risk-repair"])
def repair_order(payload: OrderPayload):
    """Analyze whether legitimate customer correction can restore COD."""
    return analyze_repair(payload.model_dump())


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
            return HTMLResponse(content=body, status_code=response.status_code)

    return response
