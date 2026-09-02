"""Frozen pin table. High-RTO belts are Bihar / east-UP / NE — not a model."""

PIN_RTO = {
    "560001": {"rate": 0.06, "city": "Bengaluru GPO", "band": "low"},
    "560038": {"rate": 0.05, "city": "Indiranagar", "band": "low"},
    "560034": {"rate": 0.06, "city": "Koramangala", "band": "low"},
    "400001": {"rate": 0.07, "city": "Mumbai Fort", "band": "low"},
    "400050": {"rate": 0.06, "city": "Bandra West", "band": "low"},
    "110001": {"rate": 0.08, "city": "New Delhi GPO", "band": "low"},
    "110017": {"rate": 0.07, "city": "Malviya Nagar", "band": "low"},
    "600001": {"rate": 0.07, "city": "Chennai GPO", "band": "low"},
    "500001": {"rate": 0.06, "city": "Hyderabad GPO", "band": "low"},
    "411001": {"rate": 0.07, "city": "Pune GPO", "band": "low"},
    "380001": {"rate": 0.08, "city": "Ahmedabad", "band": "low"},
    "700001": {"rate": 0.15, "city": "Kolkata GPO", "band": "mid"},
    "226001": {"rate": 0.16, "city": "Lucknow", "band": "mid"},
    "302001": {"rate": 0.14, "city": "Jaipur", "band": "mid"},
    "201301": {"rate": 0.12, "city": "Noida", "band": "mid"},
    "122001": {"rate": 0.13, "city": "Gurugram", "band": "mid"},
    "462001": {"rate": 0.17, "city": "Bhopal", "band": "mid"},
    "800001": {"rate": 0.22, "city": "Patna", "band": "mid"},
    "841226": {"rate": 0.41, "city": "Siwan", "band": "high"},
    "848101": {"rate": 0.37, "city": "Samastipur", "band": "high"},
    "854301": {"rate": 0.38, "city": "Purnia", "band": "high"},
    "846004": {"rate": 0.39, "city": "Darbhanga", "band": "high"},
    "277001": {"rate": 0.36, "city": "Ballia", "band": "high"},
    "271001": {"rate": 0.32, "city": "Gonda", "band": "high"},
    "786001": {"rate": 0.33, "city": "Dibrugarh", "band": "high"},
    "795001": {"rate": 0.35, "city": "Imphal", "band": "high"},
}

HIGH_PREFIX = {"80", "84", "85", "24", "27", "28", "78", "79", "75", "76"}
LOW_PREFIX = {"56", "40", "11", "60", "50", "41", "38", "12", "16", "39"}


def is_valid_pincode(pincode: str) -> bool:
    pin = (pincode or "").strip()
    return pin.isdigit() and len(pin) == 6


def rto_for_pin(pincode: str) -> dict:
    pin = (pincode or "").strip()
    if not is_valid_pincode(pin):
        return {"rate": None, "city": None, "band": "invalid"}
    known = PIN_RTO.get(pin)
    if known:
        return known
    prefix = pin[:2]
    if prefix in HIGH_PREFIX:
        return {"rate": 0.34, "city": None, "band": "high"}
    if prefix in LOW_PREFIX:
        return {"rate": 0.07, "city": None, "band": "low"}
    return {"rate": 0.18, "city": None, "band": "mid"}
