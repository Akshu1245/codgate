from app.cases import CANONICAL_CASES
from app.policy import decide


def test_allow_cod():
    c = next(x for x in CANONICAL_CASES if x["id"] == "allow")
    result = decide(c["order"])
    assert result["decision"] == "ALLOW_COD"
    assert result["payment_link"] is None


def test_force_prepaid():
    c = next(x for x in CANONICAL_CASES if x["id"] == "force")
    result = decide(c["order"])
    assert result["decision"] == "FORCE_PREPAID"
    assert result["points"] >= result["threshold"]
    ids = {r["id"] for r in result["rules"]}
    assert "R4" in ids
    assert "R5" in ids
    assert "R8" in ids
    assert result["payment_link"] is None


def test_stop_pincode_56():
    c = next(x for x in CANONICAL_CASES if x["id"] == "stop")
    assert c["order"]["pincode"] == "56"
    result = decide(c["order"])
    assert result["decision"] == "STOP"
    assert result["rules"][0]["id"] == "R1"
