"""Native Kestra UI read access via opaque sessions, never browser admin credentials.

Nginx applies this read policy before its private, server-side authentication
mapping. Native administrator requests without this cookie bypass the adapter.
"""
from __future__ import annotations

import hashlib
import json
import re
import secrets
import sqlite3
import time
import urllib.error
import urllib.request
from http.cookies import CookieError, SimpleCookie

COOKIE = 'COSMOTECH_KV'
LIFETIME = 8 * 60 * 60
READ_RESOURCES = {
    'flows', 'executions', 'logs', 'metrics', 'namespaces', 'dashboards',
    'stats', 'triggers', 'templates', 'plugins', 'usages', 'kv', 'blueprints',
    'tasks', 'tags', 'search',
}


def read_allowed(method: str, path: str, origin: str, trusted_origin: str) -> bool:
    """Reviewed OSS2.0.8 reads, including dashboard queries; all control writes denied."""
    if (len(path) > 8192 or not path.startswith('/api/v1/') or
            any(x in path for x in ['%', '//', '..', '\\', '?', '#']) or
            any(ord(c) < 32 for c in path)):
        return False
    parts = path.split('/')[3:]
    if 'webhook' in parts or 'actions' in parts:
        return False  # GET webhooks can execute flows; expressions may invoke functions.
    if method == 'POST':
        return origin == trusted_origin and path == '/api/v1/main/dashboards/charts/preview'
    if method not in {'GET', 'HEAD'}:
        return False
    if parts[0] in {'configs', 'plugins', 'blueprints', 'basicAuthValidationErrors'}:
        return True
    return len(parts) >= 2 and parts[0] == 'main' and parts[1] in READ_RESOURCES


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def native_auth_request(path: str, body: bytes, environ: dict) -> tuple:
    """Pass native admin login/logout unchanged to the fixed loopback backend."""
    if path not in {'/api/v1/login', '/api/v1/logout'}:
        raise PermissionError('Unexpected native authentication endpoint')
    headers = {'Content-Type': 'application/json', 'X-Forwarded-Proto': 'https',
               'Host': 'kestra.cosmotechsolutions.com.br'}
    for source, target in [('HTTP_COOKIE', 'Cookie'), ('HTTP_ORIGIN', 'Origin'),
                           ('HTTP_X_CSRF_TOKEN', 'X-CSRF-TOKEN'),
                           ('HTTP_AUTHORIZATION', 'Authorization')]:
        if environ.get(source):
            headers[target] = environ[source]
    request = urllib.request.Request('http://127.0.0.1:8081' + path,
                                     data=body, headers=headers, method='POST')
    try:
        response = urllib.request.build_opener(NoRedirect).open(request, timeout=10)
    except urllib.error.HTTPError as error:
        response = error
    with response:
        safe_headers = [(k, v) for k, v in response.headers.items()
                        if k.lower() in {'content-type', 'set-cookie', 'www-authenticate'}]
        return response.code, safe_headers, response.read(65536)


