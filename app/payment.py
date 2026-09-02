"""Payment Link issuing belongs to the HTTP layer, never to decide()."""

from __future__ import annotations

import base64
import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass


@dataclass(frozen=True)
class PaymentLink:
    link_id: str
    url: str
    mode: str
    note: str


def _simulated(order: dict, reason: str) -> PaymentLink:
    link_id = f"plink_SIMULATED_{order['order_id']}"
    return PaymentLink(
        link_id=link_id,
        url=f"/pay/{link_id}",
        mode="simulated",
        note=reason,
    )


def issue_payment_link(order: dict) -> PaymentLink:
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

    payload = {
        "amount": int(round(float(order.get("amount") or 0) * 100)),
        "currency": "INR",
        "accept_partial": False,
        "description": f"CodGate prepaid to ship · {order['order_id']}",
        "customer": {
            "name": str(order.get("customer_name") or "COD customer"),
            "contact": str(order.get("phone") or ""),
        },
        "notify": {"sms": False, "email": False},
        "reminder_enable": False,
        "notes": {"order_id": order["order_id"], "policy_version": "v1.0"},
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
            note="Razorpay test Payment Link issued. Test mode only; no live money movement.",
        )
    except (urllib.error.URLError, urllib.error.HTTPError, KeyError, ValueError, json.JSONDecodeError) as exc:
        return _simulated(
            order,
            f"Razorpay test-link request failed ({type(exc).__name__}); using plink_SIMULATED openly. Nothing is charged.",
        )
