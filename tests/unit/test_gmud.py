"""Isolated synthetic tests. No GLPI deployment or wall-clock window is claimed."""
import copy
from datetime import timedelta
from unittest.mock import Mock

import pytest
from test_promotion_gate import NOW

from automation.gmud import (
    CRITICAL,
    CUSTOM_CRITICAL,
    NON_CRITICAL,
    ProtectedPolicy,
    change_identity,
)


class FakeGLPI:
    def __init__(self):
        self.rows = {}
        self.creates = 0
        self.fail_after_create = self.fail_after_write = False

    def all_changes(self):
        return copy.deepcopy(list(self.rows.values()))

    def get_change(self, identifier):
        return copy.deepcopy(self.rows[identifier])

    def create(self, name, content):
        self.creates += 1
        identifier = len(self.rows) + 1
        self.rows[identifier] = {'id':identifier, 'name':name, 'content':content,
                                 'status':{'id':1}, 'is_deleted':False}
        if self.fail_after_create:
            raise TimeoutError('Lost create response')
        return identifier

    def write(self, identifier, content, native_status):
        self.rows[identifier].update(content=content, status={'id':native_status})
        if self.fail_after_write:
            raise TimeoutError('Lost patch response')
        return self.get_change(identifier)


def policy(worker=NON_CRITICAL):
    return ProtectedPolicy({'version':'cosmotech-gmud-v1', 'rollback_hours':12,
                            'applications':{'orders-api/dev':CRITICAL, 'orders-worker/dev':worker}})




def create(journal, release, *, synthetic=True):
    return journal.ensure(release, owner='authorized-owner', ci_links=['https://github.com/example/runs/123'],
                          implementation_plan='Controlled laboratory deploy', rollback_plan='Drain, restore digest',
                          now=NOW, synthetic=synthetic)


def deploy_to_observation(journal, release):
    row = create(journal, release)
    identity = row['id']
    journal.transition(identity, 'APPROVED', now=NOW, details={'principal':'policy-engine'})
    journal.transition(identity, 'DEPLOYING', now=NOW)
    return journal.transition(identity, 'OBSERVING', now=NOW, details={'technical_validation':True})


def test_worker_full_lifecycle_requires_twelve_hours(journal, release):
    row = deploy_to_observation(journal, release)
    assert row['phase'] == 'OBSERVING' and row['native_status'] == 8
    with pytest.raises(PermissionError):
        journal.transition(row['id'], 'CLOSED_SUCCESS', now=NOW+timedelta(hours=11),
                           details={'continuous_observation_verified':True})
    with pytest.raises(PermissionError):
        journal.transition(row['id'], 'CLOSED_SUCCESS', now=NOW+timedelta(hours=12))
    row = journal.transition(row['id'], 'CLOSED_SUCCESS', now=NOW+timedelta(hours=12),
                             details={'continuous_observation_verified':True})
    assert row['outcome'] == 'SUCCESS' and row['native_status'] == 6
    assert row['synthetic'] is True


def test_api_requires_human_and_ci_cannot_reclassify(release):
    release['service'] = 'orders-api'
    release['criticality'] = NON_CRITICAL
    assert policy().category(release) == CRITICAL
    release.pop('criticality')
    assert policy().decide(release, [], now=NOW) == ('HUMAN_REVIEW', 'critical_change')


def histories(release):
    result=[]
    for i in range(5):
        target=dict(release, commit=f'{i+1:040x}', digest='sha256:'+f'{i+1:064x}', run_id=str(100+i))
        result.append({'id':change_identity(target),'release':target,'phase':'CLOSED_SUCCESS',
            'synthetic':False,'outcome':'SUCCESS','errors':[],'rollback':False,'native_status':6,'change_id':i+1,
            'validated_at':(NOW-timedelta(days=i+2)).isoformat(),
            'completed_at':(NOW-timedelta(days=i+1)).isoformat()})
    return result


def test_custom_five_real_auditable_successes(release):
    assert policy(CUSTOM_CRITICAL).decide(release, histories(release), now=NOW)[0] == 'APPROVE'


@pytest.mark.parametrize('mutation', ['failure','rollback','open','synthetic','insufficient','early_close'])
def test_custom_missing_or_bad_history_requires_human(release, mutation):
    rows = histories(release)
    if mutation == 'failure': rows[0]['errors'] = ['deployment failed']
    if mutation == 'rollback': rows[0]['rollback'] = True
    if mutation == 'open': rows[0]['phase'] = 'OBSERVING'
    if mutation == 'synthetic': rows[0]['synthetic'] = True
    if mutation == 'insufficient': rows.pop()
    if mutation == 'early_close': rows[0]['validated_at'] = rows[0]['completed_at']
    assert policy(CUSTOM_CRITICAL).decide(release, rows, now=NOW)[0] == 'HUMAN_REVIEW'


def test_custom_uses_same_application_environment_and_latest_five(release):
    rows = histories(release)
    rows[0]['release'] = dict(release, service='orders-api')
    assert policy(CUSTOM_CRITICAL).decide(release, rows, now=NOW)[0] == 'HUMAN_REVIEW'
    rows = histories(release)
    rows.append(dict(rows[0], change_id=99, phase='ROLLED_BACK', completed_at=NOW.isoformat()))
    assert policy(CUSTOM_CRITICAL).decide(release, rows, now=NOW)[0] == 'HUMAN_REVIEW'


