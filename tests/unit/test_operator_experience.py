import base64
import json
import secrets
import sqlite3
import stat
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
from test_approval_portal import request

from automation.approval_portal import ApprovalPortal
from automation.operator_auth import (
    OperatorAuthenticator,
    hash_password,
    verify_password,
)
from automation.portal_view import render
from automation.release_images import version_tag
from automation.release_projection import project


def test_password_hash_random_salt_and_wrong_password():
    password = secrets.token_urlsafe(24)
    encoded = hash_password(password)
    assert password not in encoded and encoded != hash_password(password)
    assert verify_password(password, encoded)
    assert not verify_password(secrets.token_urlsafe(24), encoded)
    assert not verify_password(password, encoded.replace("600000", "1"))


def test_operator_cannot_fall_back_to_admin_and_retries_bounded(tmp_path):
    password = secrets.token_urlsafe(24)
    p = tmp_path / "users.json"
    p.write_text(
        json.dumps(
            {
                "users": {
                    "operator-test": {
                        "password_hash": hash_password(password),
                        "role": "operator",
                        "enabled": True,
                    }
                }
            }
        )
    )
    fallback = Mock(return_value=False)
    with patch(
        "automation.operator_auth.Path.lstat",
        return_value=SimpleNamespace(st_uid=0, st_mode=stat.S_IFREG | 0o640),
    ):
        auth = OperatorAuthenticator(str(p), fallback)
    assert auth("operator-test", password)
    for _ in range(5):
        assert not auth("operator-test", "invalid")
    assert not auth("operator-test", password)
    fallback.assert_not_called()
    assert not auth("admin", "invalid")
    fallback.assert_called_once()


def test_reopening_approved_gmud_names_real_actor_and_never_offers_second_approval(
    tmp_path, release
):
    app = ApprovalPortal(
        str(tmp_path / "portal.db"),
        b"x" * 32,
        {"operator-test"},
        lambda u, p: True,
        "https://kestra.example.invalid",
    )
    identity = app.register(release, None)
    app.set_context(identity, {"phase": "HUMAN_REVIEW"})
    with sqlite3.connect(app.database) as db:
        db.execute(
            "INSERT INTO human_approvals VALUES (?,?)",
            (
                identity,
                json.dumps(
                    {
                        "principal": "operator-test",
                        "result": "APPROVE",
                        "timestamp": datetime.now(UTC).isoformat(),
                    }
                ),
            ),
        )
    auth = "Basic " + base64.b64encode(b"operator-test:isolated").decode()
    code, body = request(app, "/releases/" + identity, auth=auth)
    assert code.startswith("200") and "GMUD já foi aprovada por operator-test." in body
    assert body.index("GMUD já foi aprovada") < body.index("Ações da GMUD")
    assert ">Aprovar<" not in body
    assert "Conectado como operator-test" in body


def test_glpi_down_keeps_snapshot_and_decisions_and_blocks_mutation(tmp_path, release):
    app = ApprovalPortal(
        str(tmp_path / "portal.db"),
        b"x" * 32,
        {"operator-test"},
        lambda *a: True,
        "https://kestra.example.invalid",
    )
    identity = app.register(release, None)
    app.set_context(identity, {"rcl_id": "RCL000123", "phase": "HUMAN_REVIEW"})
    app.mark_sync_error(identity)
    auth = "Basic " + base64.b64encode(b"operator-test:isolated").decode()
    code, body = request(app, "/releases/" + identity, auth=auth)
    assert (
        code.startswith("200") and "GLPI indisponível" in body and "RCL000123" in body
    )
    assert ">Aprovar<" not in body and app.decision(identity) is None
    assert request(
        app, "/releases/" + identity, "POST", "csrf=any&result=APPROVE", auth
    )[0].startswith("503")
    assert app.decision(identity) is None
    app.mark_sync_success(identity)
    assert ">Aprovar<" in request(app, "/releases/" + identity, auth=auth)[1]


