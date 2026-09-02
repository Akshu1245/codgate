"""Address, pincode and customer feature extraction. Pure: no network, no I/O."""

import re

from .pincodes import is_valid_pincode, rto_for_pin

LANDMARK_RE = re.compile(
    r"\b(?:near|opp\.?|opposite|beside|behind|next\s+to)\s+"
    r"(?:the\s+)?(?:temple|mandir|mosque|masjid|church|dargah|gurudwara)\b",
    re.I,
)
HOUSE_RE = re.compile(
    r"\b(?:flat|apt|apartment|plot|house|villa|hno|h\.?\s*no\.?|#|door|floor)\b|\b\d{1,4}[a-z]?\b",
    re.I,
)
LOCALITY_RE = re.compile(
    r"\b(?:road|rd|street|st|main|nagar|layout|sector|block|cross|marg|lane|extension|extn|circle|park|stage|colony|hills)\b",
    re.I,
)


def classify_address(address: str) -> str:
    """Mutually exclusive: empty | landmark_only | short | partial | complete."""
    text = re.sub(r"\s+", " ", (address or "").strip())
    if not text:
        return "empty"

    landmark = bool(LANDMARK_RE.search(text))
    house = bool(HOUSE_RE.search(text))
    locality = bool(LOCALITY_RE.search(text))

    # Landmark check happens before the short-address check by policy.
    if landmark and not house:
        return "landmark_only"
    if len(text) < 12:
        return "short"
    if house and locality:
        return "complete"
    return "partial"


def extract_features(order: dict) -> dict:
    pin = str(order.get("pincode") or "").strip()
    pin_meta = rto_for_pin(pin)
    address = str(order.get("address") or "")
    address_class = classify_address(address)
    orders_count = int(order.get("orders_count") or 0)
    account_age_days = int(order.get("account_age_days") or 0)
    prepaid_orders = int(order.get("prepaid_orders") or 0)
    prior_rto_count = int(order.get("prior_rto_count") or 0)
    amount = float(order.get("amount") or 0)

    return {
        "pincode": pin,
        "pincode_valid": is_valid_pincode(pin),
        "pin_rto_rate": pin_meta["rate"],
        "pin_band": pin_meta["band"],
        "pin_city": pin_meta["city"],
        "address_class": address_class,
        "address_len": len(address.strip()),
        "is_new_customer": orders_count == 0 or account_age_days < 21,
        "prepaid_history": prepaid_orders > 0,
        "prepaid_veteran": prepaid_orders >= 3,
        "prior_rto_on_phone": prior_rto_count >= 1,
        "high_ticket": amount >= 3000,
        "amount": amount,
    }
