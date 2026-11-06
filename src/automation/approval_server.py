"""Run the decision-only portal behind the existing HTTPS reverse proxy."""
from __future__ import annotations

import json
from pathlib import Path
from socketserver import ThreadingMixIn
from wsgiref.simple_server import WSGIServer, make_server

from automation.approval_portal import ApprovalPortal
from automation.native_viewer import NativeViewer
from automation.operator_auth import OperatorAuthenticator
from automation.promotion_runtime import KestraClient


class Server(ThreadingMixIn, WSGIServer):
    daemon_threads = True


def main() -> None:
    config = json.loads(Path('/etc/cosmotech/promotion/portal.json').read_text())
    authenticator=OperatorAuthenticator(config.get('operator_users'),lambda user,password: KestraClient.authenticate_user(config['kestra_origin'],user,password))
    viewer=NativeViewer(str(Path(config['database']).with_name('native-view-sessions.db')),authenticator,set(config['allowed_principals']),config['public_origin'])
    portal = ApprovalPortal(config['database'], Path(config['csrf_key']).read_bytes(),
                            set(config['allowed_principals']),
                            authenticator,
                            config['public_origin'],ci_directory=config.get('ci_directory'))
    def application(environ, start_response):
        if environ.get('PATH_INFO') == '/healthz':
            start_response('200 OK', [('Content-Type', 'application/json'), ('Cache-Control', 'no-store')])
            return [b'{"service":"approval-portal","promotion_authority":false}']
        if environ.get('PATH_INFO') in {'/api/v1/login','/api/v1/logout','/_kestra/auth'}:
            return viewer(environ,start_response)
        return portal(environ, start_response)
    with make_server('127.0.0.1', 8095, application, server_class=Server) as server:
        server.serve_forever()


if __name__ == '__main__':
    main()
