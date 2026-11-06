from datetime import timedelta

import pytest
from test_gmud import deploy_to_observation
from test_promotion_gate import NOW

from automation.gmud_observation import ObservationWindow


def signal(release, at):
    return {'observed_at':at.isoformat(),'current_digest':release['digest'], 'argo_synced':True,
            'healthy':True, 'metrics_reliable':True, 'critical_incidents':0, 'errors':[]}


def test_full_twelve_hours_and_persistence_not_a_temporary_process(journal, release):
    change=deploy_to_observation(journal, release)
    window=ObservationWindow(journal.database)
    for minute in range(721):
        at=NOW+timedelta(minutes=minute)
        window.record(change, signal(release, at), now=at)
    restarted=ObservationWindow(journal.database)
    with pytest.raises(PermissionError):restarted.completion(change, now=NOW+timedelta(hours=11))
    evidence=restarted.completion(change, now=NOW+timedelta(hours=12))
    assert evidence['samples'] == 721
    assert evidence['max_gap_seconds'] == 60
    assert journal.transition(change['id'],'CLOSED_SUCCESS',now=NOW+timedelta(hours=12), details=evidence)['outcome']=='SUCCESS'


@pytest.mark.parametrize('failure',['gap','unhealthy','superseded'])
def test_missing_or_bad_observation_never_auto_closes(journal, release, failure):
    change=deploy_to_observation(journal, release)
    window=ObservationWindow(journal.database)
    for minute in range(721):
        if failure=='gap' and 30 <= minute <= 40:continue
        at=NOW+timedelta(minutes=minute)
        sample=signal(release, at)
        if minute==50 and failure=='unhealthy':sample['healthy']=False
        if minute==50 and failure=='superseded':sample['current_digest']='sha256:'+'f'*64
        window.record(change,sample,now=at)
    with pytest.raises(PermissionError):window.completion(change,now=NOW+timedelta(hours=12))
    assert journal.read(change['id'])['phase']=='OBSERVING'
