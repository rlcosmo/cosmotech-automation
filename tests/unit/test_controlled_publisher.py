import copy
import json
from unittest.mock import Mock

import pytest
from test_gmud import create
from test_promotion_gate import NOW
from test_signed_git_publisher import bundle_for

from automation.controlled_publisher import ControlledPublisher, stable_control
from automation.gmud import change_identity


def publisher_fixture(tmp_path,journal,release,monkeypatch):
    bundle=bundle_for(release)
    create(journal,release)
    journal.transition(change_identity(release),'APPROVED',now=NOW)
    identity=change_identity(release)
    journal.transition(identity,'DEPLOYING',now=NOW)
    repo=tmp_path/'gitops';(repo/'.git').mkdir(parents=True)
    folder=repo/'gitops/apps/orders-worker/dev';folder.mkdir(parents=True)
    current=copy.deepcopy(bundle['resources']['deployment.json'])
    current['spec']['template']['spec']['containers'][0]['image']=current['spec']['template']['spec']['containers'][0]['image'].split('@')[0]+'@'+release['current_digest']
    (folder/'deployment.json').write_text(json.dumps(current))
    fake=Mock(repo=repo,origin='protected-origin',fingerprint='A'*40,git_env={})
    def git(*args):
        if args==('remote','get-url','origin'):return 'protected-origin'
        if args==('rev-parse','origin/main'):return 'a'*40
        if args==('rev-parse','HEAD'):return 'b'*40
        return ''
    fake.git.side_effect=git
    monkeypatch.setattr('automation.controlled_publisher.verify_commit',Mock())
    return ControlledPublisher(fake,journal,str(tmp_path/'effects.db')),bundle,identity,fake


def test_gmud_effect_scoped_cas_and_once_only(tmp_path,journal,release,monkeypatch):
    publisher,bundle,identity,fake=publisher_fixture(tmp_path,journal,release,monkeypatch)
    revision=publisher.publish(identity,'deploy:bootstrap',bundle['resources'],expected_digest=release['current_digest'])
    assert revision=='b'*40
    publisher.publish(identity,'deploy:bootstrap',bundle['resources'],expected_digest=release['current_digest'])
    assert sum(c.args[:1]==('push',) for c in fake.git.call_args_list)==1
    proof=json.loads((fake.repo/'gitops/releases/orders-worker-dev.json').read_text())
    assert proof['gmud_id']=='RCL000001' and proof['glpi_change_id']==1 and proof['source_sha']==release['commit']


def test_superseded_or_other_namespace_cannot_publish(tmp_path,journal,release,monkeypatch):
    publisher,bundle,identity,fake=publisher_fixture(tmp_path,journal,release,monkeypatch)
    with pytest.raises(PermissionError):
        publisher.publish(identity,'deploy:bootstrap',bundle['resources'],expected_digest=release['digest'])
    bundle['resources']['configmap.json']['metadata']['namespace']='orders-api'
    with pytest.raises(PermissionError):
        publisher.publish(identity,'deploy:bootstrap',bundle['resources'],expected_digest=release['current_digest'])
    assert not any(c.args[:1]==('push',) for c in fake.git.call_args_list)


def test_ambiguous_push_never_repeats(tmp_path,journal,release,monkeypatch):
    publisher,bundle,identity,fake=publisher_fixture(tmp_path,journal,release,monkeypatch)
    original=fake.git.side_effect
    def git(*args):
        if args[:1]==('push',):raise TimeoutError('Lost push response')
        return original(*args)
    fake.git.side_effect=git
    with pytest.raises(TimeoutError):publisher.publish(identity,'deploy:bootstrap',bundle['resources'],expected_digest=release['current_digest'])
    with pytest.raises(PermissionError,match='Ambiguous'):publisher.publish(identity,'deploy:bootstrap',bundle['resources'],expected_digest=release['current_digest'])
    assert sum(c.args[:1]==('push',) for c in fake.git.call_args_list)==1


def test_stable_control_preserves_business_configuration_and_hpa_ownership(release):
    deployment=bundle_for(release)['resources']['deployment.json']
    deployment['spec']['template']['metadata']={'labels':{'app':'orders-worker'}}
    transformed,control=stable_control(deployment,release['digest'])
    assert 'replicas' not in transformed['spec']
    assert transformed['spec']['template']['spec']['terminationGracePeriodSeconds']==240
    assert control['data']['mode']=='run'
    env=transformed['spec']['template']['spec']['containers'][0]['env']
    assert {'name':'RELEASE_VERSION','value':release['digest']} in env


def test_terminal_rollback_cannot_publish_application_or_new_version(tmp_path,journal,release,monkeypatch):
    publisher,bundle,identity,_fake=publisher_fixture(tmp_path,journal,release,monkeypatch)
    record=journal.read(identity)
    journal.transition(identity,'ROLLBACK_RUNNING',now=NOW,details={'reason':'isolated'})
    journal.transition(identity,'ROLLED_BACK',now=NOW,details={'rollback_validated':True})
    with pytest.raises(PermissionError):publisher.publish(identity,'drain-complete:rollback',bundle['resources'],expected_digest=release['current_digest'])
    with pytest.raises(PermissionError):publisher.publish(identity,'deploy:replay',{'hpa.json':bundle['resources']['hpa.json']},expected_digest=release['current_digest'])
    assert record['rcl_id']=='RCL000001'
