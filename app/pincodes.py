"""Frozen location-policy bands used only for deterministic control regression.

These bands are NOT measured RTO rates and are not presented as empirical data.
They preserve the original prototype policy's deterministic behavior while real
model evidence is evaluated separately by app.evidence_gate / Risk Canary.
"""

PIN_POLICY = {
    "560001": {"city": "Bengaluru GPO", "band": "low"},
    "560038": {"city": "Indiranagar", "band": "low"},
    "560034": {"city": "Koramangala", "band": "low"},
    "400001": {"city": "Mumbai Fort", "band": "low"},
    "400050": {"city": "Bandra West", "band": "low"},
    "110001": {"city": "New Delhi GPO", "band": "low"},
    "110017": {"city": "Malviya Nagar", "band": "low"},
    "600001": {"city": "Chennai GPO", "band": "low"},
    "500001": {"city": "Hyderabad GPO", "band": "low"},
    "411001": {"city": "Pune GPO", "band": "low"},
    "380001": {"city": "Ahmedabad", "band": "low"},
    "700001": {"city": "Kolkata GPO", "band": "mid"},
    "226001": {"city": "Lucknow", "band": "mid"},
    "302001": {"city": "Jaipur", "band": "mid"},
    "201301": {"city": "Noida", "band": "mid"},
    "122001": {"city": "Gurugram", "band": "mid"},
    "462001": {"city": "Bhopal", "band": "mid"},
    "800001": {"city": "Patna", "band": "mid"},
    "841226": {"city": "Siwan", "band": "high"},
    "848101": {"city": "Samastipur", "band": "high"},
    "854301": {"city": "Purnia", "band": "high"},
    "846004": {"city": "Darbhanga", "band": "high"},
    "277001": {"city": "Ballia", "band": "high"},
    "271001": {"city": "Gonda", "band": "high"},
    "786001": {"city": "Dibrugarh", "band": "high"},
    "795001": {"city": "Imphal", "band": "high"},
}

# Legacy prototype grouping retained to keep deterministic policy fixtures stable.
# The names high/mid/low are policy severity bands, not geographic RTO estimates.
HIGH_PREFIX = {"80", "84", "85", "24", "27", "28", "78", "79", "75", "76"}
LOW_PREFIX = {"56", "40", "11", "60", "50", "41", "38", "12", "16", "39"}


def is_valid_pincode(pincode: str) -> bool:
    pin = (pincode or "").strip()
    return pin.isdigit() and len(pin) == 6


def rto_for_pin(pincode: str) -> dict:
    """Return the legacy policy band without inventing an empirical RTO rate.

    Function name is retained for compatibility with the frozen v1.0 policy.
    New code should treat the returned `band` only as a deterministic control
    fixture until a merchant/Razorpay-owned location signal is supplied.
    """
    pin = (pincode or "").strip()
    if not is_valid_pincode(pin):
        return {"city": None, "band": "invalid", "rate": None, "rate_source": "none"}
    known = PIN_POLICY.get(pin)
    if known:
        return {**known, "rate": None, "rate_source": "not_measured"}
    prefix = pin[:2]
    if prefix in HIGH_PREFIX:
        return {"city": None, "band": "high", "rate": None, "rate_source": "not_measured"}
    if prefix in LOW_PREFIX:
        return {"city": None, "band": "low", "rate": None, "rate_source": "not_measured"}
    return {"city": None, "band": "mid", "rate": None, "rate_source": "not_measured"}
