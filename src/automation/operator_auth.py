"""Local operational identities alongside the unchanged Kestra administrator."""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import stat
import threading
import time
from pathlib import Path

ITERATIONS = 600_000


def hash_password(password: str) -> str:
    """PBKDF2 SHA256 with a fresh 256-bit salt; never log passwords."""
    if not 12 <= len(password) <= 256:
        raise ValueError("A senha deve ter entre12 e256 caracteres")
    salt = secrets.token_hex(32)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode(), bytes.fromhex(salt), ITERATIONS
    ).hex()
    return f"pbkdf2_sha256${ITERATIONS}${salt}${digest}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, rounds, salt, wanted = encoded.split("$")
        if (
            algorithm != "pbkdf2_sha256"
            or int(rounds) != ITERATIONS
            or len(salt) != 64
            or len(wanted) != 64
            or len(password) > 256
        ):
            return False
        digest = hashlib.pbkdf2_hmac(
            "sha256", password.encode(), bytes.fromhex(salt), ITERATIONS
        ).hex()
        return hmac.compare_digest(digest, wanted)
    except (ValueError, TypeError):
        return False


class OperatorAuthenticator:
    """Immutable root-owned user registry; bounded retries; no administration API."""

    def __init__(self, path: str | None, fallback):
        self.users = {}
        self.fallback = fallback
        self.failures = {}
        self.lock = threading.Lock()
        if path:
            p = Path(path)
            info = p.lstat()
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_uid != 0
                or info.st_mode & 0o027
            ):
                raise PermissionError("Registro operacional não protegido")
            users = json.loads(p.read_text())["users"]
            if any(
                set(u) != {"password_hash", "role", "enabled"}
                or u["role"] != "operator"
                for u in users.values()
            ):
                raise PermissionError("Papel operacional inválido")
            self.users = users

    def __call__(self, username: str, password: str) -> bool:
        if username not in self.users:
            return self.fallback(username, password)
        now = time.monotonic()
        with self.lock:
            attempts = [t for t in self.failures.get(username, []) if now - t < 60]
            self.failures[username] = attempts
            if len(attempts) >= 5:
                return False
        user = self.users[username]
        ok = user["enabled"] is True and verify_password(
            password, user["password_hash"]
        )
        if not ok:
            with self.lock:
                self.failures[username].append(now)
        return ok
