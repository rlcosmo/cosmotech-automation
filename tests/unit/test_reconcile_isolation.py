import json
from unittest.mock import Mock

from automation.gate_runner import Runtime
from automation.gmud import change_identity


def test_one_invalid_observation_does_not_starve_other_releases(tmp_path,release,monkeypatch):
    import automation.gate_runner as runner
    monkeypatch.setattr(runner,'ROOT',tmp_path)
    folder=tmp_path/'executions';folder.mkdir()
    rows=[]
    for i in range(2):
        target=dict(release,commit=f'{i+1:040x}',execution_id='execution'+str(i))
        (folder/(target['execution_id']+'.json')).write_text(json.dumps({'release':target}))
        rows.append({'id':change_identity(target),'rcl_id':f'RCL{i+1:06d}','change_id':i+1,'phase':'OBSERVING'})
    first,second=Mock(),Mock();first.tick.side_effect=PermissionError('Observation gap');second.tick.return_value={'gmud_id':'RCL000002','phase':'OBSERVING'}
    monkeypatch.setattr('automation.delivery_runtime.DeliveryRuntime',Mock(side_effect=[first,second]))
    runtime=Runtime.__new__(Runtime);runtime.config={};runtime.execution=Mock();runtime.changes=Mock();runtime.changes.read.side_effect=rows;runtime.portal=Mock()
    result=runtime.tick('scheduler')
    assert result['reconciled'][0]['phase']=='RECONCILIATION_REQUIRED'
    assert result['reconciled'][1]['gmud_id']=='RCL000002'
    second.tick.assert_called_once()