class NativeViewer:
    def __init__(self, database: str, authenticator, allowed: set[str], origin: str,
                 *, forward=native_auth_request, clock=time.time, audit=None):
        self.database, self.authenticator, self.allowed = database, authenticator, allowed
        self.origin, self.forward, self.clock = origin, forward, clock
        self.audit = audit or (lambda value: print(json.dumps(value), flush=True))
        with sqlite3.connect(database) as db:
            db.execute('CREATE TABLE IF NOT EXISTS native_view_sessions '
                       '(token_hash TEXT PRIMARY KEY, principal TEXT NOT NULL, expires REAL NOT NULL)')

    @staticmethod
    def cookie(environ: dict) -> str | None:
        try:
            parsed = SimpleCookie(environ.get('HTTP_COOKIE', ''))
            return parsed[COOKIE].value if COOKIE in parsed else None
        except CookieError:
            return '' if COOKIE in environ.get('HTTP_COOKIE', '') else None

    def session(self, token: str | None) -> str | None:
        if not token or not re.fullmatch(r'[A-Za-z0-9_-]{43}', token):
            return None
        with sqlite3.connect(self.database) as db:
            row = db.execute('SELECT principal,expires FROM native_view_sessions WHERE token_hash=?',
                             (hashlib.sha256(token.encode()).hexdigest(),)).fetchone()
        if (not row or row[1] <= self.clock() or row[0] not in self.allowed or
                not self.authenticator.users.get(row[0], {}).get('enabled')):
            return None
        return row[0]

    def revoke(self, token: str | None) -> None:
        if token:
            with sqlite3.connect(self.database) as db:
                db.execute('DELETE FROM native_view_sessions WHERE token_hash=?',
                           (hashlib.sha256(token.encode()).hexdigest(),))

    @staticmethod
    def cookie_header(name: str, value: str, *, clear=False, http_only=True) -> tuple:
        result = f'{name}={value}; Path=/; Secure; SameSite=Strict; Max-Age={0 if clear else LIFETIME}'
        if http_only:
            result += '; HttpOnly'
        return ('Set-Cookie', result)

    def __call__(self, environ, start_response):
        path, method = environ.get('PATH_INFO', ''), environ.get('REQUEST_METHOD', '')

        def reply(code, body=b'', headers=()):
            from http import HTTPStatus
            start_response(f'{code} {HTTPStatus(code).phrase}',
                           [('Content-Type', 'application/json'), ('Cache-Control', 'no-store'),
                            ('X-Content-Type-Options', 'nosniff'), *headers])
            return [body]

        token = self.cookie(environ)
        if path == '/_kestra/auth':
            if environ.get('REMOTE_ADDR') not in {'127.0.0.1', '::1'}:
                return reply(404)
            principal = self.session(token)
            if not principal:
                return reply(401)
            original_method = environ.get('HTTP_X_ORIGINAL_METHOD', '')
            original_path = environ.get('HTTP_X_ORIGINAL_PATH', '')
            permitted = read_allowed(original_method, original_path,
                                     environ.get('HTTP_X_ORIGINAL_ORIGIN', ''), self.origin)
            self.audit({'event': 'kestra_native_view', 'principal': principal,
                        'method': original_method, 'path': original_path[:256],
                        'status': 204 if permitted else 403, 'at': int(self.clock())})
            if not permitted:
                return reply(403)
            # Preserve non-authentication cookies (including the native CSRF cookie).
            cookies = SimpleCookie(environ.get('HTTP_COOKIE', ''))
            clean_cookie = '; '.join(f'{k}={v.coded_value}' for k, v in cookies.items()
                                    if k not in {COOKIE, 'BASIC_AUTH'})
            return reply(204, headers=[('X-CosmoTech-Role', 'viewer'),
                                       ('X-CosmoTech-Principal', principal),
                                       ('X-CosmoTech-Cookie', clean_cookie)])
        if path not in {'/api/v1/login', '/api/v1/logout'}:
            return reply(404)
        if method != 'POST':
            return reply(405)
        try:
            length = int(environ.get('CONTENT_LENGTH') or 0)
            if not 0 <= length <= 8192:
                return reply(413)
            body = environ['wsgi.input'].read(length)
            credentials = json.loads(body) if path.endswith('/login') else {}
            if not isinstance(credentials, dict):
                return reply(400)
        except (ValueError, UnicodeError):
            return reply(400)
        if path.endswith('/logout') and token is not None:
            if environ.get('HTTP_ORIGIN') != self.origin:
                return reply(403)
            self.revoke(token)
            return reply(204, headers=[self.cookie_header(COOKIE, '', clear=True),
                                      self.cookie_header('BASIC_AUTH', '', clear=True),
                                      self.cookie_header('kestraBasicAuthenticated', '', clear=True, http_only=False)])
        username = credentials.get('username')
        if path.endswith('/login') and isinstance(username, str) and username in self.authenticator.users:
            if environ.get('HTTP_ORIGIN') != self.origin:
                return reply(403)
            password = credentials.get('password')
            if (username not in self.allowed or not isinstance(password, str) or
                    not self.authenticator(username, password)):
                return reply(401)
            self.revoke(token)
            new_token = secrets.token_urlsafe(32)
            with sqlite3.connect(self.database) as db:
                db.execute('INSERT INTO native_view_sessions VALUES (?,?,?)',
                           (hashlib.sha256(new_token.encode()).hexdigest(), username, self.clock() + LIFETIME))
            self.audit({'event': 'kestra_native_login', 'principal': username, 'at': int(self.clock())})
            return reply(204, headers=[self.cookie_header(COOKIE, new_token),
                                      self.cookie_header('BASIC_AUTH', '', clear=True),
                                      self.cookie_header('kestraBasicAuthenticated', 'true', http_only=False)])
        try:
            status, headers, response = self.forward(path, body, environ)
        except OSError:
            return reply(502)
        if path.endswith('/login') and 200 <= status < 300:
            self.revoke(token)
            headers = [*headers, self.cookie_header(COOKIE, '', clear=True)]
        return reply(status, response, headers)
