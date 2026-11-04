"""Local, idempotent GitHub-stage evidence and public-console Kestra execution bridge."""
from __future__ import annotations

import argparse
import base64
import fcntl
import json
import re
import urllib.error
import urllib.request
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path('/opt/cosmotech/pipeline-events')
NAMESPACE = 'cosmotech.clusters.aws_eks_istio_lab_dev'
NAMESPACES = {'orders-api': NAMESPACE, 'orders-worker': NAMESPACE, 'gitops': 'cosmotech.clusters.argocd_management'}
APPS = {'orders-api': 'cosmotech-orders-api', 'orders-worker': 'cosmotech-orders-worker', 'gitops': 'cosmotech-gitops'}
STAGES = {'orders-api': ['tests', 'build_scan', 'publish', 'gitops', 'rollout', 'health'],
          'orders-worker': ['tests', 'build_scan', 'publish', 'gitops', 'rollout', 'health'],
          'gitops': ['validate', 'rollout', 'health']}


def validate(app: str, run: str, attempt: str, sha: str, stage: str, state: str) -> None:
    if app not in APPS or not re.fullmatch(r'[0-9]{1,20}', run) or not re.fullmatch(r'[0-9]{1,4}', attempt):
        raise ValueError('Invalid application or execution')
    if not re.fullmatch(r'[a-f0-9]{40}', sha) or stage not in STAGES[app]:
        raise ValueError('Invalid commit or stage')
    if state not in {'running', 'success', 'failure', 'cancelled', 'skipped'}:
        raise ValueError('Invalid stage state')


def evolve(record: dict, stage: str, state: str, now: str) -> dict:
    """Terminal evidence cannot be overwritten by late or duplicate callbacks."""
    current = record.setdefault('stages', {}).get(stage, {})
    if record.get('state') in {'success', 'failure', 'cancelled'} or current.get('state') in {'success', 'failure', 'cancelled', 'skipped'}:
        return record
    record['stages'][stage] = {'state': state, 'started_at': current.get('started_at', now), 'updated_at': now}
    if state in {'failure', 'cancelled'}:
        record['state'] = state
    elif all(record['stages'].get(s, {}).get('state') == 'success' for s in STAGES[record['app']]):
        record['state'] = 'success'
    else:
        record['state'] = 'running'
    return record


def request(path: str, method: str = 'GET', data: bytes | None = None, content_type: str = 'application/json') -> dict:
    env = dict(line.split('=', 1) for line in Path('/opt/cosmotech/secrets/itsm/runtime.env').read_text().splitlines() if '=' in line and not line.startswith('#'))
    auth = base64.b64encode((env['KESTRA_ADMIN_USERNAME'] + ':' + env['KESTRA_ADMIN_PASSWORD']).encode()).decode()
    req = urllib.request.Request('http://127.0.0.1:8081/api/v1/main' + path, data=data, method=method,
                                 headers={'Authorization': 'Basic ' + auth, 'Content-Type': content_type})
    with urllib.request.urlopen(req, timeout=15) as response:
        return json.load(response)


def save(path: Path, value: dict) -> None:
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, indent=2) + '\n')
    temporary.chmod(0o644)
    temporary.replace(path)


def record(app: str, run: str, attempt: str, sha: str, stage: str, state: str) -> dict:
    validate(app, run, attempt, sha, stage, state)
    ROOT.mkdir(parents=True, exist_ok=True)
    with (ROOT / '.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        index = ROOT / f'{app}-{sha}.index'
        if run == '0':
            if not index.exists():
                return {'untracked_source': True, 'app': app}
            run, attempt = index.read_text().split()
        path = ROOT / f'{app}-{run}-{attempt}.json'
        now = datetime.now(UTC).isoformat()
        if path.exists():
            value = json.loads(path.read_text())
            if value['sha'] != sha:
                raise ValueError('Commit mismatch for existing pipeline execution')
        else:
            value = {'app': app, 'run_id': run, 'attempt': attempt, 'sha': sha,
                     'cluster': 'argocd-management-k3s' if app == 'gitops' else 'aws-eks-istio-lab-dev', 'state': 'running', 'stages': {},
                     'url': f'https://github.com/rlcosmo/{APPS[app]}/actions/runs/{run}/attempts/{attempt}'}
        value = evolve(value, stage, state, now)
        if not value.get('execution_id'):
            if value.get('dispatch_intent'):
                raise RuntimeError('Ambiguous prior Kestra dispatch; reconcile labeled execution before retrying')
            value['dispatch_intent'] = now
            save(path, value)
            boundary = 'cosmotech-pipeline-inputs'
            fields = {'run_id': run, 'run_attempt': attempt, 'commit_sha': sha}
            body = ''.join(f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n' for k, v in fields.items()) + f'--{boundary}--\r\n'
            execution = request('/executions/' + NAMESPACES[app] + '/' + app.replace('-', '_') + '_ci_cd', method='POST', data=body.encode(), content_type='multipart/form-data; boundary=' + boundary)
            value['execution_id'] = execution['id']
        save(path, value)
        index.write_text(run + ' ' + attempt)
        return {'app': app, 'run_id': run, 'stage': stage, 'state': state, 'execution_id': value['execution_id']}


def serve() -> None:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            match = re.fullmatch(r'/(orders-api|orders-worker|gitops)/([0-9]{1,20})/([0-9]{1,4})', self.path)
            value = {'state': 'pending'}
            if match:
                path = ROOT / ('-'.join(match.groups()) + '.json')
                if path.exists():
                    value = json.loads(path.read_text())
            else:
                self.send_error(404)
                return
            body = json.dumps(value).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:
            pass
    ThreadingHTTPServer(('127.0.0.1', 8094), Handler).serve_forever()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('mode', choices=['serve', 'record', 'install'])
    parser.add_argument('--app')
    parser.add_argument('--run')
    parser.add_argument('--attempt', default='1')
    parser.add_argument('--sha')
    parser.add_argument('--stage')
    parser.add_argument('--state')
    args = parser.parse_args()
    if args.mode == 'serve':
        serve()
    elif args.mode == 'install':
        for path in sorted(Path('/opt/cosmotech/pipelines/flows').glob('*.yaml')):
            source = path.read_bytes()
            # Generated flow IDs and namespaces are fixed source metadata.
            name = path.stem
            namespace = NAMESPACES[name.removesuffix('_ci_cd').replace('_', '-')]
            try:
                request('/flows/' + namespace + '/' + name, method='PUT', data=source, content_type='application/x-yaml')
            except urllib.error.HTTPError as error:
                if error.code != 404:
                    raise
                request('/flows', method='POST', data=source, content_type='application/x-yaml')
            print('Installed ' + namespace + '/' + name)
    else:
        print(json.dumps(record(args.app, args.run, args.attempt, args.sha, args.stage, args.state)))


if __name__ == '__main__':
    main()
