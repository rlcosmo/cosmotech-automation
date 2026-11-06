"""Cross-repository contract: CI requests cannot contain a direct GitOps publisher."""
import importlib.util
import json
from pathlib import Path
from unittest.mock import Mock

import pytest
import yaml

ROOT = Path('/home/lab/cosmotech-repositories')


@pytest.mark.parametrize('service,filename', [('orders-api','orders-ci.yml'), ('orders-worker','worker-ci.yml')])
def test_ci_requests_only(service, filename, monkeypatch):
    repository = ROOT / ('cosmotech-' + service)
    workflow = yaml.safe_load((repository / '.github/workflows' / filename).read_text())
    jobs = workflow['jobs']
    assert 'propose-gitops' not in jobs
    assert 'request-promotion' in jobs
    raw = (repository / '.github/workflows' / filename).read_text()
    assert 'actions/create-github-app-token' not in raw
    assert 'gh pr merge' not in raw
    spec = importlib.util.spec_from_file_location('request_promotion', repository / 'scripts/request_promotion.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setenv('GITHUB_SHA', 'a' * 40)
    monkeypatch.setenv('GITHUB_RUN_ID', '123')
    monkeypatch.setenv('GITHUB_RUN_ATTEMPT', '1')
    image = f'123456789012.dkr.ecr.us-east-1.amazonaws.com/aws-eks-istio-lab-dev-{service}@sha256:' + 'b' * 64
    monkeypatch.setattr('sys.argv', ['request', '--service', service, '--image', image])
    run = Mock(return_value=Mock(returncode=0, stdout='test-command'))
    monkeypatch.setattr(module.subprocess, 'run', run)
    module.main()
    command = run.call_args.args[0]
    assert command[:3] == ['aws', 'ssm', 'send-command']
    parameters = json.loads(command[command.index('--parameters') + 1])
    assert set(parameters) == {'Commit', 'Digest', 'RunId', 'RunAttempt'}
    assert 'approved' not in json.dumps(parameters).lower()
    run.reset_mock()
    monkeypatch.setattr('sys.argv', ['request', '--service', service, '--image', image.split('@')[0] + ':latest'])
    with pytest.raises(SystemExit):
        module.main()
    run.assert_not_called()
