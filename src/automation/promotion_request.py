"""Idempotent SSM-to-Kestra request ingress; CI never receives a GitOps write key."""

from __future__ import annotations

import json
import re
import sqlite3
import uuid

from automation.promotion_gate import canonical


def enqueue(database: str, kestra, *, service: str, commit: str, digest: str,
            run_id: str, run_attempt: str = "1") -> dict:
    """Validate identifiers only; the trusted Kestra collector independently verifies evidence."""
    if service not in {"orders-api", "orders-worker"}:
        raise ValueError("Unknown application")
    for value, pattern in [(commit, r"[a-f0-9]{40}"), (digest, r"sha256:[a-f0-9]{64}"),
                           (run_id, r"[0-9]{1,20}"), (run_attempt, r"[0-9]{1,4}")]:
        if not isinstance(value, str) or not re.fullmatch(pattern, value):
            raise ValueError("Invalid immutable promotion reference")
    inputs = {"service": service, "environment": "dev", "commit": commit, "digest": digest,
              "run_id": run_id, "run_attempt": run_attempt}
    identity = canonical(inputs).decode()
    with sqlite3.connect(database) as db:
        db.execute("CREATE TABLE IF NOT EXISTS promotion_requests (id TEXT PRIMARY KEY, state TEXT, execution TEXT)")
        row = db.execute("SELECT state,execution FROM promotion_requests WHERE id=?", (identity,)).fetchone()
        if row:
            if row[0] != "accepted":
                raise PermissionError("Ambiguous Kestra request; reconcile the execution before retry")
            return json.loads(row[1])
        db.execute("INSERT INTO promotion_requests VALUES (?,'dispatching',NULL)", (identity,))
    boundary = "cosmotech-" + uuid.uuid4().hex
    body = "".join(f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n{value}\r\n'
                   for key, value in inputs.items()) + f"--{boundary}--\r\n"
    execution = kestra.request("/api/v1/main/executions/cosmotech.promotion/promotion_gate",
                              method="POST", body=body.encode(),
                              content_type="multipart/form-data; boundary=" + boundary)
    identifier = execution.get("id")
    if not isinstance(identifier, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", identifier):
        raise ValueError("Kestra did not return a valid execution reference")
    result = {"state": "requested", "kestra_execution_id": identifier, "promotion_validated": False}
    with sqlite3.connect(database) as db:
        db.execute("UPDATE promotion_requests SET state='accepted',execution=? WHERE id=?",
                   (json.dumps(result), identity))
    return result
