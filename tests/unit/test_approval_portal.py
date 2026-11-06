import base64
import io
import re
import urllib.parse
from datetime import UTC, datetime

from automation.approval_portal import ApprovalPortal


def request(app, path, method="GET", body="", auth=None, origin="https://kestra.example.invalid"):
    status = []
    env = {"PATH_INFO": path, "REQUEST_METHOD": method, "CONTENT_LENGTH": str(len(body.encode())),
           "wsgi.input": io.BytesIO(body.encode()), "HTTP_ORIGIN": origin}
    if auth:
        env["HTTP_AUTHORIZATION"] = auth
    output = b"".join(app(env, lambda code, headers: status.append(code))).decode()
    return status[0], output


def test_human_browser_flow_records_only_authenticated_bound_decision(tmp_path, release, ai):
    release["observed_at"] = datetime.now(UTC).isoformat()
    app = ApprovalPortal(str(tmp_path / "portal.db"), b"x" * 32, {"owner"},
                         lambda user, pw: (user, pw) == ("owner", "test-only"),
                         "https://kestra.example.invalid/promotion")
    identity = app.register(release, ai)
    app.set_context(identity,{"phase":"HUMAN_REVIEW"})
    path = "/releases/" + identity
    assert request(app, path)[0].startswith("401")
    auth = "Basic " + base64.b64encode(b"owner:test-only").decode()
    code, body = request(app, path, auth=auth)
    assert code.startswith("200")
    csrf = re.search(r'name="csrf" value="([^"]+)"', body)[1]
    encoded = urllib.parse.urlencode({"csrf": csrf, "result": "APPROVE"})
    assert request(app, path, "POST", encoded, auth, origin="https://attacker.invalid")[0].startswith("403")
    assert app.decision(identity) is None
    assert request(app, path, "POST", encoded, auth)[0].startswith("200")
    assert app.decision(identity)["principal"] == "owner"
    assert app.decision(identity)["release_id"] == identity
    assert ">Aprovar<" not in request(app,path,auth=auth)[1]


def test_email_link_without_credentials_or_other_release_cannot_approve(tmp_path, release, ai):
    app = ApprovalPortal(str(tmp_path / "portal.db"), b"x" * 32, {"owner"},
                         lambda *args: True, "https://kestra.example.invalid")
    identity=app.register(release,ai)
    app.set_context(identity,{"phase":"HUMAN_REVIEW"})
    path = "/releases/" + identity
    assert request(app, path, "POST", "csrf=invalid&result=APPROVE")[0].startswith("403")
    assert request(app, "/releases/" + "f" * 64)[0].startswith("404")


def test_authenticated_rollback_request_is_separate_bound_and_non_deploying(tmp_path, release):
    from datetime import timedelta

    release['observed_at']=datetime.now(UTC).isoformat()
    app=ApprovalPortal(str(tmp_path/'portal.db'),b'x'*32,{'owner'},lambda *args:True,
                       'https://kestra.example.invalid')
    identity=app.register(release,None)
    app.set_context(identity,{'phase':'OBSERVING','change_id':7,'rollback_eligible':True,
                             'rollback_until':(datetime.now(UTC)+timedelta(hours=12)).isoformat()})
    auth='Basic '+base64.b64encode(b'owner:test-only').decode()
    path='/releases/'+identity+'/rollback'
    assert request(app,path)[0].startswith('401')
    code,body=request(app,path,auth=auth)
    assert code.startswith('200')
    csrf=re.search(r'name="csrf" value="([^"]+)"',body)[1]
    data=urllib.parse.urlencode({'csrf':csrf,'result':'APPROVE','reason':'Controlled synthetic test'})
    assert request(app,path,'POST',data,auth,origin='https://attacker.invalid')[0].startswith('403')
    assert app.rollback_request(identity) is None
    assert request(app,path,'POST',data,auth)[0].startswith('200')
    assert app.rollback_request(identity)['principal']=='owner'
    assert app.decision(identity) is None
    assert request(app,path,'POST',data,auth)[0].startswith('409')
    app.set_context(identity,{'phase':'OBSERVING','change_id':7,'rollback_eligible':True,
                             'rollback_until':(datetime.now(UTC)-timedelta(seconds=1)).isoformat()})
    assert request(app,path,auth=auth)[0].startswith('409')
