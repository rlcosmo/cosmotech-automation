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
    bundle['resources']['hpa.json']['spec']={}

    before=copy.deepcopy(bundle['resources'])
    before['deployment.json']['spec']['template']['spec']['containers'][0]['image']=before['deployment.json']['spec']['template']['spec']['containers'][0]['image'].split('@')[0]+'@'+release['current_digest']
    rt=SimpleNamespace(gate=SimpleNamespace(database=':memory:'),kube=Mock(),config={'cluster':'lab'},changes=Mock(),key=Mock())
    rt.changes.read.return_value={'rcl_id':'RCL000001'}
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
