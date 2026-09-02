import json

from fastapi.testclient import TestClient

import app.main as main
from app.cases import CANONICAL_CASES
from app.ops import append_chained_audit, policy_manifest, verify_audit_chain


client = TestClient(main.app)


def _case(case_id: str) -> dict:
    return next(item for item in CANONICAL_CASES if item["id"] == case_id)["order"]


def _isolate_runtime(monkeypatch, tmp_path):
    monkeypatch.setattr(main, "AUDIT", tmp_path / "audit.jsonl")
    monkeypatch.setattr(main, "PAYMENT_EVENTS", tmp_path / "payment_events.jsonl")
    monkeypatch.setattr(main, "IDEMPOTENCY_PATH", tmp_path / "idempotency.jsonl")
    monkeypatch.delenv("RAZORPAY_KEY_ID", raising=False)
    monkeypatch.delenv("RAZORPAY_KEY_SECRET", raising=False)
    monkeypatch.delenv("CODGATE_MODE", raising=False)


def test_shadow_mode_is_non_enforcing_and_idempotent(monkeypatch, tmp_path):
    _isolate_runtime(monkeypatch, tmp_path)
    order = _case("force")
    headers = {"X-CodGate-Mode": "shadow", "Idempotency-Key": "qa-shadow-01"}

    first = client.post("/orders/score", json=order, headers=headers)
    assert first.status_code == 200
    first_body = first.json()
    assert first_body["decision"] == "FORCE_PREPAID"
    assert first_body["execution_mode"] == "shadow"
    assert first_body["action"] == "OBSERVE_ONLY"
    assert first_body["would_issue_payment_link"] is True
    assert first_body["payment_link"] is None
    assert first_body["idempotent_replay"] is False
    assert first_body["receipt_id"].startswith("cgr_")
    assert first_body["audit_entry_hash"]

    second = client.post("/orders/score", json=order, headers=headers)
    assert second.status_code == 200
    second_body = second.json()
    assert second_body["receipt_id"] == first_body["receipt_id"]
    assert second_body["audit_entry_hash"] == first_body["audit_entry_hash"]
    assert second_body["idempotent_replay"] is True

    audit_lines = (tmp_path / "audit.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(audit_lines) == 1

    changed = {**order, "amount": order["amount"] + 1}
    conflict = client.post("/orders/score", json=changed, headers=headers)
    assert conflict.status_code == 409


def test_enforce_issues_known_simulated_link_and_rejects_forged_one(monkeypatch, tmp_path):
    _isolate_runtime(monkeypatch, tmp_path)
    order = _case("force")

    response = client.post("/orders/score", json=order, headers={"X-CodGate-Mode": "enforce"})
    assert response.status_code == 200
    body = response.json()
    assert body["decision"] == "FORCE_PREPAID"
    assert body["action"] == "FORCE_PREPAID"
    assert body["payment_link"] == "plink_SIMULATED_ord_siwan_temple_01"
    assert body["payment_link_mode"] == "simulated"

    state = client.get(f"/payment-links/{body['payment_link']}")
    assert state.status_code == 200
    assert state.json()["known"] is True
    assert state.json()["paid"] is False

    paid = client.post(f"/payment-links/{body['payment_link']}/paid")
    assert paid.status_code == 200
    assert paid.json()["paid"] is True

    forged = client.post("/payment-links/plink_SIMULATED_never_issued/paid")
    assert forged.status_code == 404


def test_audit_hash_chain_detects_tampering(tmp_path):
    path = tmp_path / "audit.jsonl"
    append_chained_audit(path, {"event": "decision", "order_id": "a", "decision": "ALLOW_COD"})
    append_chained_audit(path, {"event": "decision", "order_id": "b", "decision": "STOP"})

    verified = verify_audit_chain(path)
    assert verified["verified"] is True
    assert verified["hashed_rows"] == 2
    assert verified["legacy_rows"] == 0
    assert verified["coverage"] == "full"

    lines = path.read_text(encoding="utf-8").splitlines()
    first = json.loads(lines[0])
    first["decision"] = "FORCE_PREPAID"
    lines[0] = json.dumps(first, separators=(",", ":"))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    broken = verify_audit_chain(path)
    assert broken["verified"] is False
    assert "entry_hash mismatch" in broken["first_error"]


def test_policy_manifest_is_source_bound():
    manifest = policy_manifest()
    assert manifest["policy_version"] == "v1.0"
    assert manifest["threshold"] == 50
    assert manifest["frozen_date"] == "2026-09-02"
    assert len(manifest["policy_source_sha256"]) == 64
    assert set(manifest["components"]) == {"app/policy.py", "app/features.py", "app/pincodes.py"}
