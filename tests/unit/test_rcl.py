"""Concurrency, retry, crash and legacy boundaries for corporate identifiers."""
import copy
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from test_gmud import FakeGLPI, create, policy
from test_promotion_gate import NOW

from automation.gmud import ChangeJournal


class LockedGLPI(FakeGLPI):
    def __init__(self):
        super().__init__()
        self.lock=threading.RLock()

    def create(self,*args):
        with self.lock:return super().create(*args)

    def all_changes(self):
        with self.lock:return super().all_changes()


def test_global_atomic_sequence_parallel_retries_and_restart(tmp_path,release):
    api=LockedGLPI();database=str(tmp_path/'sequence.db')
    def new_journal():return ChangeJournal(database,api,b'key-'*16,policy())
    new_journal()
    targets=[dict(release,commit=f'{i+1:040x}',run_id=str(i+1),execution_id='exec-'+str(i)) for i in range(12)]
    def perform(i):
        try:return create(new_journal(),targets[i])
        except PermissionError as error:
            assert 'Ambiguous creation' in str(error)
            return None
    with ThreadPoolExecutor(max_workers=4) as pool:
        rows=list(pool.map(perform,list(range(12))*2))
    rows=[r for r in rows if r is not None]+[perform(i) for i in range(12)]
    assert {r['rcl_id'] for r in rows}=={f'RCL{i:06d}' for i in range(1,13)}
    assert api.creates==12
    restarted=create(new_journal(),dict(release,commit='f'*40,run_id='999'))
    assert restarted['rcl_id']=='RCL000013'


def test_ambiguous_creation_burns_number_and_never_duplicates(journal,release):
    journal.glpi.fail_after_create=True
    with pytest.raises(TimeoutError):create(journal,release)
    journal.glpi.fail_after_create=False
    with pytest.raises(PermissionError):create(journal,release)
    next_release=dict(release,commit='f'*40,run_id='999')
    assert create(journal,next_release)['rcl_id']=='RCL000002'
    assert journal.reconcile(__import__('automation.gmud',fromlist=['change_identity']).change_identity(release))['rcl_id']=='RCL000001'
    assert journal.glpi.creates==2


def test_old_gmud_is_not_renumbered(journal,release):
    row=create(journal,release)
    native=journal.glpi.rows[row['change_id']]
    record=journal.decode(native)
    for key in ['rcl_id','title','application_version','current_version','change_id','native_status']:
        record.pop(key,None)
    native.update(name='COSMOTECH GMUD '+record['id'],content=journal.encode(record))
    with journal.db() as db:
        import json
        db.execute('UPDATE gmud SET record=? WHERE id=?',(json.dumps(record),record['id']))
    before=copy.deepcopy(native)
    assert 'rcl_id' not in create(journal,release)
    assert native==before


def test_title_and_corporate_id_are_signed_and_bound(journal,release):
    row=journal.ensure(release,owner='owner',ci_links=['https://ci.example'],implementation_plan='Implantar',rollback_plan='Reverter',now=NOW,application_version='1.0.1')
    assert row['title']=='RCL000001 | DEPLOY | ORDERS-WORKER | DEV | v1.0.1'
    journal.glpi.rows[row['change_id']]['name']='RCL000999 | altered'
    with pytest.raises(PermissionError):journal.read(row['id'])


def test_stale_database_restore_blocks_number_reuse(tmp_path,journal,release):
    create(journal,release)
    stale=ChangeJournal(str(tmp_path/'stale.db'),journal.glpi,journal.key,policy())
    with pytest.raises(PermissionError,match='Sequência local anterior'):
        create(stale,dict(release,commit='f'*40,run_id='999'))
    assert journal.glpi.creates==1


def test_failed_change_never_reuses_corporate_number(journal,release):
    first=create(journal,release)
    journal.transition(first['id'],'FAILED',now=NOW,details={'reason':'isolated test failure'})
    second=create(journal,dict(release,commit='f'*40,run_id='999'))
    assert second['rcl_id']=='RCL000002'
