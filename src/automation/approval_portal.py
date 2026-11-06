"""Authenticated human decision recorder for the existing Kestra HTTPS host.

Records decisions only. It has no signing key, publisher, model client or deploy
function. Kestra must consume the bound decision through the deterministic gate.
"""

from __future__ import annotations

import json
import re
import sqlite3
import urllib.parse
from collections.abc import Callable
from datetime import UTC, datetime

from automation.portal_view import confirmation, csp, render
from automation.promotion_gate import instant, release_id
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
            db.execute("CREATE TABLE IF NOT EXISTS approval_context (id TEXT PRIMARY KEY, context TEXT)")
            db.execute("CREATE TABLE IF NOT EXISTS portal_rollback_requests (id TEXT PRIMARY KEY, request TEXT)")

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

    def set_context(self, identity: str, context: dict) -> None:
        """Trusted gate writes only public state; this does not authorize rollback."""
        with sqlite3.connect(self.database) as db:
            db.execute("INSERT OR REPLACE INTO approval_context VALUES (?,?)", (identity, json.dumps(context)))

    def rollback_request(self, identity: str) -> dict | None:
        with sqlite3.connect(self.database) as db:
            row = db.execute("SELECT request FROM portal_rollback_requests WHERE id=?", (identity,)).fetchone()
        return json.loads(row[0]) if row else None

    def __call__(self, environ, start_response):
        def reply(code, body, authenticate=False):
            headers = [("Content-Type", "text/html; charset=utf-8"), ("Cache-Control", "no-store"),
                       ("X-Content-Type-Options", "nosniff"), ("X-Frame-Options", "DENY"),
                       ("Content-Security-Policy", csp())]
            if authenticate:
                headers.append(("WWW-Authenticate", 'Basic realm="CosmoTech approval", charset="UTF-8"'))
            start_response(code, headers)
            return [body.encode()]
        match = re.fullmatch(r"/releases/([a-f0-9]{64})(/rollback)?", environ.get("PATH_INFO", ""))
        if not match:
            return reply("404 Not Found", "Página não encontrada")
        identity = match[1]
        rollback = match[2] is not None
        action = "rollback" if rollback else "approve"
        with sqlite3.connect(self.database) as db:
            row = db.execute("SELECT release,advice FROM approval_requests WHERE id=?", (identity,)).fetchone()
        if not row:
            return reply("404 Not Found", "Página não encontrada")
        release, _advice = map(json.loads, row)
        with sqlite3.connect(self.database) as db:
            context_row = db.execute("SELECT context FROM approval_context WHERE id=?", (identity,)).fetchone()
        context = json.loads(context_row[0]) if context_row else {}
        now = datetime.now(UTC)
        authorization = environ.get("HTTP_AUTHORIZATION", "")
        method = environ.get("REQUEST_METHOD")
        if method not in {"GET", "POST"}:
            return reply("405 Method Not Allowed", "Método não permitido")
        if method == "GET":
            # Authenticate even reads; no public release metadata or token-only approvals.
            csrf = challenge(release, self.key, now, action=action)
            try:
                authenticated_human(release, authorization=authorization, csrf=csrf,
                    key=self.key, result="REJECT", now=now, allowed_principals=self.allowed,
                    authenticate=self.authenticate, action=action)
            except (PermissionError, OSError):
                return reply("401 Unauthorized", "Autenticação necessária", True)
            if rollback and (context.get('rollback_eligible') is not True or context.get('phase')!='OBSERVING' or not context.get('rollback_until')
                    or now>=instant(context['rollback_until']) or self.rollback_request(identity)):
                return reply('409 Conflict','Janela de rollback indisponível. Consulte o estado ou solicite nova GMUD.')
            recorded=self.decision(identity)
            if context.get('phase')=='HUMAN_REVIEW' and recorded is not None:
                context={**context,'phase':'DECISION_RECORDED','events':list(context.get('events') or [])+[{'type':'decision_recorded','at':recorded['timestamp'],'details':{'principal':recorded['principal']}}]}
            body=render(release,context,csrf,now=now,rollback=rollback,
                        rollback_pending=self.rollback_request(identity) is not None)
            return reply("200 OK", body)
        if environ.get("HTTP_ORIGIN") != self.origin:
            return reply("403 Forbidden", "Origem da solicitação inválida")
        if not rollback and (context.get('phase') != 'HUMAN_REVIEW' or context.get('decision')=='APPROVE'):
            return reply('409 Conflict','Esta GMUD não possui aprovação humana pendente.')
        try:
            length = int(environ.get("CONTENT_LENGTH") or 0)
            if not 0 < length <= 8192:
                raise ValueError("Invalid body size")
            fields = urllib.parse.parse_qs(environ["wsgi.input"].read(length).decode(), strict_parsing=True)
            if set(fields) != ({"csrf", "result", "reason"} if rollback else {"csrf", "result"}) or any(len(v) != 1 for v in fields.values()):
                raise ValueError("Invalid decision form")
            record = authenticated_human(release, authorization=authorization, csrf=fields["csrf"][0],
                key=self.key, result=fields["result"][0], now=now,
                allowed_principals=self.allowed, authenticate=self.authenticate, action=action)
            if rollback:
                if (fields['result'][0] != 'APPROVE' or not 8 <= len(fields['reason'][0].strip()) <= 2000
                        or context.get('rollback_eligible') is not True or context.get('phase') != 'OBSERVING' or not context.get('rollback_until')
                        or now >= instant(context['rollback_until'])):
                    raise PermissionError('Rollback window or justification invalid')
                record['reason'] = fields['reason'][0].strip()
                with sqlite3.connect(self.database) as db:
                    try:
                        db.execute('INSERT INTO portal_rollback_requests VALUES (?,?)', (identity, json.dumps(record)))
                    except sqlite3.IntegrityError:
                        return reply('409 Conflict', 'Já existe uma solicitação de rollback. Consulte seu estado.')
                return reply('200 OK', confirmation('Rollback solicitado. O Kestra verificará autorização, prazo e versão atual.',identity))
            with sqlite3.connect(self.database) as db:
                existing = db.execute("SELECT decision FROM human_approvals WHERE id=?", (identity,)).fetchone()
                if existing and json.loads(existing[0])["result"] != record["result"]:
                    return reply("409 Conflict", "Uma decisão já foi registrada")
                db.execute("INSERT OR IGNORE INTO human_approvals VALUES (?,?)", (identity, json.dumps(record)))
        except (ValueError, KeyError, PermissionError, OSError):
            return reply("403 Forbidden", "Decisão não aceita")
        return reply("200 OK", confirmation("Decisão registrada. O Kestra validará a release antes de promover.",identity))
