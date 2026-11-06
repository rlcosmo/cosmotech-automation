"""Single release decision, advisory AI, signed receipts and durable replay prevention.

No network authentication is implemented here. Identity and evidence must be
supplied by trusted server adapters, never constructed from caller JSON claims.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
import sqlite3
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

POLICY_VERSION = "cosmotech-promotion-v2-deterministic"
CRITICALITY = {"orders-api": "HIGH", "orders-worker": "MEDIUM"}
CHECKS = {
    "tests_passed", "scan_passed", "digest_verified", "sqs_compatible",
    "argo_healthy", "incidents_clear", "observability_reliable", "dependencies_reviewed",
    "sensitive_changes",
}
AI_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object", "additionalProperties": False,
    "required": ["service", "environment", "criticality", "risk_level", "recommendation",
                 "justification", "evidence", "model_version", "timestamp"],
    "properties": {
        "service": {"type": "string", "enum": list(CRITICALITY)},
        "environment": {"type": "string", "enum": ["dev"]},
        "criticality": {"type": "string", "enum": ["HIGH", "MEDIUM"]},
        "risk_level": {"type": "string", "enum": ["LOW", "MEDIUM", "HIGH", "CRITICAL"]},
        "recommendation": {"type": "string", "enum": ["APPROVE", "HUMAN_REVIEW", "REJECT"]},
        "justification": {"type": "string", "minLength": 1, "maxLength": 2000},
        "evidence": {"type": "array", "minItems": 1, "maxItems": 20,
                     "items": {"type": "string", "pattern": "^[a-z_]+$"}},
        "model_version": {"type": "string", "minLength": 1, "maxLength": 200},
        "timestamp": {"type": "string", "format": "date-time"},
    },
}


def canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def instant(value: str) -> datetime:
    result = datetime.fromisoformat(value)
    if result.tzinfo is None:
        raise ValueError("Timezone required")
    return result


def fresh(value: str, now: datetime) -> bool:
    return timedelta(0) <= now - instant(value) <= timedelta(minutes=15)


def validate_schema(value: Any, schema: dict) -> None:
    """Validate the restricted JSON Schema vocabulary used by AI_SCHEMA.

    This deliberately supports only this versioned contract, not arbitrary schemas.
    """
    kind = schema["type"]
    types = {"object": dict, "string": str, "array": list}
    if type(value) is not types[kind]:
        raise ValueError("AI response type mismatch")
    if "enum" in schema and value not in schema["enum"]:
        raise ValueError("AI enum mismatch")
    if kind == "object":
        if set(value) != set(schema["required"]):
            raise ValueError("AI properties must match the schema exactly")
        for key, item in value.items():
            validate_schema(item, schema["properties"][key])
    elif kind == "array":
        if not schema.get("minItems", 0) <= len(value) <= schema.get("maxItems", 20):
            raise ValueError("AI evidence count invalid")
        for item in value:
            validate_schema(item, schema["items"])
    else:
        if not schema.get("minLength", 0) <= len(value) <= schema.get("maxLength", 4096):
            raise ValueError("AI text size invalid")
        if "pattern" in schema and not re.fullmatch(schema["pattern"], value):
            raise ValueError("AI evidence name invalid")
        if schema.get("format") == "date-time":
            instant(value)


def validate_release(release: dict, now: datetime) -> None:
    required = {"service", "environment", "commit", "digest", "current_digest", "manifest_sha256",
                "run_id", "execution_id", "observed_at", "checks", "enforcement_verified"}
    if type(release) is not dict or set(release) != required:
        raise ValueError("Incomplete or unexpected release evidence")
    if release["service"] not in CRITICALITY or release["environment"] != "dev":
        raise ValueError("Unsupported application/environment")
    for key, pattern in {"commit": r"[a-f0-9]{40}", "digest": r"sha256:[a-f0-9]{64}",
                         "current_digest": r"sha256:[a-f0-9]{64}",
                         "manifest_sha256": r"[a-f0-9]{64}", "run_id": r"[0-9]{1,20}",
                         "execution_id": r"[A-Za-z0-9_-]{1,100}"}.items():
        if not isinstance(release[key], str) or not re.fullmatch(pattern, release[key]):
            raise ValueError("Invalid immutable release reference")
    checks = release["checks"]
    if type(checks) is not dict or set(checks) != CHECKS or any(type(v) is not bool for v in checks.values()):
        raise ValueError("Required checks must have explicit boolean results")
    if type(release["enforcement_verified"]) is not bool or not fresh(release["observed_at"], now):
        raise ValueError("Stale evidence or unknown enforcement")


def release_id(release: dict) -> str:
    return hashlib.sha256(canonical(release)).hexdigest()


def ai_input(release: dict) -> dict:
    """Allowlist: no diff text, payloads, URLs, credentials or personal identity."""
    return {"service": release["service"], "environment": release["environment"],
            "criticality": CRITICALITY[release["service"]], "commit": release["commit"],
            "digest": release["digest"], "manifest_sha256": release["manifest_sha256"],
            "observed_at": release["observed_at"],
            "checks": release["checks"]}


def evaluate(release: dict, ai: dict | None, *, now: datetime, ai_enabled: bool = False) -> tuple[str, str]:
    """Return APPROVE, HUMAN_REVIEW, REJECT or BLOCK; only this policy authorizes."""
    try:
        validate_release(release, now)
    except (ValueError, TypeError, KeyError):
        return "BLOCK", "incomplete_invalid_or_stale_evidence"
    if release["enforcement_verified"] is not True:
        return "BLOCK", "mandatory_gate_not_enforced"
    checks = release["checks"]
    if not checks["tests_passed"] or not checks["scan_passed"] or not checks["digest_verified"]:
        return "REJECT", "technical_security_or_digest_failure"
    if not checks["sqs_compatible"] or not checks["incidents_clear"]:
        return "REJECT", "contract_or_critical_incident"
    if not checks["argo_healthy"] or not checks["observability_reliable"]:
        return "BLOCK", "runtime_evidence_unreliable"
    if type(ai_enabled) is not bool:
        return "BLOCK", "invalid_trusted_ai_configuration"
    if not ai_enabled:
        if (release["service"] == "orders-api" or checks["sensitive_changes"]
                or not checks["dependencies_reviewed"]):
            return "HUMAN_REVIEW", "human_review_required_by_policy"
        return "APPROVE", "worker_deterministic_all_conditions_met_ai_disabled"
    try:
        validate_schema(ai, AI_SCHEMA)
        if (ai["service"] != release["service"] or ai["environment"] != release["environment"]
                or ai["criticality"] != CRITICALITY[release["service"]]
                or not fresh(ai["timestamp"], now) or not set(ai["evidence"]) <= CHECKS):
            raise ValueError("AI evidence or identity mismatch")
    except (ValueError, TypeError, KeyError):
        return "BLOCK", "ai_unavailable_invalid_or_stale"
    if ai["recommendation"] == "REJECT":
        return "REJECT", "ai_recommends_rejection"
    if (release["service"] == "orders-api" or checks["sensitive_changes"]
            or not checks["dependencies_reviewed"] or ai["recommendation"] != "APPROVE"
            or ai["risk_level"] != "LOW"):
        return "HUMAN_REVIEW", "human_review_required_by_policy"
    return "APPROVE", "worker_low_risk_all_conditions_met"


class PromotionGate:
    """Trusted service boundary; signing key never belongs to app CI or the model."""

    def __init__(self, database: str, signing_key: Ed25519PrivateKey, approvers: set[str], *, ai_enabled: bool = False):
        self.database, self.key, self.approvers = database, signing_key, frozenset(approvers)
        self.ai_enabled = ai_enabled
        with sqlite3.connect(database) as db:
            db.execute("CREATE TABLE IF NOT EXISTS decisions (id TEXT PRIMARY KEY, receipt TEXT NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS dispatches (id TEXT PRIMARY KEY, state TEXT NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS gate_events (release_id TEXT, timestamp TEXT, decision TEXT, reason TEXT)")

    def audit(self, release: dict, now: datetime, decision: str, reason: str) -> None:
        """Record decisions without payloads or credentials, including denied attempts."""
        identity = release_id(release)
        with sqlite3.connect(self.database) as db:
            db.execute("INSERT INTO gate_events VALUES (?,?,?,?)",
                       (identity, now.isoformat(), decision, reason))

    def decide(self, release: dict, ai: dict | None, *, now: datetime,
               human: dict | None = None, kestra_available: bool = True) -> dict:
        if not kestra_available:
            self.audit(release, now, "BLOCK", "kestra_unavailable")
            raise PermissionError("Kestra unavailable; no promotion")
        decision, reason = evaluate(release, ai, now=now, ai_enabled=self.ai_enabled)
        self.audit(release, now, decision, reason)
        principal = "policy-engine"
        if decision == "HUMAN_REVIEW":
            # Human record must come from a server-authenticated identity adapter.
            # A request body with authenticated=true is never accepted by a web adapter.
            if human is None:
                raise PermissionError("Authenticated human review required")
            if (set(human) != {"principal", "release_id", "result", "timestamp", "execution_id"}
                    or human["principal"] not in self.approvers
                    or human["release_id"] != release_id(release)
                    or human["execution_id"] != release["execution_id"]
                    or not fresh(human["timestamp"], now)):
                raise PermissionError("Human identity/release binding invalid")
            decision = "APPROVE" if human["result"] == "APPROVE" else "REJECT"
            reason, principal = "human_decision", human["principal"]
            self.audit(release, now, decision, reason)
        if decision != "APPROVE":
            raise PermissionError(reason)
        payload = {"release_id": release_id(release), "release": release, "result": decision,
                   "approver": principal, "issued_at": now.isoformat(),
                   "expires_at": (now + timedelta(minutes=10)).isoformat(),
                   "policy_version": POLICY_VERSION, "reason": reason,
                   "ai_mode": "ENABLED" if self.ai_enabled else "DISABLED",
                   "ai_sha256": hashlib.sha256(canonical(ai)).hexdigest()}
        receipt = {"payload": payload,
                   "signature": base64.b64encode(self.key.sign(canonical(payload))).decode()}
        with sqlite3.connect(self.database) as db:
            existing = db.execute("SELECT receipt FROM decisions WHERE id=?", (payload["release_id"],)).fetchone()
            if existing:
                return json.loads(existing[0])
            db.execute("INSERT INTO decisions VALUES (?,?)", (payload["release_id"], json.dumps(receipt)))
        return receipt

    def promote(self, receipt: dict, release: dict, publish: Callable[[dict], None], *, now: datetime) -> None:
        """Consume once before dispatch. Ambiguous/failing dispatch requires reconciliation."""
        verify_receipt(receipt, self.key.public_key(), release, now=now)
        identity = release_id(release)
        with sqlite3.connect(self.database) as db:
            stored = db.execute("SELECT receipt FROM decisions WHERE id=?", (identity,)).fetchone()
            if stored is None or json.loads(stored[0]) != receipt:
                raise PermissionError("Decision was not issued by this gate")
            try:
                db.execute("INSERT INTO dispatches VALUES (?, 'dispatching')", (identity,))
            except sqlite3.IntegrityError as error:
                raise PermissionError("Already dispatched; do not replay") from error
        try:
            publish(release)
        except Exception:
            with sqlite3.connect(self.database) as db:
                db.execute("UPDATE dispatches SET state='reconcile_required' WHERE id=?", (identity,))
            raise
        with sqlite3.connect(self.database) as db:
            db.execute("UPDATE dispatches SET state='success' WHERE id=?", (identity,))


def verify_receipt(receipt: dict, key: Ed25519PublicKey, release: dict, *, now: datetime) -> None:
    """Verifier needs only public key, never a secret capable of granting approval."""
    try:
        payload = receipt["payload"]
        key.verify(base64.b64decode(receipt["signature"], validate=True), canonical(payload))
        validate_release(release, now)
        if (payload["release_id"] != release_id(release) or payload["release"] != release
                or payload["result"] != "APPROVE" or payload["policy_version"] != POLICY_VERSION
                or not instant(payload["issued_at"]) <= now < instant(payload["expires_at"])
                or instant(payload["expires_at"]) - instant(payload["issued_at"]) > timedelta(minutes=10)):
            raise ValueError("Invalid receipt binding or expiry")
    except (ValueError, KeyError, TypeError, InvalidSignature) as error:
        raise PermissionError("Invalid promotion receipt") from error
