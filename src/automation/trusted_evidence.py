"""Read deployment evidence from authenticated sources, never caller check claims."""
from __future__ import annotations

import ast
import copy
import hashlib
import json
import re
from datetime import datetime

from automation.promotion_evidence import classify_manifests
from automation.promotion_gate import canonical

FILES = ('deployment.json', 'configmap.json', 'service.json', 'hpa.json', 'ingress.json')


def collect(request: dict, *, now: datetime, github, ecr, runtime, workflow_hash: str,
            fingerprint: str, account: str) -> tuple[dict, dict]:
    """Sources are injected trusted adapters. Every missing/ambiguous source raises."""
    service = request['service']
    if service not in {'orders-api', 'orders-worker'} or request['environment'] != 'dev':
        raise ValueError('Unsupported deployment boundary')
    if not re.fullmatch('[a-f0-9]{40}', request['commit']) or not re.fullmatch('sha256:[a-f0-9]{64}', request['digest']):
        raise ValueError('Immutable source and image required')
    workflow = 'worker-ci.yml' if service == 'orders-worker' else 'orders-ci.yml'
    run = github.api(f"actions/runs/{request['run_id']}")
    if (run.get('head_sha') != request['commit'] or run.get('head_branch') != 'main'
            or run.get('event') not in {'push', 'workflow_dispatch'}
            or str(run.get('run_attempt')) != request['run_attempt']
            or run.get('path') != '.github/workflows/' + workflow
            or run.get('conclusion') not in {None, 'success'}):
        raise PermissionError('Actions execution does not match the release')
    source = github.file('.github/workflows/' + workflow, request['commit'])
    if hashlib.sha256(source).hexdigest() != workflow_hash:
        raise PermissionError('Workflow is outside the reviewed policy')
    jobs = github.api(f"actions/runs/{request['run_id']}/attempts/{request['run_attempt']}/jobs?per_page=100")
    by_name = {job['name']: job for job in jobs['jobs']}
    if jobs.get('total_count', 0) > 100:
        raise PermissionError('Ambiguous Actions job list')
    required = [by_name.get(name, {}) for name in ('test-build-scan', 'publish')]
    if any(job.get('conclusion') != 'success' for job in required):
        raise PermissionError('Tests, scan and publication did not succeed')
    steps = required[0].get('steps', [])
    if (not any('pytest' in step.get('name', '') and step.get('conclusion') == 'success' for step in steps)
            or not any('trivy' in step.get('name', '').lower() and step.get('conclusion') == 'success' for step in steps)):
        raise PermissionError('Mandatory tests or Trivy evidence missing')
    repository = 'aws-eks-istio-lab-dev-' + service
    details = ecr.describe_images(repositoryName=repository,
                                  imageIds=[{'imageTag': 'sha-' + request['commit']}])['imageDetails']
    if len(details) != 1 or details[0]['imageDigest'] != request['digest']:
        raise PermissionError('ECR digest does not match the tested source')
    live = runtime(service)
    current_digest = live['image'].split('@')[-1]
    if not re.fullmatch('sha256:[a-f0-9]{64}', current_digest):
        raise PermissionError('Stable image is not immutable')
    stable_details = ecr.describe_images(repositoryName=repository,
                                        imageIds=[{'imageDigest': current_digest}])['imageDetails']
    tags = [tag[4:] for item in stable_details for tag in item.get('imageTags', [])
            if re.fullmatch('sha-[a-f0-9]{40}', tag)]
    if len(set(tags)) != 1:
        raise PermissionError('Stable source commit cannot be proven')
    baseline = tags[0]
    comparison = github.api(f"compare/{baseline}...{request['commit']}")
    changed = comparison.get('files')
    if not isinstance(changed, list) or len(changed) >= 300:
        raise PermissionError('Source diff is absent or truncated')
    names = {item['filename'] for item in changed}
    dependencies = not any(name.startswith('requirements') or name.endswith(('poetry.lock', 'uv.lock')) for name in names)
    sensitive_source = any(re.search(r'(^|/)(terraform|iam|secrets?|network|\.github)(/|$)', name) for name in names)
    contract = not any(name.startswith(('src/orders/shared/validation', 'src/orders/consumer/')) for name in names)
    if service == 'orders-worker' and 'src/orders/runtime/worker.py' in names:
        def business_function(revision):
            tree = ast.parse(github.file('src/orders/runtime/worker.py', revision).decode())
            return ast.dump(next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == 'process_message'))
        contract = contract and business_function(baseline) == business_function(request['commit'])
    before = live['resources']
    after = {name: json.loads(github.file('deploy/dev/' + name, request['commit'])) for name in FILES}
    image = f'{account}.dkr.ecr.us-east-1.amazonaws.com/{repository}@' + request['digest']
    deployment = after['deployment.json']['spec']['template']['spec']
    container = next(c for c in deployment['containers'] if c['name'] == service)
    container['image'] = image
    for env in container.get('env', []):
        if env['name'] == 'RELEASE_VERSION':
            env['value'] = request['digest']
    diff = classify_manifests(list(before.values()), list(after.values()))
    release = {key: request[key] for key in ('service', 'environment', 'commit', 'digest', 'run_id', 'execution_id')}
    release.update(current_digest=current_digest, observed_at=now.isoformat(),
                   manifest_sha256=hashlib.sha256(canonical(after)).hexdigest(),
                   enforcement_verified=live['signature_key'] == fingerprint[-16:],
                   checks={'tests_passed': True, 'scan_passed': True, 'digest_verified': True,
                           'sqs_compatible': contract, 'argo_healthy': live['argo_healthy'],
                           'incidents_clear': live['incidents_clear'],
                           'observability_reliable': live['observability_reliable'],
                           'dependencies_reviewed': dependencies,
                           'sensitive_changes': sensitive_source or diff['sensitive_changes']})
    return release, {'resources': copy.deepcopy(after)}
