from unittest.mock import Mock

import pytest
from test_promotion_gate import NOW, human
from test_signed_git_publisher import bundle_for

from automation.promotion_coordinator import PromotionCoordinator


def setup_coordinator(gate):
    portal, notifier, publisher, worker = Mock(), Mock(), Mock(), Mock()
    portal.decision.return_value = None
    notifier.send.return_value = "mock-sns-accepted"
    coordinator = PromotionCoordinator(gate, Mock(), portal, notifier, publisher, worker, flow_revision=1)
    return coordinator, portal, notifier, publisher, worker


def test_worker_is_dispatched_to_canary_only_once(release, ai, gate):
    bundle = bundle_for(release)
    coordinator, _, notifier, publisher, worker = setup_coordinator(gate)
    result = coordinator.complete(release, bundle, ai, now=NOW)
    assert result["delivery_validated"] is False
    worker.assert_called_once()
    publisher.publish.assert_not_called()
    notifier.send.assert_not_called()
    with pytest.raises(PermissionError):
        coordinator.complete(release, bundle, ai, now=NOW)
    assert worker.call_count == 1


def test_api_authenticated_human_is_one_logical_gate_and_notified_once(release, ai, gate):
    release["service"], ai["service"], ai["criticality"] = "orders-api", "orders-api", "HIGH"
    bundle = bundle_for(release)
    coordinator, portal, notifier, publisher, worker = setup_coordinator(gate)
    for _ in range(2):
        assert coordinator.complete(release, bundle, ai, now=NOW)["decision"] == "HUMAN_REVIEW"
    notifier.send.assert_called_once()
    publisher.publish.assert_not_called()
    portal.decision.return_value = human(release)
    assert coordinator.complete(release, bundle, ai, now=NOW)["decision"] == "APPROVE"
    publisher.publish.assert_called_once()
    worker.assert_not_called()


def test_kestra_outage_blocks_all_real_adapters(release, ai, gate):
    bundle = bundle_for(release)
    coordinator, _, notifier, publisher, worker = setup_coordinator(gate)
    coordinator.kestra.require_authorizing_execution.side_effect = TimeoutError()
    with pytest.raises(TimeoutError):
        coordinator.complete(release, bundle, ai, now=NOW)
    worker.assert_not_called()
    publisher.publish.assert_not_called()
    notifier.send.assert_not_called()
