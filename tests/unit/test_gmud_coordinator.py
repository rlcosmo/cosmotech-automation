from unittest.mock import Mock

import pytest
from test_gmud import FakeGLPI, policy
from test_promotion_coordinator import setup_coordinator
from test_promotion_gate import NOW, human
from test_signed_git_publisher import bundle_for

from automation.gmud import CUSTOM_CRITICAL, ChangeJournal, change_identity
from automation.promotion_gate import PromotionGate


def configured(tmp_path, gate, *, custom=False):
    gate=PromotionGate(gate.database,gate.key,set(gate.approvers))
    journal=ChangeJournal(str(tmp_path/'gmud.db'),FakeGLPI(),b'test-only-key-'*4,
                          policy(CUSTOM_CRITICAL) if custom else policy())
    coordinator,portal,notifier,publisher,worker=setup_coordinator(gate)
    coordinator.changes,coordinator.owner=journal,'authorized-owner'
    return coordinator,journal,portal,notifier,publisher,worker


def test_worker_gmud_approves_and_dispatches_but_is_not_closed(tmp_path,gate,release):
    coordinator,journal,_,notifier,publisher,worker=configured(tmp_path,gate)
    bundle=bundle_for(release)
    assert coordinator.complete(release,bundle,None,now=NOW)['dispatch']=='accepted'
    record=journal.read(change_identity(release))
    assert record['phase']=='DEPLOYING' and record['outcome'] is None
    worker.assert_called_once()
    publisher.publish.assert_not_called()
    notifier.send.assert_not_called()
    with pytest.raises(PermissionError):coordinator.complete(release,bundle,None,now=NOW)
    assert journal.glpi.creates==1


def test_api_gmud_notifies_waits_then_only_consumes_authenticated_record(tmp_path,gate,release):
    release['service']='orders-api'
    coordinator,journal,portal,notifier,publisher,worker=configured(tmp_path,gate)
    bundle=bundle_for(release)
    result=coordinator.complete(release,bundle,None,now=NOW)
    assert result['decision']=='HUMAN_REVIEW'
    record=journal.read(change_identity(release))
    assert record['phase']=='HUMAN_REVIEW'
    notifier.send.assert_called_once_with(release,None,change_id=record['change_id'],rcl_id=record['rcl_id'],application_version=record['application_version'])
    publisher.publish.assert_not_called()
    portal.decision.return_value=human(release)
    coordinator.complete(release,bundle,None,now=NOW)
    publisher.publish.assert_called_once()
    worker.assert_not_called()
    assert journal.read(record['id'])['phase']=='DEPLOYING'


def test_custom_history_missing_cannot_fall_through_to_worker_autoapproval(tmp_path,gate,release):
    coordinator,journal,_,_,publisher,worker=configured(tmp_path,gate,custom=True)
    assert coordinator.complete(release,bundle_for(release),None,now=NOW)['decision']=='HUMAN_REVIEW'
    assert journal.read(change_identity(release))['phase']=='HUMAN_REVIEW'
    publisher.publish.assert_not_called()
    worker.assert_not_called()


def test_glpi_outage_blocks_before_any_publish_or_notification(tmp_path,gate,release):
    coordinator,journal,_,notifier,publisher,worker=configured(tmp_path,gate)
    bundle=bundle_for(release)
    journal.glpi.all_changes=Mock(side_effect=TimeoutError('GLPI unavailable'))
    with pytest.raises(TimeoutError):coordinator.complete(release,bundle,None,now=NOW)
    worker.assert_not_called()
    publisher.publish.assert_not_called()
    notifier.send.assert_not_called()


def test_authenticated_api_rejection_is_recorded_in_glpi(tmp_path,gate,release):
    release['service']='orders-api'
    coordinator,journal,portal,_,publisher,worker=configured(tmp_path,gate)
    bundle=bundle_for(release)
    coordinator.complete(release,bundle,None,now=NOW)
    portal.decision.return_value=human(release,'REJECT')
    with pytest.raises(PermissionError):coordinator.complete(release,bundle,None,now=NOW)
    record=journal.read(change_identity(release))
    assert record['phase']=='REJECTED'
    assert record['native_status']==13
    assert record['errors']==['authenticated_human_rejected']
    publisher.publish.assert_not_called();worker.assert_not_called()
