import copy
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from test_signed_git_publisher import bundle_for

from automation.canary_delivery import WorkerCanaryDelivery


def fixture_delivery(release,monkeypatch):
    monkeypatch.setattr('automation.canary_delivery.client',lambda service:Mock())
    bundle=bundle_for(release)
    dep=bundle['resources']['deployment.json']
    dep['metadata']['labels']={'app':'orders-worker'}
    dep['spec']['selector']={'matchLabels':{'app':'orders-worker'}}
    dep['spec']['template']['metadata']={'labels':{'app':'orders-worker'}}
    bundle['resources']['hpa.json']['spec']={'minReplicas':1,'maxReplicas':4}

    before=copy.deepcopy(bundle['resources'])
    before['deployment.json']['spec']['template']['spec']['containers'][0]['image']=before['deployment.json']['spec']['template']['spec']['containers'][0]['image'].split('@')[0]+'@'+release['current_digest']
    rt=SimpleNamespace(gate=SimpleNamespace(database=':memory:'),kube=Mock(),config={'cluster':'lab'},changes=Mock(),key=Mock())
    rt.changes.read.return_value={'rcl_id':'RCL000001','change_id':1}
    delivery=Mock(rt=rt,release=release,identity='signed-change',state={'bundle':bundle,'before_bundle':{'resources':before}})
    delivery.controlled.return_value.publish.return_value='a'*40
    return WorkerCanaryDelivery(delivery),bundle,before


def test_canary_preparation_preserves_stable_version_and_starts_closed(release,monkeypatch):
    binding,bundle,before=fixture_delivery(release,monkeypatch)
    binding.queue=Mock(return_value={'dlq':0,'visible':0,'inflight':0})
    binding.controller=Mock();binding.executor=Mock()
    binding.start(bundle,{'receipt':'test'})
    published=binding.delivery.controlled.return_value.publish.call_args.args[2]
    assert published['deployment.json']==before['deployment.json']
    candidate=published['canary-deployment.json']
    assert candidate['spec']['replicas']==0
    env={e['name']:e['value'] for e in candidate['spec']['template']['spec']['containers'][0]['env']}
    assert env['RELEASE_CHANNEL']=='canary' and env['WORKER_CONTROL_FILE'].endswith('lease.json')
    assert published['hpa.json']['spec']['maxReplicas']==1
    binding.controller.start.assert_called_once()
    binding.executor.return_value.apply.assert_called_once()


def test_nonempty_or_dirty_baseline_blocks_before_publisher(release,monkeypatch):
    binding,bundle,_=fixture_delivery(release,monkeypatch)
    binding.queue=Mock(return_value={'dlq':1,'visible':0,'inflight':0})
    with pytest.raises(PermissionError):binding.start(bundle,{})
    binding.delivery.controlled.assert_not_called()


def test_transient_observation_error_keeps_audit_and_clears_active_error(release,monkeypatch,tmp_path):
    import json
    import sqlite3
    from datetime import UTC, datetime

    binding,_,_=fixture_delivery(release,monkeypatch)
    database=str(tmp_path/'gate.db');binding.rt.gate.database=database
    state={'stage':'DRAIN_PROMOTED','entered_at':datetime.now(UTC).isoformat(),'reason':'passed'}
    with sqlite3.connect(database) as db:
        db.execute('CREATE TABLE rollouts(id TEXT,state TEXT)')
        db.execute('INSERT INTO rollouts VALUES (?,?)',(binding.identity,json.dumps(state)))
    binding.delivery.state['delivery']={}
    binding.controller=Mock();binding.controller.step.return_value=state
    binding.executor=Mock()
    binding.observation=Mock(side_effect=PermissionError('missing fresh pod sample'))
    binding.tick()
    assert binding.delivery.state['delivery']['observation_error']=='missing fresh pod sample'
    assert binding.controller.step.call_args.args[1]=={}
    binding.observation=Mock(return_value={'verified_fresh_sample':True})
    binding.tick()
    assert 'observation_error' not in binding.delivery.state['delivery']
    assert binding.delivery.state['delivery']['observation_errors'][0]['stage']=='DRAIN_PROMOTED'


