"""Authenticated human decision recorder for the existing Kestra HTTPS host.

Records decisions only. It has no signing key, publisher, model client or deploy
function. Kestra must consume the bound decision through the deterministic gate.
"""

from __future__ import annotations

import html
import json
import re
import sqlite3
import urllib.parse
from collections.abc import Callable
from datetime import UTC, datetime

from automation.promotion_gate import CRITICALITY, release_id
from automation.promotion_identity import authenticated_human, challenge


class ApprovalPortal:
    """WSGI app; bind loopback only behind the existing TLS proxy."""

    def __init__(self, database: str, csrf_key: bytes, allowed_principals: set[str],
                 authenticate: Callable[[str, str], bool], public_origin: str):
        self.database, self.key, self.allowed = database, csrf_key, allowed_principals
        self.authenticate = authenticate
        origin = urllib.parse.urlsplit(public_origin)
        if origin.scheme != "https" or not origin.netloc or origin.username:
            raise ValueError("Existing trusted HTTPS origin required")
        self.origin = origin.scheme + "://" + origin.netloc
        with sqlite3.connect(database) as db:
            db.execute("CREATE TABLE IF NOT EXISTS approval_requests (id TEXT PRIMARY KEY, release TEXT, advice TEXT)")
            db.execute("CREATE TABLE IF NOT EXISTS human_approvals (id TEXT PRIMARY KEY, decision TEXT)")

    def register(self, release: dict, advice: dict | None) -> str:
        """Trusted Kestra service path only; this is not exposed as a web endpoint."""
        identity = release_id(release)
        with sqlite3.connect(self.database) as db:
            db.execute("INSERT OR IGNORE INTO approval_requests VALUES (?,?,?)",
                       (identity, json.dumps(release), json.dumps(advice)))
        return identity

    def decision(self, identity: str) -> dict | None:
        """Called by Kestra's trusted runner, not by unauthenticated web clients."""
        with sqlite3.connect(self.database) as db:
            row = db.execute("SELECT decision FROM human_approvals WHERE id=?", (identity,)).fetchone()
        return json.loads(row[0]) if row else None

    def __call__(self, environ, start_response):
        def reply(code, body, authenticate=False):
            headers = [("Content-Type", "text/html; charset=utf-8"), ("Cache-Control", "no-store"),
                       ("X-Content-Type-Options", "nosniff"), ("X-Frame-Options", "DENY"),
                       ("Content-Security-Policy", "default-src 'none'; form-action 'self'; frame-ancestors 'none'")]
            if authenticate:
                headers.append(("WWW-Authenticate", 'Basic realm="CosmoTech approval", charset="UTF-8"'))
            start_response(code, headers)
            return [body.encode()]
        match = re.fullmatch(r"/releases/([a-f0-9]{64})", environ.get("PATH_INFO", ""))
        if not match:
            return reply("404 Not Found", "Not found")
        identity = match[1]
        with sqlite3.connect(self.database) as db:
            row = db.execute("SELECT release,advice FROM approval_requests WHERE id=?", (identity,)).fetchone()
        if not row:
            return reply("404 Not Found", "Not found")
        release, advice = map(json.loads, row)
        now = datetime.now(UTC)
        authorization = environ.get("HTTP_AUTHORIZATION", "")
        method = environ.get("REQUEST_METHOD")
        if method not in {"GET", "POST"}:
            return reply("405 Method Not Allowed", "Method not allowed")
        if method == "GET":
            # Authenticate even reads; no public release metadata or token-only approvals.
            csrf = challenge(release, self.key, now)
            try:
                authenticated_human(release, authorization=authorization, csrf=csrf,
                    key=self.key, result="REJECT", now=now, allowed_principals=self.allowed,
                    authenticate=self.authenticate)
            except (PermissionError, OSError):
                return reply("401 Unauthorized", "Authentication required", True)
            esc = lambda value: html.escape(str(value), quote=True)
            assessment = ("Bedrock disabled; deterministic policy and human review apply."
                          if advice is None else
                          "AI advisory: " + advice["recommendation"] + " — " + advice["justification"])
            body = (f"<h1>Review {esc(release['service'])}</h1>"
                    f"<p>{esc(CRITICALITY[release['service']])} / {esc(release['environment'])}</p>"
                    f"<p>Commit: {esc(release['commit'])}</p><p>Current: {esc(release['current_digest'])}</p>"
                    f"<p>Candidate: {esc(release['digest'])}</p>"
                    f"<p>{esc(assessment)}</p>"
                    f'<form method="post"><input type="hidden" name="csrf" value="{esc(csrf)}">'
                    '<button name="result" value="APPROVE">Approve this release</button>'
                    '<button name="result" value="REJECT">Reject</button></form>')
            return reply("200 OK", body)
        if environ.get("HTTP_ORIGIN") != self.origin:
            return reply("403 Forbidden", "Invalid request origin")
        try:
            length = int(environ.get("CONTENT_LENGTH") or 0)
            if not 0 < length <= 8192:
                raise ValueError("Invalid body size")
            fields = urllib.parse.parse_qs(environ["wsgi.input"].read(length).decode(), strict_parsing=True)
            if set(fields) != {"csrf", "result"} or any(len(v) != 1 for v in fields.values()):
                raise ValueError("Invalid decision form")
            record = authenticated_human(release, authorization=authorization, csrf=fields["csrf"][0],
                key=self.key, result=fields["result"][0], now=now,
                allowed_principals=self.allowed, authenticate=self.authenticate)
            with sqlite3.connect(self.database) as db:
                existing = db.execute("SELECT decision FROM human_approvals WHERE id=?", (identity,)).fetchone()
                if existing and json.loads(existing[0])["result"] != record["result"]:
                    return reply("409 Conflict", "A decision is already recorded")
                db.execute("INSERT OR IGNORE INTO human_approvals VALUES (?,?)", (identity, json.dumps(record)))
        except (ValueError, KeyError, PermissionError, OSError):
            return reply("403 Forbidden", "Decision rejected")
        return reply("200 OK", "Decision recorded. Kestra still validates the release before promotion.")
