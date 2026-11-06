import copy
import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from test_controlled_publisher import publisher_fixture

from automation.history_retention import reconcile
from automation.image_references import publication_resources


def test_fixed_publication_standard_does_not_mutate_tested_bundle():
    before={'deployment.json':{'kind':'Deployment','spec':{'revisionHistoryLimit':10,'replicas':2,'template':{'spec':{'containers':[{'name':'orders-worker','image':'123456789012.dkr.ecr.us-east-1.amazonaws.com/aws-eks-istio-lab-dev-orders-worker@sha256:'+'a'*64}]}}}}}
    snapshot=copy.deepcopy(before)
    after=publication_resources(before)
    assert before==snapshot
    assert after['deployment.json']['spec']['revisionHistoryLimit']==2
    assert after['deployment.json']['spec']['template']==before['deployment.json']['spec']['template']
    assert after['deployment.json']['spec']['replicas']==2


def current_resource(publisher):
    return json.loads((publisher.git.repo/'gitops/apps/orders-worker/dev/deployment.json').read_text())


def test_retention_maintenance_preserves_live_template_and_is_idempotent(tmp_path,journal,release,monkeypatch):
    publisher,_,identity,git=publisher_fixture(tmp_path,journal,release,monkeypatch)
    before=current_resource(publisher);before['spec']['revisionHistoryLimit']=10
    (git.repo/'gitops/apps/orders-worker/dev/deployment.json').write_text(json.dumps(before))
    proposed=copy.deepcopy(before);proposed['spec']['revisionHistoryLimit']=2
    publisher.publish(identity,'history-limit:2',{'deployment.json':proposed},expected_digest=release['current_digest'])
    after=current_resource(publisher)
    assert after['spec']['revisionHistoryLimit']==2
    after['spec']['revisionHistoryLimit']=10
    assert after==before
    publisher.publish(identity,'history-limit:2',{'deployment.json':proposed},expected_digest=release['current_digest'])
    assert sum(c.args[:1]==('push',) for c in git.git.call_args_list)==1
    assert (git.repo/'gitops/policies/history-retention/orders-worker-dev.json').is_file()
    assert not (git.repo/'gitops/releases/orders-worker-dev.json').exists()


@pytest.mark.parametrize('tamper',['replicas','image','env','annotation','history'])
def test_history_operation_cannot_change_any_other_field(tmp_path,journal,release,monkeypatch,tamper):
    publisher,_,identity,git=publisher_fixture(tmp_path,journal,release,monkeypatch)
    before=current_resource(publisher);proposed=copy.deepcopy(before);proposed['spec']['revisionHistoryLimit']=2
    container=proposed['spec']['template']['spec']['containers'][0]
    if tamper=='replicas':proposed['spec']['replicas']=3
    elif tamper=='image':container['image']=container['image'].split('@')[0]+'@'+release['digest']
    elif tamper=='env':container['env']=[{'name':'CHANGED','value':'true'}]
    elif tamper=='annotation':proposed['metadata']['annotations']={'new':'value'}
    else:proposed['spec']['revisionHistoryLimit']=3
    with pytest.raises(PermissionError):publisher.publish(identity,'history-limit:2',{'deployment.json':proposed},expected_digest=release['current_digest'])
    assert not any(c.args[:1]==('push',) for c in git.git.call_args_list)


def test_retention_rejects_unrelated_resource_paths(tmp_path,journal,release,monkeypatch):
    publisher,bundle,identity,git=publisher_fixture(tmp_path,journal,release,monkeypatch)
    with pytest.raises(PermissionError):publisher.publish(identity,'history-limit:2',bundle['resources'],expected_digest=release['current_digest'])
    assert not any(c.args[:1]==('push',) for c in git.git.call_args_list)


def test_protected_setting_cannot_increase_retention_and_no_record_means_no_publish():
    runtime=SimpleNamespace(config={'revision_history_limit':3},changes=Mock(),publisher=Mock())
    with pytest.raises(PermissionError):reconcile(runtime)
    runtime.changes.history.assert_not_called();runtime.publisher.assert_not_called()
    runtime.config={}
    assert reconcile(runtime)==[]
