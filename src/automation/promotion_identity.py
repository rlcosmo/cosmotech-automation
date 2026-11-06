"""Human approval boundary: authenticated identity plus a release-bound CSRF challenge."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from collections.abc import Callable
from datetime import datetime, timedelta

from automation.promotion_gate import canonical, instant, release_id


def challenge(release: dict, key: bytes, now: datetime, *, action: str = "approve") -> str:
    """Token is not a bearer approval: valid Basic identity is also mandatory."""
    if len(key) < 32 or action not in {"approve", "rollback"}:
        raise ValueError("Dedicated server-side CSRF key required")
    payload = {"release_id": release_id(release), "action": action,
               "expires_at": (now + timedelta(minutes=10)).isoformat()}
    signature = hmac.new(key, canonical(payload), hashlib.sha256).hexdigest()
    return base64.urlsafe_b64encode(canonical({"payload": payload, "signature": signature})).decode()


def authenticated_human(release: dict, *, authorization: str, csrf: str, key: bytes,
                        result: str, now: datetime, allowed_principals: set[str],
                        authenticate: Callable[[str, str], bool], action: str = "approve") -> dict:
    """authenticate must validate credentials against the existing Kestra over TLS.

    A web handler must pass the HTTP Authorization header; never accept a principal
    or authenticated=true field from the form body. Do not log headers/passwords.
    """
    try:
        if result not in {"APPROVE", "REJECT"} or len(key) < 32 or len(csrf) > 4096:
            raise ValueError("Invalid decision/challenge")
        if not authorization.startswith("Basic ") or len(authorization) > 4096:
            raise ValueError("Basic authentication required")
        username, password = base64.b64decode(authorization[6:], validate=True).decode().split(":", 1)
        if username not in allowed_principals or not password:
            raise ValueError("Unauthorized human")
        envelope = json.loads(base64.urlsafe_b64decode(csrf))
        payload = envelope["payload"]
        expected = hmac.new(key, canonical(payload), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(envelope["signature"], expected):
            raise ValueError("Invalid challenge signature")
        remaining = instant(payload["expires_at"]) - now
        if (payload["release_id"] != release_id(release) or payload.get("action") != action
                or not timedelta(0) < remaining <= timedelta(minutes=10)):
            raise ValueError("Expired or wrong-release challenge")
        if authenticate(username, password) is not True:
            raise ValueError("Kestra authentication failed")
        return {"principal": username, "release_id": release_id(release), "result": result,
                "timestamp": now.isoformat(), "execution_id": release["execution_id"]}
    except (ValueError, TypeError, KeyError, UnicodeError) as error:
        raise PermissionError("Human authentication/approval failed") from error
