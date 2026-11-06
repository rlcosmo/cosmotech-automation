import hashlib
import io
import json
import secrets
import sqlite3
from http.cookies import SimpleCookie
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from automation.native_viewer import COOKIE, LIFETIME, NativeViewer, read_allowed

ORIGIN='https://kestra.cosmotechsolutions.com.br'
USER='laboratorio@cosmotechsolutions.com.br'


def call(app,path,method='POST',body=None,cookie='',origin=ORIGIN,**extra):
    data=json.dumps(body).encode() if body is not None else b''
    result={}
    env={'PATH_INFO':path,'REQUEST_METHOD':method,'HTTP_ORIGIN':origin,'HTTP_COOKIE':cookie,
         'CONTENT_LENGTH':str(len(data)),'wsgi.input':io.BytesIO(data),'REMOTE_ADDR':'127.0.0.1',**extra}
    def start(status,headers):result.update(status=int(status.split()[0]),headers=headers)
    result['body']=b''.join(app(env,start))
    return result


@pytest.fixture
def fixture(tmp_path):
    password=secrets.token_urlsafe(24)
    auth=Mock(users={USER:{'enabled':True}},side_effect=lambda u,p:u==USER and p==password)
    forward=Mock(return_value=(204,[('Set-Cookie','BASIC_AUTH=native-admin-cookie; HttpOnly')],b''))
    clock=Mock(return_value=1000.)
    audit=Mock()
    app=NativeViewer(str(tmp_path/'sessions.db'),auth,{USER},ORIGIN,forward=forward,clock=clock,audit=audit)
    return SimpleNamespace(app=app,password=password,auth=auth,forward=forward,clock=clock,audit=audit)


def login(f):
    result=call(f.app,'/api/v1/login',body={'username':USER,'password':f.password})
    assert result['status']==204
    cookies=SimpleCookie()
    for key,value in result['headers']:
        if key=='Set-Cookie':cookies.load(value)
    return cookies[COOKIE].value,result


def test_native_login_uses_opaque_hashed_session_and_no_native_admin_cookie(fixture):
    token,result=login(fixture)
    assert fixture.app.session(token)==USER
    assert fixture.password.encode() not in result['body']
    assert all(fixture.password not in value for _,value in result['headers'])
    cookies=[v for k,v in result['headers'] if k=='Set-Cookie']
    assert any(COOKIE+'=' in v and 'Secure' in v and 'HttpOnly' in v and 'SameSite=Strict' in v for v in cookies)
    assert any('BASIC_AUTH=;' in v and 'Max-Age=0' in v for v in cookies)
    with sqlite3.connect(fixture.app.database) as db:
        stored=db.execute('SELECT token_hash FROM native_view_sessions').fetchone()[0]
    assert stored==hashlib.sha256(token.encode()).hexdigest() and stored!=token
    fixture.forward.assert_not_called()


def test_native_admin_login_and_logout_preserve_upstream_behavior(fixture):
    result=call(fixture.app,'/api/v1/login',body={'username':'admin@example.invalid','password':'isolated-admin'})
    assert result['status']==204
    assert ('Set-Cookie','BASIC_AUTH=native-admin-cookie; HttpOnly') in result['headers']
    assert fixture.forward.call_args.args[1]==json.dumps({'username':'admin@example.invalid','password':'isolated-admin'}).encode()
    call(fixture.app,'/api/v1/logout',cookie='BASIC_AUTH=native-admin-cookie')
    assert fixture.forward.call_args.args[0]=='/api/v1/logout'


@pytest.mark.parametrize('path,method',[
    ('/api/v1/main/flows','POST'),('/api/v1/main/flows/cosmotech.promotion/release_pipeline','DELETE'),
    ('/api/v1/main/executions/real/actions/resume','POST'),
    ('/api/v1/main/executions/real/actions/replay','POST'),
    ('/api/v1/main/executions/webhook/namespace/flow/key','GET'),
    ('/api/v1/main/flows','PUT'),('/api/v1/main/namespaces/files','PATCH'),
    ('/api/v1/main/ai/generate','POST'),('/api/v1/main/basicAuth','POST'),
    ('/api/v1/main/flows/../executions/webhook/a/b/c','GET'),
    ('/api/v1/main/flows%2f..%2fexecutions','GET'),
    ('/api/v1/main/executions/webhook;matrix=1/namespace/flow/key','GET'),
])
def test_viewer_mutation_webhook_and_path_bypass_are_denied(fixture,path,method):
    token,_=login(fixture)
    result=call(fixture.app,'/_kestra/auth',method='GET',cookie=COOKIE+'='+token,
                HTTP_X_ORIGINAL_METHOD=method,HTTP_X_ORIGINAL_PATH=path,HTTP_X_ORIGINAL_ORIGIN=ORIGIN)
    assert result['status']==403
    fixture.forward.assert_not_called()


def test_read_authorization_preserves_csrf_cookie_and_authenticated_identity(fixture):
    token,_=login(fixture)
    result=call(fixture.app,'/_kestra/auth',method='GET',cookie=COOKIE+'='+token+'; BASIC_AUTH=forged; csrf=actual',
                HTTP_X_ORIGINAL_METHOD='GET',HTTP_X_ORIGINAL_PATH='/api/v1/main/executions/search')
    assert result['status']==204
    headers=dict(result['headers'])
    assert headers['X-CosmoTech-Principal']==USER and headers['X-CosmoTech-Role']=='viewer'
    assert headers['X-CosmoTech-Cookie']=='csrf=actual'
    assert fixture.audit.call_args.args[0]['principal']==USER


def test_expired_invalid_or_disabled_operator_sessions_fail_closed(fixture):
    token,_=login(fixture)
    assert fixture.app.session('forged') is None
    fixture.clock.return_value=1000+LIFETIME
    assert fixture.app.session(token) is None
    fixture.clock.return_value=1000
    fixture.auth.users[USER]['enabled']=False
    assert fixture.app.session(token) is None


def test_login_and_logout_require_same_origin_and_logout_revokes(fixture):
    bad=call(fixture.app,'/api/v1/login',body={'username':USER,'password':fixture.password},origin='https://outside.invalid')
    assert bad['status']==403
    token,_=login(fixture)
    assert call(fixture.app,'/api/v1/logout',cookie=COOKIE+'='+token,origin='https://outside.invalid')['status']==403
    assert fixture.app.session(token)==USER
    assert call(fixture.app,'/api/v1/logout',cookie=COOKIE+'='+token)['status']==204
    assert fixture.app.session(token) is None


def test_wrong_password_is_not_forwarded_to_admin_and_no_internal_auth_access(fixture):
    assert call(fixture.app,'/api/v1/login',body={'username':USER,'password':'incorrect'})['status']==401
    fixture.forward.assert_not_called()
    assert call(fixture.app,'/_kestra/auth',method='GET',REMOTE_ADDR='192.0.2.1')['status']==404
    assert call(fixture.app,'/_kestra/auth',method='GET')['status']==401


def test_reviewed_dashboard_read_post_requires_same_origin():
    path='/api/v1/main/dashboards/charts/preview'
    assert read_allowed('POST',path,ORIGIN,ORIGIN)
    assert not read_allowed('POST',path,'https://outside.invalid',ORIGIN)
    assert not read_allowed('POST',path+'/execute',ORIGIN,ORIGIN)
    assert read_allowed('GET','/api/v1/main/logs/search','',ORIGIN)
