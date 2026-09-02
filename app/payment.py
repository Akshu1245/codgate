"""Payment Link issuing belongs to the HTTP layer, never to decide()."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import urllib.error
import urllib.request
from dataclasses import dataclass


@dataclass(frozen=True)
class PaymentLink:
    link_id: str
    url: str
    mode: str
    note: str


_UNSAFE_LINK_CHARS = re.compile(r"[^A-Za-z0-9_-]+")


def _safe_simulated_token(order_id: object) -> str:
    """Keep canonical safe ids verbatim; harden arbitrary user-supplied ids for URLs."""
    raw = str(order_id or "order")
    cleaned = _UNSAFE_LINK_CHARS.sub("_", raw).strip("_") or "order"
    cleaned = cleaned[:72]
    if cleaned == raw and len(raw) <= 72:
        return cleaned
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:8]
    return f"{cleaned}_{digest}"


def _simulated(order: dict, reason: str) -> PaymentLink:
    token = _safe_simulated_token(order.get("order_id"))
    link_id = f"plink_SIMULATED_{token}"
    return PaymentLink(
        link_id=link_id,
        url=f"/pay/{link_id}",
        mode="simulated",
        note=reason,
    )


def issue_payment_link(order: dict, receipt_id: str | None = None) -> PaymentLink:
    """Issue only a Razorpay test link; otherwise fall back explicitly to simulation."""
    key_id = os.getenv("RAZORPAY_KEY_ID", "").strip()
    key_secret = os.getenv("RAZORPAY_KEY_SECRET", "").strip()

    if not key_id or not key_secret:
        return _simulated(
            order,
            "Razorpay test keys are absent — plink_SIMULATED is explicit; nothing is charged.",
        )

    if not key_id.startswith("rzp_test_"):
        return _simulated(
            order,
            "Only Razorpay test keys are accepted by CodGate. A non-test key was ignored; nothing is charged.",
        )

    reference_id = receipt_id or f"cg_{_safe_simulated_token(order.get('order_id'))}"
    notes = {"order_id": str(order["order_id"]), "policy_version": "v1.0"}
    if receipt_id:
        notes["codgate_receipt"] = receipt_id

    payload = {
        "amount": int(round(float(order.get("amount") or 0) * 100)),
        "currency": "INR",
        "accept_partial": False,
        "reference_id": reference_id,
        "description": f"CodGate prepaid to ship · {order['order_id']}",
        "customer": {
            "name": str(order.get("customer_name") or "COD customer"),
            "contact": str(order.get("phone") or ""),
        },
        "notify": {"sms": False, "email": False},
        "reminder_enable": False,
        "notes": notes,
    }
    raw = json.dumps(payload).encode("utf-8")
    auth = base64.b64encode(f"{key_id}:{key_secret}".encode()).decode()
    request = urllib.request.Request(
        "https://api.razorpay.com/v1/payment_links",
        data=raw,
        headers={"Authorization": f"Basic {auth}", "Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=8) as response:
            body = json.loads(response.read().decode("utf-8"))
        link_id = str(body["id"])
        short_url = str(body["short_url"])
        return PaymentLink(
            link_id=link_id,
            url=short_url,
            mode="razorpay_test",
            note="Razorpay test Payment Link issued and tied to the CodGate receipt. Test mode only; no live money movement.",
        )
    except (urllib.error.URLError, urllib.error.HTTPError, KeyError, ValueError, json.JSONDecodeError) as exc:
        return _simulated(
            order,
            f"Razorpay test-link request failed ({type(exc).__name__}); using plink_SIMULATED openly. Nothing is charged.",
        )