@pytest.mark.parametrize('kind', [CRITICAL, NON_CRITICAL, CUSTOM_CRITICAL])
def test_no_classification_overrides_failed_technical_checks(release, kind):
    registry = policy(kind)
    release['checks']['scan_passed'] = False
    assert registry.decide(release, histories(release), now=NOW)[0] == 'REJECT'
    release['checks']['scan_passed'] = True
    release['enforcement_verified'] = False
    assert registry.decide(release, histories(release), now=NOW)[0] == 'BLOCK'


def test_retry_stable_identity_and_no_duplicate(journal, release):
    row = create(journal, release)
    refreshed = dict(release, observed_at=(NOW+timedelta(seconds=30)).isoformat())
    assert create(journal, refreshed)['change_id'] == row['change_id']
    assert journal.glpi.creates == 1
    with pytest.raises(PermissionError, match='Original Kestra'):
        create(journal, dict(release, execution_id='another-execution'))


def test_ambiguous_creation_reconciles_without_second_post(journal, release):
    journal.glpi.fail_after_create = True
    with pytest.raises(TimeoutError): create(journal, release)
    with pytest.raises(PermissionError): create(journal, release)
    assert journal.glpi.creates == 1
    result = journal.reconcile(change_identity(release))
    assert result['phase'] == 'CREATED'
    assert journal.glpi.creates == 1


def test_lost_patch_response_and_glpi_failure_block(journal, release):
    row = create(journal, release)
    journal.glpi.fail_after_write = True
    with pytest.raises(TimeoutError):journal.transition(row['id'], 'APPROVED', now=NOW)
    with pytest.raises(PermissionError):journal.read(row['id'])
    assert journal.reconcile(row['id'])['phase'] == 'APPROVED'
    journal.glpi.all_changes = Mock(side_effect=TimeoutError())
    with pytest.raises(TimeoutError):journal.history()


def test_rollback_inside_window_auth_digest_duplicate_and_drain(journal, release):
    row = deploy_to_observation(journal, release)
    args = {'principal':'authorized-owner','reason':'Synthetic validation failure', 'now':NOW+timedelta(hours=1),
            'allowed_principals':{'authorized-owner'},'current_digest':release['digest']}
    with pytest.raises(PermissionError):journal.request_rollback(row['id'], **dict(args, principal='attacker'))
    with pytest.raises(PermissionError):journal.request_rollback(row['id'], **dict(args, current_digest='sha256:'+'f'*64))
    assert journal.request_rollback(row['id'], **args)['phase'] == 'ROLLBACK_REQUESTED'
    with pytest.raises(PermissionError):journal.request_rollback(row['id'], **args)
    journal.transition(row['id'], 'ROLLBACK_RUNNING', now=args['now'])
    with pytest.raises(PermissionError):journal.transition(row['id'], 'ROLLED_BACK', now=args['now'])
    row=journal.transition(row['id'], 'ROLLED_BACK', now=args['now'], details={'rollback_validated':True})
    assert row['rollback'] is True and row['outcome'] != 'SUCCESS'
    with pytest.raises(PermissionError):journal.transition(row['id'], 'CLOSED_SUCCESS', now=NOW+timedelta(hours=13))


def test_after_twelve_hours_requires_new_change(journal, release):
    row=deploy_to_observation(journal, release)
    with pytest.raises(PermissionError, match='new GMUD'):
        journal.request_rollback(row['id'], principal='authorized-owner', reason='Synthetic rollback request',
            now=NOW+timedelta(hours=12),allowed_principals={'authorized-owner'},current_digest=release['digest'])


def test_content_tampering_or_native_state_bypass_is_detected(journal, release):
    row=create(journal, release)
    native=journal.glpi.rows[row['change_id']]
    native['content']=native['content'].replace('NON_CRITICAL','CRITICAL')
    with pytest.raises(PermissionError):journal.read(row['id'])
    native['content']=journal.encode({k:v for k,v in row.items() if k not in {'change_id','native_status'}})
    native['status']['id']=6
    with pytest.raises(PermissionError):journal.read(row['id'])


def test_incomplete_auditable_history_requires_human_instead_of_assuming_success(release):
    rows=histories(release)
    rows[0].pop('validated_at')
    assert policy(CUSTOM_CRITICAL).decide(release,rows,now=NOW)[0]=='HUMAN_REVIEW'


def test_copied_gmud_cannot_satisfy_five_successful_changes(release):
    rows=histories(release)
    rows[-1]=dict(rows[0],change_id=99)
    assert policy(CUSTOM_CRITICAL).decide(release,rows,now=NOW)[0]=='HUMAN_REVIEW'


def test_duplicate_signed_native_record_blocks_history(journal,release):
    created=create(journal,release)
    duplicate=journal.glpi.get_change(created['change_id'])
    duplicate['id']=99
    journal.glpi.rows[99]=duplicate
    with pytest.raises(PermissionError,match='duplicados'):
        journal.history()
