"""Operational controls around the frozen CodGate policy.

Nothing in this module changes policy weights or calls decide(). It only makes
HTTP-layer execution traceable, replay-safe and auditable.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
POLICY_FILES = ("app/policy.py", "app/features.py", "app/pincodes.py")
POLICY_FROZEN_DATE = "2026-09-02"
IDEMPOTENCY = ROOT / "idempotency.jsonl"

_AUDIT_LOCK = threading.Lock()
_IDEMPOTENCY_LOCK = threading.Lock()


class IdempotencyConflict(ValueError):
    """The same idempotency key was reused for a different request."""


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_json(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _source_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@lru_cache(maxsize=1)
def policy_manifest() -> dict:
    components = {name: _source_sha256(ROOT / name) for name in POLICY_FILES}
    return {
        "policy_version": "v1.0",
        "frozen_date": POLICY_FROZEN_DATE,
        "threshold": 50,
        "components": components,
        "policy_source_sha256": sha256_json(components),
    }


def default_execution_mode() -> str:
    raw = os.getenv("CODGATE_MODE", "enforce").strip().lower()
    return raw if raw in {"enforce", "shadow"} else "enforce"


def resolve_execution_mode(requested: str | None) -> str:
    mode = (requested or default_execution_mode()).strip().lower()
    if mode not in {"enforce", "shadow"}:
        raise ValueError("X-CodGate-Mode must be 'enforce' or 'shadow'.")
    return mode


def request_fingerprint(order: dict, mode: str) -> str:
    return sha256_json({"order": order, "execution_mode": mode})


def decision_receipt(order: dict, result: dict) -> str:
    manifest = policy_manifest()
    material = {
        "order_sha256": sha256_json(order),
        "policy_version": result["policy_version"],
        "policy_source_sha256": manifest["policy_source_sha256"],
        "decision": result["decision"],
        "points": result["points"],
        "threshold": result["threshold"],
        "rules": [{"id": rule["id"], "points": rule["points"]} for rule in result["rules"]],
    }
    return "cgr_" + sha256_json(material)[:20]


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def _append_jsonl(path: Path, entry: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(canonical_json(entry) + "\n")
        handle.flush()


def _last_chain_hash(path: Path) -> str:
    for row in reversed(read_jsonl(path)):
        entry_hash = row.get("entry_hash")
        if entry_hash:
            return str(entry_hash)
    return "GENESIS"


def append_chained_audit(path: Path, entry: dict) -> dict:
    """Append one SHA-256 chained audit entry without rewriting prior rows."""
    with _AUDIT_LOCK:
        payload = dict(entry)
        payload.pop("entry_hash", None)
        payload["prev_hash"] = _last_chain_hash(path)
        payload["entry_hash"] = sha256_json(payload)
        _append_jsonl(path, payload)
        return payload


def verify_audit_chain(path: Path) -> dict:
    if not path.exists():
        return {
            "verified": True,
            "hashed_rows": 0,
            "legacy_rows": 0,
            "tail_hash": "GENESIS",
            "first_error": None,
            "coverage": "empty",
        }

    chain_head = "GENESIS"
    hashed_rows = 0
    legacy_rows = 0
    chain_started = False

    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            return {
                "verified": False,
                "hashed_rows": hashed_rows,
                "legacy_rows": legacy_rows,
                "tail_hash": chain_head,
                "first_error": f"line {line_no}: invalid JSON",
                "coverage": "broken",
            }
        if not isinstance(row, dict):
            return {
                "verified": False,
                "hashed_rows": hashed_rows,
                "legacy_rows": legacy_rows,
                "tail_hash": chain_head,
                "first_error": f"line {line_no}: audit row is not an object",
                "coverage": "broken",
            }

        actual_hash = row.get("entry_hash")
        if not actual_hash:
            if chain_started:
                return {
                    "verified": False,
                    "hashed_rows": hashed_rows,
                    "legacy_rows": legacy_rows,
                    "tail_hash": chain_head,
                    "first_error": f"line {line_no}: unhashed row appears after hash chain started",
                    "coverage": "broken",
                }
            legacy_rows += 1
            continue

        chain_started = True
        if row.get("prev_hash") != chain_head:
            return {
                "verified": False,
                "hashed_rows": hashed_rows,
                "legacy_rows": legacy_rows,
                "tail_hash": chain_head,
                "first_error": f"line {line_no}: prev_hash mismatch",
                "coverage": "broken",
            }

        material = dict(row)
        material.pop("entry_hash", None)
        expected_hash = sha256_json(material)
        if actual_hash != expected_hash:
            return {
                "verified": False,
                "hashed_rows": hashed_rows,
                "legacy_rows": legacy_rows,
                "tail_hash": chain_head,
                "first_error": f"line {line_no}: entry_hash mismatch",
                "coverage": "broken",
            }

        hashed_rows += 1
        chain_head = str(actual_hash)

    coverage = "full" if legacy_rows == 0 else "new-rows-only"
    return {
        "verified": True,
        "hashed_rows": hashed_rows,
        "legacy_rows": legacy_rows,
        "tail_hash": chain_head,
        "first_error": None,
        "coverage": coverage,
    }


def _idempotency_key_hash(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def lookup_idempotency(path: Path, key: str, request_sha256: str) -> dict | None:
    key_hash = _idempotency_key_hash(key)
    for row in reversed(read_jsonl(path)):
        if row.get("key_sha256") != key_hash:
            continue
        if row.get("request_sha256") != request_sha256:
            raise IdempotencyConflict("Idempotency-Key was already used for a different request.")
        return row
    return None


def store_idempotency(path: Path, key: str, request_sha256: str, response_meta: dict) -> dict:
    """Persist replay metadata only; do not duplicate customer PII in this store."""
    row = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "key_sha256": _idempotency_key_hash(key),
        "request_sha256": request_sha256,
        **response_meta,
    }
    with _IDEMPOTENCY_LOCK:
        existing = lookup_idempotency(path, key, request_sha256)
        if existing is not None:
            return existing
        _append_jsonl(path, row)
    return row