def test_restarted_candidate_cannot_pass_on_reset_counters(release,monkeypatch):
    binding,bundle,_=fixture_delivery(release,monkeypatch)
    binding.rt.kube.deployment.return_value=bundle['resources']['deployment.json']
    binding.rt.kube.request.return_value={'items':[{'metadata':{'name':'canary-pod'},'status':{'phase':'Running','containerStatuses':[{'restartCount':1}]}}]}
    from datetime import UTC, datetime
    with pytest.raises(PermissionError,match='reiniciou'):
        binding.observation({'stage':'CANARY_2','entered_at':datetime.now(UTC).isoformat()})


@pytest.mark.parametrize('stage',['COMPLETE','ROLLED_BACK'])
def test_terminal_rollout_reconciles_effect_without_querying_removed_pods(release,monkeypatch,tmp_path,stage):
    import json
    import sqlite3
    from datetime import UTC, datetime
    binding,_,_=fixture_delivery(release,monkeypatch)
    binding.rt.gate.database=str(tmp_path/'terminal.db')
    state={'stage':stage,'entered_at':datetime.now(UTC).isoformat(),'reason':'validated_drain'}
    with sqlite3.connect(binding.rt.gate.database) as db:
        db.execute('CREATE TABLE rollouts(id TEXT,state TEXT)')
        db.execute('INSERT INTO rollouts VALUES (?,?)',(binding.identity,json.dumps(state)))
    binding.rt.changes.read.return_value={'rcl_id':'RCL000001','change_id':1,'phase':'ROLLED_BACK'}
    binding.delivery.state['delivery']={'capacity_restored':True}
    binding.controller=Mock();binding.executor=Mock();binding.observation=Mock(side_effect=AssertionError('removed pods must not be queried'))
    binding.tick()
    binding.observation.assert_not_called();binding.controller.step.assert_not_called()
    if stage=='COMPLETE':binding.delivery.observing.assert_called_once()


def test_two_replica_baseline_is_not_reduced_during_canary_preparation(release,monkeypatch):
    binding,bundle,before=fixture_delivery(release,monkeypatch)
    before['hpa.json']['spec'].update(minReplicas=2,maxReplicas=2)
    binding.queue=Mock(return_value={'dlq':0,'visible':0,'inflight':0})
    binding.controller=Mock();binding.executor=Mock()
    binding.start(bundle,{'receipt':'test'})
    published=binding.delivery.controlled.return_value.publish.call_args.args[2]
    assert published['hpa.json']['spec']['minReplicas']==2
    assert published['hpa.json']['spec']['maxReplicas']==2
    assert published['deployment.json']==before['deployment.json']


def test_promoted_candidate_uses_approved_two_replica_capacity(release,monkeypatch):
    binding,bundle,before=fixture_delivery(release,monkeypatch)
    bundle['resources']['hpa.json']['spec'].update(minReplicas=2,maxReplicas=2)
    binding.rt.kube.deployment.return_value=before['deployment.json']
    binding.delivery.state['delivery']={}
    binding.promote(binding.identity,release['digest'],'promotion')
    published=binding.delivery.controlled.return_value.publish.call_args.args[2]
    assert published['hpa.json']['spec']['minReplicas']==2
    assert published['hpa.json']['spec']['maxReplicas']==2
    assert published['deployment.json']['spec']['template']['spec']['containers'][0]['image'].endswith('@'+release['digest'])


def test_invalid_capacity_is_rejected_before_any_canary_publication(release,monkeypatch):
    binding,bundle,before=fixture_delivery(release,monkeypatch)
    before['hpa.json']['spec'].update(minReplicas=0,maxReplicas=2)
    binding.queue=Mock(return_value={'dlq':0,'visible':0,'inflight':0})
    with pytest.raises(PermissionError):binding.start(bundle,{})
    binding.delivery.controlled.assert_not_called()
