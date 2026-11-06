import hashlib
import json
from unittest.mock import Mock

import pytest
from test_promotion_gate import NOW
from test_signed_git_publisher import bundle_for

from automation.trusted_evidence import collect


def setup_sources(release):
    bundle = bundle_for(release)
    request = {key: release[key] for key in ('service', 'environment', 'commit', 'digest', 'run_id', 'execution_id')}
    request['run_attempt'] = '1'
    source = b'reviewed workflow'
    git = Mock()
    def api(path):
        if path.startswith('compare/'):
            return {'files': []}
        if '/jobs?' in path:
            return {'total_count': 2, 'jobs': [
                {'name': 'test-build-scan', 'conclusion': 'success', 'steps': [
                    {'name': 'Run python -m pytest tests/unit', 'conclusion': 'success'},
                    {'name': 'Run aquasecurity/trivy-action', 'conclusion': 'success'}]},
                {'name': 'publish', 'conclusion': 'success'}]}
        return {'head_sha': release['commit'], 'head_branch': 'main', 'event': 'push',
                'run_attempt': 1, 'path': '.github/workflows/worker-ci.yml', 'conclusion': None}
    git.api.side_effect = api
    git.file.side_effect = lambda name, revision: source if name.startswith('.github/') else json.dumps(bundle['resources'][name.split('/')[-1]]).encode()
    ecr = Mock()
    ecr.describe_images.side_effect = [
        {'imageDetails': [{'imageDigest': release['digest']}]},
        {'imageDetails': [{'imageTags': ['sha-' + 'c' * 40]}]}]
    live = {'image': 'registry@' + release['current_digest'], 'resources': bundle['resources'],
            'signature_key': 'A' * 16, 'argo_healthy': True, 'incidents_clear': True,
            'observability_reliable': True}
    return request, {'github': git, 'ecr': ecr, 'runtime': Mock(return_value=live),
                     'workflow_hash': hashlib.sha256(source).hexdigest(),
                     'fingerprint': 'A' * 40, 'account': '000000000000'}, live


def test_check_claims_do_not_override_verified_sources(release):
    request, sources, live = setup_sources(release)
    request['checks'] = {'tests_passed': True}
    result, _ = collect(request, now=NOW, **sources)
    assert result['checks']['tests_passed'] is True
    live['signature_key'] = 'B' * 16
    sources['ecr'].describe_images.side_effect = [
        {'imageDetails': [{'imageDigest': release['digest']}]},
        {'imageDetails': [{'imageTags': ['sha-' + 'c' * 40]}]}]
    result, _ = collect(request, now=NOW, **sources)
    assert result['enforcement_verified'] is False


@pytest.mark.parametrize('attack', ['workflow', 'jobs', 'digest', 'source'])
def test_mismatched_or_incomplete_evidence_blocks(release, attack):
    request, sources, _ = setup_sources(release)
    if attack == 'workflow':
        sources['workflow_hash'] = '0' * 64
    elif attack == 'jobs':
        original = sources['github'].api.side_effect
        sources['github'].api.side_effect = lambda path: {'total_count': 0, 'jobs': []} if '/jobs?' in path else original(path)
    elif attack == 'digest':
        sources['ecr'].describe_images.side_effect = [{'imageDetails': [{'imageDigest': 'sha256:' + '0' * 64}]}]
    else:
        request['commit'] = 'not-a-commit'
    with pytest.raises((PermissionError, ValueError)):
        collect(request, now=NOW, **sources)
