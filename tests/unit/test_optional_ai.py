from unittest.mock import Mock

from test_promotion_gate import NOW

from automation.promotion_gate import PromotionGate, evaluate


def test_explicit_trusted_disabled_mode_allows_worker_without_model(release):
    assert evaluate(release, None, now=NOW, ai_enabled=False)[0] == 'APPROVE'
    assert evaluate(release, {'invalid': 'not used'}, now=NOW, ai_enabled=False)[0] == 'APPROVE'
    assert evaluate(release, None, now=NOW, ai_enabled=True)[0] == 'BLOCK'


def test_disabled_ai_does_not_override_security_human_or_runtime(release):
    release['checks']['scan_passed'] = False
    assert evaluate(release, None, now=NOW, ai_enabled=False)[0] == 'REJECT'
    release['checks']['scan_passed'] = True
    release['checks']['sensitive_changes'] = True
    assert evaluate(release, None, now=NOW, ai_enabled=False)[0] == 'HUMAN_REVIEW'
    release['checks']['sensitive_changes'] = False
    release['service'] = 'orders-api'
    assert evaluate(release, None, now=NOW, ai_enabled=False)[0] == 'HUMAN_REVIEW'
    release['enforcement_verified'] = False
    assert evaluate(release, None, now=NOW, ai_enabled=False)[0] == 'BLOCK'


def test_receipt_records_disabled_ai_and_dispatch_once(gate, release):
    gate = PromotionGate(gate.database, gate.key, set(gate.approvers), ai_enabled=False)
    receipt = gate.decide(release, None, now=NOW)
    assert receipt['payload']['ai_mode'] == 'DISABLED'
    publish = Mock()
    gate.promote(receipt, release, publish, now=NOW)
    publish.assert_called_once()


def test_default_gate_disables_ai_and_preserves_api_human_approval(gate, release):
    import pytest

    default = PromotionGate(gate.database, gate.key, set(gate.approvers))
    assert evaluate(release, None, now=NOW)[0] == 'APPROVE'
    receipt = default.decide(release, None, now=NOW)
    assert receipt['payload']['ai_mode'] == 'DISABLED'
    assert receipt['payload']['policy_version'] == 'cosmotech-promotion-v2-deterministic'
    release['service'] = 'orders-api'
    with pytest.raises(PermissionError, match='human review'):
        default.decide(release, None, now=NOW)


def test_kestra_deterministic_execution_needs_no_bedrock_but_opt_in_does(release):
    import pytest

    from automation.promotion_runtime import KestraClient

    client = KestraClient('https://kestra.example.invalid', 'user', 'test')
    client.request = Mock(return_value={
        'id': release['execution_id'], 'namespace': 'cosmotech.promotion',
        'flowId': 'promotion_gate', 'flowRevision': 2, 'state': {'current': 'RUNNING'},
        'inputs': {k: release[k] for k in ['service', 'environment', 'commit', 'digest', 'run_id']},
        'taskRunList': [{'taskId': name, 'state': {'current': 'SUCCESS'}}
                        for name in ['verified_evidence', 'deterministic_policy']]})
    client.require_authorizing_execution(release, flow_revision=2)
    with pytest.raises(PermissionError, match='tasks did not complete'):
        client.require_authorizing_execution(release, flow_revision=2, ai_enabled=True)
    client.request.return_value['taskRunList'].pop()
    with pytest.raises(PermissionError, match='tasks did not complete'):
        client.require_authorizing_execution(release, flow_revision=2)


def test_coordinator_notifies_api_with_no_ai_and_never_dispatches(gate, release):
    from test_promotion_coordinator import setup_coordinator
    from test_signed_git_publisher import bundle_for

    default = PromotionGate(gate.database, gate.key, set(gate.approvers))
    release['service'] = 'orders-api'
    bundle = bundle_for(release)
    coordinator, portal, notifier, publisher, worker = setup_coordinator(default)
    assert coordinator.complete(release, bundle, None, now=NOW)['decision'] == 'HUMAN_REVIEW'
    portal.register.assert_called_once_with(release, None)
    notifier.send.assert_called_once_with(release, None)
    publisher.publish.assert_not_called()
    worker.assert_not_called()
    coordinator.kestra.require_authorizing_execution.assert_called_once_with(
        release, flow_revision=1, ai_enabled=False,flow_id="promotion_gate")


def test_notification_and_portal_display_disabled_bedrock(tmp_path, release):
    import base64

    from test_approval_portal import request

    from automation.approval_portal import ApprovalPortal
    from automation.promotion_notification import notification

    message = notification(release, None, recipient='owner@example.invalid',
                           approval_origin='https://kestra.example.invalid')
    assert 'Bedrock' not in message.get_content()
    assert 'AI advisory:' not in message.get_content()
    portal = ApprovalPortal(str(tmp_path / 'portal.db'), b'x' * 32, {'owner'},
                            lambda *args: True, 'https://kestra.example.invalid')
    identity = portal.register(release, None)
    auth = 'Basic ' + base64.b64encode(b'owner:test').decode()
    # Identity adapter also requires current, release-bound evidence.
    from datetime import UTC, datetime
    release['observed_at'] = datetime.now(UTC).isoformat()
    identity = portal.register(release, None)
    code, body = request(portal, '/releases/' + identity, auth=auth)
    assert code.startswith('200')
    assert 'Bedrock' not in body
    assert 'Criticidade e política' in body
    assert portal.decision(identity) is None


def test_cli_defaults_to_deterministic_evaluation(tmp_path, release, monkeypatch):
    import json
    from datetime import UTC, datetime

    from automation.promotion_cli import main

    release['observed_at'] = datetime.now(UTC).isoformat()
    source, output = tmp_path / 'release.json', tmp_path / 'decision.json'
    source.write_text(json.dumps(release))
    monkeypatch.delenv('BEDROCK_PROMOTION_ENABLED', raising=False)
    monkeypatch.setattr('sys.argv', ['promotion_cli', 'evaluate', '--release', str(source),
                                    '--output', str(output)])
    main()
    assert json.loads(output.read_text()) == {
        'decision': 'APPROVE', 'reason': 'worker_deterministic_all_conditions_met_ai_disabled'}


def test_cli_invalid_trusted_mode_cannot_silently_authorize(tmp_path, release, monkeypatch):
    import json

    import pytest

    from automation.promotion_cli import main

    source, output = tmp_path / 'release.json', tmp_path / 'decision.json'
    source.write_text(json.dumps(release))
    monkeypatch.setenv('BEDROCK_PROMOTION_ENABLED', 'typo')
    monkeypatch.setattr('sys.argv', ['promotion_cli', 'evaluate', '--release', str(source),
                                    '--output', str(output)])
    with pytest.raises(SystemExit, match='Invalid trusted Bedrock configuration'):
        main()
    assert not output.exists()
