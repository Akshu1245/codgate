from pathlib import Path

from fastapi.testclient import TestClient

from app.repair_app import app


client = TestClient(app)
ROOT = Path(__file__).resolve().parents[1]


SURFACES = [
    "Control Room",
    "Decision Desk",
    "Customer Correctability",
    "Risk Canary",
    "Audit Terminal",
    "Integration Simulator",
]


def test_root_exposes_migration_console_surfaces():
    response = client.get("/")
    assert response.status_code == 200
    body = response.text

    for surface in SURFACES:
        assert surface in body

    # The redesign is an operations console, not a generic AI dashboard.
    assert "COD Risk Governance Engine" in body
    assert "Razorpay internal risk operations" in body
    assert "glassmorphism" not in body.lower()
    assert "linear-gradient" not in body.lower()


def test_console_wires_to_existing_governance_contracts():
    response = client.get("/")
    body = response.text

    for endpoint in [
        "/ops/status",
        "/orders/score",
        "/orders/repair",
        "/release/demo/",
        "/audit/verify",
        "/outcomes/verify",
    ]:
        assert endpoint in body

    # Simulator must stay side-effect safe by using shadow mode.
    assert "X-CodGate-Mode':'shadow'" in body
    assert "Shadow mode creates no Payment Link" in body


def test_legacy_ops_wrapper_cannot_override_new_console_execution_mode():
    script = (ROOT / "static" / "ops.js").read_text(encoding="utf-8")
    guard = 'if (!document.getElementById("gate")) return;'
    wrapper = "window.fetch = async"

    assert guard in script
    assert wrapper in script
    assert script.index(guard) < script.index(wrapper)
