"""Repository-scoped, short-lived GitHub App tokens; no token in Git URLs or logs."""
from __future__ import annotations

import base64
import json
import time
import urllib.error
import urllib.parse
import urllib.request

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding


class GitHubApp:
    def __init__(self, credentials: dict, repository: str, *, write: bool = False):
        if repository not in {'cosmotech-orders-api', 'cosmotech-orders-worker', 'cosmotech-gitops'}:
            raise ValueError('Repository outside the delivery boundary')
        self.credentials, self.repository, self.write = credentials, repository, write
        self.token, self.expires = None, 0

    @staticmethod
    def request(url, *, headers, body=None, method='GET', raw=False):
        request = urllib.request.Request(url, data=json.dumps(body).encode() if body is not None else None,
                                         method=method, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=25) as response:
                data = response.read(4_000_000)
                return data if raw else json.loads(data)
        except urllib.error.HTTPError as error:
            raise PermissionError(f'GitHub evidence/publisher request rejected: HTTP {error.code}') from None

    def headers(self):
        if self.token is None or time.monotonic() >= self.expires:
            def encode(value):
                return base64.urlsafe_b64encode(json.dumps(value, separators=(',', ':')).encode()).rstrip(b'=')
            now = int(time.time())
            unsigned = encode({'alg': 'RS256', 'typ': 'JWT'}) + b'.' + encode({
                'iat': now - 30, 'exp': now + 300, 'iss': str(self.credentials['app_id'])})
            key = serialization.load_pem_private_key(self.credentials['private_key_pem'].encode(), password=None)
            jwt = (unsigned + b'.' + base64.urlsafe_b64encode(key.sign(
                unsigned, padding.PKCS1v15(), hashes.SHA256())).rstrip(b'=')).decode()
            headers = {'Authorization': 'Bearer ' + jwt, 'Accept': 'application/vnd.github+json',
                       'Content-Type': 'application/json'}
            installation = self.request('https://api.github.com/repos/rlcosmo/' + self.repository + '/installation', headers=headers)
            permissions = {'contents': 'write' if self.write else 'read'}
            if self.repository != 'cosmotech-gitops':
                permissions['actions'] = 'read'
            result = self.request(f"https://api.github.com/app/installations/{installation['id']}/access_tokens",
                headers=headers, body={'repositories': [self.repository], 'permissions': permissions}, method='POST')
            self.token, self.expires = result['token'], time.monotonic() + 3000
        return {'Authorization': 'Bearer ' + self.token, 'Accept': 'application/vnd.github+json',
                'Content-Type': 'application/json', 'X-GitHub-Api-Version': '2022-11-28'}

    def api(self, path):
        parsed = urllib.parse.urlsplit(path)
        decoded = urllib.parse.unquote(parsed.path)
        if (parsed.scheme or parsed.netloc or parsed.fragment or path.startswith('/')
                or '%' in decoded or '\\' in decoded
                or any(part in {'.', '..'} for part in decoded.split('/'))
                or any(ord(char) < 32 or ord(char) == 127 for char in path)):
            raise ValueError('Invalid relative GitHub API path')
        return self.request('https://api.github.com/repos/rlcosmo/' + self.repository + '/' + path, headers=self.headers())

    def file(self, path, revision):
        data = self.api('contents/' + urllib.parse.quote(path, safe='/') + '?ref=' + urllib.parse.quote(revision, safe=''))
        if data.get('encoding') != 'base64' or data.get('size', 0) > 1_000_000:
            raise PermissionError('Missing or oversized source file')
        return base64.b64decode(data['content'])

    def git_environment(self, base: dict) -> dict:
        self.headers()
        encoded = base64.b64encode(('x-access-token:' + self.token).encode()).decode()
        return dict(base, GIT_TERMINAL_PROMPT='0', GIT_CONFIG_COUNT='1',
                    GIT_CONFIG_KEY_0='http.https://github.com/.extraheader',
                    GIT_CONFIG_VALUE_0='AUTHORIZATION: basic ' + encoded)