def test_read_only_execution_path_cannot_execute_actions(tmp_path, release):
    app = ApprovalPortal(
        str(tmp_path / "portal.db"),
        b"x" * 32,
        {"reader"},
        lambda *a: True,
        "https://kestra.example.invalid",
    )
    identity = app.register(release, None)
    app.set_context(identity, {"phase": "HUMAN_REVIEW"})
    auth = "Basic " + base64.b64encode(b"reader:isolated").decode()
    code, body = request(app, "/releases/" + identity + "/execution", auth=auth)
    assert code.startswith("200") and ">Aprovar<" not in body
    assert request(app, "/releases/" + identity + "/execution", "POST", "x=y", auth)[
        0
    ].startswith("405")


def test_projection_uses_actual_events_and_never_closes_observation_early(release):
    now = datetime.now(UTC).isoformat()
    c = {
        "release": release,
        "phase": "OBSERVING",
        "events": [{"type": "observing", "at": now}],
        "rollback_until": now,
    }
    stages = {s["key"]: s for s in project(c)["stages"]}
    assert (
        stages["observation"]["status"] == "RUNNING"
        and stages["closure"]["status"] == "PENDING"
    )
    assert stages["ci"]["started_at"] is None
    result = project(
        c,
        {
            "reconciliation_error": {"message": "private"},
            "operational_stages": {"argo": {"status": "RUNNING", "started_at": now}},
        },
    )
    assert result["failure"]["stage"] == "Argo CD"


@pytest.mark.parametrize(
    "service,version,build,wanted",
    [
        ("orders-worker", "1.3.0", "1", "worker-1.3.0-1"),
        ("orders-api", "2.0.3-lab", "72", "api-2.0.3-lab-72"),
    ],
)
def test_readable_image_alias_is_derived_from_real_version_build(
    service, version, build, wanted
):
    assert version_tag(service, version, build) == wanted


def test_actor_markup_is_escaped(release):
    page = render(
        release,
        {
            "phase": "OBSERVING",
            "events": [
                {
                    "type": "approved",
                    "at": datetime.now(UTC).isoformat(),
                    "details": {"principal": "<script>x</script>"},
                }
            ],
        },
        "csrf",
        now=datetime.now(UTC),
    )
    assert "<script>x</script>" not in page and "&lt;script&gt;x&lt;/script&gt;" in page


def test_new_main_flow_is_pinned_and_legacy_or_manual_execution_cannot_authorize(release):
    from automation.promotion_runtime import KestraClient
    client=KestraClient('https://kestra.example.invalid','test','test')
    client.request=Mock(return_value={'id':release['execution_id'],'namespace':'cosmotech.promotion','flowId':'release_pipeline','flowRevision':1,'state':{'current':'RUNNING'},'inputs':{k:release[k] for k in ['service','environment','commit','digest','run_id']},'taskRunList':[{'taskId':key,'state':{'current':'SUCCESS'}} for key in ['verified_evidence','deterministic_policy']]})
    client.require_authorizing_execution(release,flow_revision=1,flow_id='release_pipeline')
    client.request.return_value['flowId']='promotion_gate'
    with pytest.raises(PermissionError):client.require_authorizing_execution(release,flow_revision=1,flow_id='release_pipeline')


def test_operational_login_keeps_admin_and_has_no_decision_side_effect(tmp_path,release):
    app=ApprovalPortal(str(tmp_path/'portal.db'),b'x'*32,{'admin-test','laboratorio'},lambda *a:True,'https://kestra.example.invalid')
    identity=app.register(release,None)
    admin='Basic '+base64.b64encode(b'admin-test:isolated').decode()
    operator='Basic '+base64.b64encode(b'laboratorio:isolated').decode()
    assert request(app,'/releases/login',auth=admin)[0].startswith('401')
    assert request(app,'/releases/login',auth=operator)[0].startswith('302')
    assert app.decision(identity) is None
