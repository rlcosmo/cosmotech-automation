import base64
import json
import sqlite3
from datetime import UTC, datetime

from test_approval_portal import request

from automation.approval_portal import ApprovalPortal
from automation.portal_view import approval_mode, render

AUTH='Basic '+base64.b64encode(b'owner:test-only').decode()


def portal(tmp_path):
    return ApprovalPortal(str(tmp_path/'portal.db'),b'x'*32,{'owner'},lambda user,pw:(user,pw)==('owner','test-only'),'https://kestra.example.invalid')


def test_index_authentication_empty_state_and_no_mutation(tmp_path):
    app=portal(tmp_path)
    assert request(app,'/releases/')[0].startswith('401')
    stranger='Basic '+base64.b64encode(b'stranger:test-only').decode()
    assert request(app,'/releases/',auth=stranger)[0].startswith('401')
    assert request(app,'/releases/',auth='Basic invalid')[0].startswith('401')
    code,page=request(app,'/releases/',auth=AUTH)
    assert code.startswith('200') and 'Nenhuma aprovação humana pendente' in page
    assert request(app,'/releases/','POST','',AUTH)[0].startswith('405')
    with sqlite3.connect(app.database) as db:assert db.execute('SELECT COUNT(*) FROM human_approvals').fetchone()[0]==0


def test_index_distinguishes_real_automatic_and_human_decisions(tmp_path,release):
    app=portal(tmp_path)
    auto=app.register(release,None)
    app.set_context(auto,{'rcl_id':'RCL000001','criticality':'NON_CRITICAL','phase':'OBSERVING',
        'events':[{'type':'approved','details':{'principal':'policy-engine'}}]})
    human=dict(release,service='orders-api',commit='e'*40)
    pending=app.register(human,None)
    app.set_context(pending,{'rcl_id':'RCL000002','criticality':'CRITICAL','phase':'HUMAN_REVIEW'})
    code,page=request(app,'/releases/',auth=AUTH)
    assert code.startswith('200')
    assert page.index('RCL000002')<page.index('RCL000001')
    assert 'Interação humana necessária' in page and 'Aprovada automaticamente pela política' in page
    assert 'Criticidade: CRÍTICA' in page and 'Criticidade: NÃO CRÍTICA' in page
    assert '1 pendente(s)' in page
    with sqlite3.connect(app.database) as db:
        db.execute('INSERT INTO human_approvals VALUES (?,?)',(pending,json.dumps({'result':'APPROVE'})))
    page=request(app,'/releases/',auth=AUTH)[1]
    assert '0 pendente(s)' in page and 'Aprovação humana registrada' in page


def test_top_actions_and_explicit_criticality(release):
    page=render(release,{'phase':'HUMAN_REVIEW','criticality':'CUSTOM_CRITICAL'},'csrf',now=datetime.now(UTC))
    assert 'Criticidade da aplicação: <strong>CRÍTICA CUSTOMIZADA</strong>' in page
    assert page.index('>Aprovar<')<page.index('Identificação da mudança')
    assert page.count('>Aprovar<')==1
    assert '<a href="/releases/">Todas as aprovações</a>' in page
    assert '<!--top-actions-->' not in page


def test_noncritical_or_unattributed_does_not_claim_automatic_approval():
    assert 'automaticamente' not in approval_mode({'criticality':'NON_CRITICAL','phase':'CREATED'})
    assert approval_mode({'events':[{'type':'approved','details':{'principal':'owner'}}]})=='Aprovada por interação humana'
    assert 'origem não informada' in approval_mode({'events':[{'type':'approved'}]})
