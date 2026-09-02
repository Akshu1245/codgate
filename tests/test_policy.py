from app.cases import CANONICAL_CASES
from app.policy import decide


def _case(case_id: str) -> dict:
    return next(item for item in CANONICAL_CASES if item["id"] == case_id)["order"]


def test_allow_cod():
    result = decide(_case("allow"))
    assert result["decision"] == "ALLOW_COD"
    assert result["points"] == 0
    assert [rule["id"] for rule in result["rules"]] == ["C4", "C3", "C2", "C1"]
    assert result["payment_link"] is None


def test_force_prepaid():
    result = decide(_case("force"))
    assert result["decision"] == "FORCE_PREPAID"
    assert result["points"] == 145
    assert [rule["id"] for rule in result["rules"]] == ["R4", "R5", "R6", "R7", "R8", "R9"]
    assert result["payment_link"] is None


def test_stop_pincode_56():
    order = _case("stop")
    assert order["pincode"] == "56"
    result = decide(order)
    assert result["decision"] == "STOP"
    assert result["rules"][0]["id"] == "R1"
    assert result["payment_link"] is None
