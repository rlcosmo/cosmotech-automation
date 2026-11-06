import json
from datetime import timedelta
from unittest.mock import Mock

import pytest
from test_promotion_gate import NOW

from automation.promotion_runtime import (
    CanaryExecutor,
    KestraClient,
    SnsApprovalNotifier,
)
from automation.worker_canary import CanaryController


def test_kestra_execution_is_not_a_caller_supplied_approved_flag(release):
    client = KestraClient("https://kestra.example.invalid", "test", "test")
    client.request = Mock(return_value={"id": release["execution_id"], "namespace": "cosmotech.promotion",
        "flowId": "promotion_gate", "flowRevision": 1, "state": {"current": "RUNNING"},
        "inputs": {k: release[k] for k in ["service", "environment", "commit", "digest", "run_id"]},
        "taskRunList": [{"taskId": name, "state": {"current": "SUCCESS"}}
                        for name in ["verified_evidence", "bedrock_assessment", "deterministic_policy"]]})
    client.require_authorizing_execution(release, flow_revision=1)
    client.request.return_value["flowId"] = "alternative_bypass"
    with pytest.raises(PermissionError):
        client.require_authorizing_execution(release, flow_revision=1)


def test_sns_reuses_only_the_confirmed_authorized_recipient(release, ai):
    client = Mock()
    subscribers = [{"Protocol": "email", "Endpoint": "owner@example.invalid", "SubscriptionArn": "confirmed"}]
    client.get_paginator.return_value.paginate.return_value = [{"Subscriptions": subscribers}]
    client.publish.return_value = {"MessageId": "accepted-not-delivery-proof"}
    notifier = SnsApprovalNotifier(client, "test-topic", "owner@example.invalid", "https://review.example.invalid")
    assert notifier.send(release, ai) == "accepted-not-delivery-proof"
    subscribers.append({"Protocol": "email", "Endpoint": "other@example.invalid", "SubscriptionArn": "confirmed"})
    with pytest.raises(PermissionError):
        notifier.send(release, ai)
    assert client.publish.call_count == 1


def test_executor_applies_only_persisted_worker_effect_once_and_drains_first(tmp_path, release, ai, gate):
    controller = CanaryController(str(tmp_path / "state.db"))
    receipt = gate.decide(release, ai, now=NOW)
    state = controller.start(receipt, release, gate.key.public_key(), now=NOW)
    run, publish, notify = Mock(), Mock(), Mock()
    executor = CanaryExecutor(controller.database, "expected-eks", run, publish, notify)
    executor.apply(state["id"], now=NOW)
    executor.apply(state["id"], now=NOW)
    assert run.call_count == 2  # Lease and scale, no repeat.
    assert all(command.args[0][3:5] == ["-n", "orders-worker"] for command in run.call_args_list)
    run.reset_mock()
    state = controller.step(state["id"], {}, now=NOW)
    executor.apply(state["id"], now=NOW)
    command = run.call_args_list[0].args[0]
    assert command[5:7] == ["patch", "configmap"]
    assert json.loads(command[-1])["data"]["lease.json"].find("drain") > -1
    assert run.call_count == 1  # No scale to zero before drain proof.
    publish.assert_called_once_with(state["id"], release["current_digest"], "rollback")


def test_expired_lease_never_starts_consumer(tmp_path, release, ai, gate):
    controller = CanaryController(str(tmp_path / "state.db"))
    state = controller.start(gate.decide(release, ai, now=NOW), release, gate.key.public_key(), now=NOW)
    run = Mock()
    executor = CanaryExecutor(controller.database, "expected-eks", run, Mock(), Mock())
    with pytest.raises(PermissionError):
        executor.apply(state["id"], now=NOW + timedelta(seconds=181))
    run.assert_not_called()


def test_deployment_email_is_explicitly_marked_as_non_promoting(release, ai):
    client = Mock()
    client.get_paginator.return_value.paginate.return_value = [{'Subscriptions': [
        {'Protocol': 'email', 'Endpoint': 'owner@example.invalid', 'SubscriptionArn': 'confirmed'}]}]
    client.publish.return_value = {'MessageId': 'test-accepted'}
    notifier = SnsApprovalNotifier(client, 'test-topic', 'owner@example.invalid', 'https://review.example.invalid')
    notifier.send(release, ai, test_only=True)
    payload = client.publish.call_args.kwargs
    assert payload['Subject'].startswith('[TESTE SEM DEPLOY]')
    assert 'Nenhuma imagem sera promovida' in payload['Message']
    assert 'Authenticated review: https://' in payload['Message']
