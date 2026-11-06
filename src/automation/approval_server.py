"""Run the decision-only portal behind the existing HTTPS reverse proxy."""
from __future__ import annotations

import json
from pathlib import Path
from socketserver import ThreadingMixIn
from wsgiref.simple_server import WSGIServer, make_server

from automation.approval_portal import ApprovalPortal
from automation.promotion_runtime import KestraClient


class Server(ThreadingMixIn, WSGIServer):
    daemon_threads = True


def main() -> None:
    config = json.loads(Path('/etc/cosmotech/promotion/portal.json').read_text())
    portal = ApprovalPortal(config['database'], Path(config['csrf_key']).read_bytes(),
                            set(config['allowed_principals']),
                            lambda user, password: KestraClient.authenticate_user(config['kestra_origin'], user, password),
                            config['public_origin'])
    def application(environ, start_response):
        if environ.get('PATH_INFO') == '/healthz':
            start_response('200 OK', [('Content-Type', 'application/json'), ('Cache-Control', 'no-store')])
            return [b'{"service":"approval-portal","promotion_authority":false}']
        return portal(environ, start_response)
    with make_server('127.0.0.1', 8095, application, server_class=Server) as server:
        server.serve_forever()


if __name__ == '__main__':
    main()
