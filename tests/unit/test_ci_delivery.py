"""Real CD events must finish the matching CI observer, never invent CI facts."""
import json

import pytest

from automation import ci_correlation as ci
from automation.delivery_runtime import DeliveryRuntime


@pytest.fixture
def record(tmp_path, monkeypatch):
    monkeypatch.setattr(ci, 'ROOT', tmp_path)
    path = tmp_path / 'orders-api-123-2.json'
    value = {'app': 'orders-api', 'run_id': '123', 'attempt': '2', 'sha': 'a'*40,
             'state': 'running', 'release_execution_id': 'release-1',
             'stages': {s: {'state': 'success'} for s in ['tests', 'build_scan', 'publish']}}
    path.write_text(json.dumps(value))
    state = {'release': {'service': 'orders-api', 'run_id': '123', 'commit': 'a'*40,
                         'execution_id': 'release-1'}, 'bundle': {'ci_attempt': '2'}}
    return path, state


def test_publication_sync_and_validation_complete_matching_observer(record):
    path, state = record
    for key in ['gitops', 'argo', 'validation']:
        assert ci.delivery_event(state, key, 'RUNNING')
        assert ci.delivery_event(state, key, 'SUCCESS')
    value = json.loads(path.read_text())
    assert value['state'] == 'success'
    assert not ci.delivery_event(state, 'validation', 'FAILED')
    assert json.loads(path.read_text()) == value


def test_canary_preparation_does_not_claim_final_health(record):
    path, state = record
    ci.delivery_event(state, 'gitops', 'SUCCESS')
    ci.delivery_event(state, 'argo', 'SUCCESS')
    assert not ci.delivery_event(state, 'canary', 'SUCCESS')
    assert json.loads(path.read_text())['state'] == 'running'
    ci.delivery_event(state, 'canary', 'FAILED')
    assert json.loads(path.read_text())['stages']['health']['state'] == 'failure'
    assert not ci.delivery_event(state, 'validation', 'SUCCESS')


@pytest.mark.parametrize('field,value', [('commit', 'b'*40), ('execution_id', 'different')])
def test_other_release_cannot_complete_attempt(record, field, value):
    path, state = record
    before = path.read_bytes()
    state['release'][field] = value
    with pytest.raises(PermissionError):
        ci.delivery_event(state, 'gitops', 'SUCCESS')
    assert path.read_bytes() == before


def test_missing_attempt_is_not_created(record):
    path, state = record
    state['bundle']['ci_attempt'] = '3'
    assert not ci.delivery_event(state, 'gitops', 'SUCCESS')
    assert not path.with_name('orders-api-123-3.json').exists()


def test_missing_ci_success_blocks_delivery_projection(record):
    path, state = record
    value = json.loads(path.read_text())
    value['stages']['publish']['state'] = 'running'
    path.write_text(json.dumps(value))
    with pytest.raises(PermissionError):
        ci.delivery_event(state, 'gitops', 'SUCCESS')


def test_runtime_calls_projection_after_saving_real_event(record):
    path, state = record
    class Runtime:
        def path(self, execution):
            return path.with_name('release.json')
    delivery = DeliveryRuntime.__new__(DeliveryRuntime)
    delivery.rt, delivery.state, delivery.release = Runtime(), state, state['release']
    delivery.record_stage('gitops', 'SUCCESS', 'Signed publication acknowledged')
    assert json.loads(path.read_text())['stages']['gitops']['state'] == 'success'
    assert json.loads(path.with_name('release.json').read_text())['operational_stages']['gitops']['status'] == 'SUCCESS'


def test_gate_failure_reaches_observer_without_waiting_for_timeout(record):
    path, state = record
    request = {**state['release'], 'run_attempt': '2'}
    ci.update(request, execution_id='release-1', error='Required evidence unavailable', stage='collect')
    value = json.loads(path.read_text())
    assert value['state'] == 'failure'
    assert value['stages']['gitops']['state'] == 'failure'
    assert not ci.delivery_event(state, 'gitops', 'SUCCESS')


def test_duplicate_publication_does_not_regress_proven_stage(record):
    path, state = record
    ci.delivery_event(state, 'gitops', 'SUCCESS')
    before = path.read_bytes()
    assert not ci.delivery_event(state, 'gitops', 'RUNNING')
    assert path.read_bytes() == before
