from copy import deepcopy
from datetime import UTC, datetime, timedelta

import pytest

from automation.promotion_gate import (
    PromotionGate,
    ai_input,
    evaluate,
    release_id,
    verify_receipt,
)

NOW = datetime(2026, 11, 5, 20, tzinfo=UTC)


def human(release, result="APPROVE"):
    return {"principal": "authorized-owner", "release_id": release_id(release), "result": result,
            "timestamp": NOW.isoformat(), "execution_id": release["execution_id"]}


def test_medium_worker_valid_evidence_auto_approves(release, ai, gate):
    receipt = gate.decide(release, ai, now=NOW)
    assert receipt["payload"]["approver"] == "policy-engine"


def test_worker_blocking_vulnerability_rejected(release, ai):
    release["checks"]["scan_passed"] = False
    assert evaluate(release, ai, now=NOW)[0] == "REJECT"


def test_sensitive_worker_requires_human(release, ai, gate):
    release["checks"]["sensitive_changes"] = True
    assert evaluate(release, ai, now=NOW)[0] == "HUMAN_REVIEW"
    with pytest.raises(PermissionError):
        gate.decide(release, ai, now=NOW)


@pytest.mark.parametrize("invalid", [None, {}, "timeout", {"recommendation": "APPROVE"}])
def test_unavailable_or_invalid_ai_blocks(release, invalid):
    assert evaluate(release, invalid, now=NOW, ai_enabled=True)[0] == "BLOCK"


def test_api_always_requires_human(release, ai, gate):
    release["service"], ai["service"], ai["criticality"] = "orders-api", "orders-api", "HIGH"
    with pytest.raises(PermissionError):
        gate.decide(release, ai, now=NOW)
    receipt = gate.decide(release, ai, now=NOW, human=human(release))
    published = []
    gate.promote(receipt, release, published.append, now=NOW)
    assert published == [release]
    assert receipt["payload"]["approver"] == "authorized-owner"


def test_api_denial_blocks(release, ai, gate):
    release["service"], ai["service"], ai["criticality"] = "orders-api", "orders-api", "HIGH"
    with pytest.raises(PermissionError):
        gate.decide(release, ai, now=NOW, human=human(release, "REJECT"))


def test_no_bypass_without_gate_enforcement(release, ai, gate):
    release["enforcement_verified"] = False
    with pytest.raises(PermissionError):
        gate.decide(release, ai, now=NOW, human=human(release))


def test_approval_for_different_digest_blocked(release, ai, gate):
    receipt = gate.decide(release, ai, now=NOW)
    release["digest"] = "sha256:" + "e" * 64
    with pytest.raises(PermissionError):
        gate.promote(receipt, release, lambda _: pytest.fail("bypass"), now=NOW)


def test_kestra_failure_never_promotes(release, ai, gate):
    with pytest.raises(PermissionError):
        gate.decide(release, ai, now=NOW, kestra_available=False)


def test_receipt_tampering_and_expiry(release, ai, gate):
    receipt = gate.decide(release, ai, now=NOW)
    altered = deepcopy(receipt)
    altered["payload"]["approver"] = "attacker"
    for value, time in [(altered, NOW), (receipt, NOW + timedelta(minutes=11))]:
        with pytest.raises(PermissionError):
            verify_receipt(value, gate.key.public_key(), release, now=time)


def test_once_only_survives_process_restart(release, ai, gate):
    receipt = gate.decide(release, ai, now=NOW)
    gate.promote(receipt, release, lambda _: None, now=NOW)
    restarted = PromotionGate(gate.database, gate.key, set(gate.approvers))
    with pytest.raises(PermissionError):
        restarted.promote(receipt, release, lambda _: pytest.fail("replay"), now=NOW)


def test_ambiguous_publish_not_retried(release, ai, gate):
    receipt = gate.decide(release, ai, now=NOW)
    def lost_response(_):
        raise TimeoutError("Ambiguous dispatch")
    with pytest.raises(TimeoutError):
        gate.promote(receipt, release, lost_response, now=NOW)
    with pytest.raises(PermissionError):
        gate.promote(receipt, release, lambda _: pytest.fail("duplicate dispatch"), now=NOW)


@pytest.mark.parametrize("check", ["tests_passed", "digest_verified", "sqs_compatible", "incidents_clear"])
def test_human_cannot_override_hard_failures(release, ai, gate, check):
    release["service"], ai["service"], ai["criticality"] = "orders-api", "orders-api", "HIGH"
    release["checks"][check] = False
    with pytest.raises(PermissionError):
        gate.decide(release, ai, now=NOW, human=human(release))


def test_unknown_or_stale_evidence_fails_closed(release, ai):
    release["checks"]["tests_passed"] = "true"
    assert evaluate(release, ai, now=NOW)[0] == "BLOCK"
    release["checks"]["tests_passed"] = True
    release["observed_at"] = (NOW - timedelta(minutes=16)).isoformat()
    assert evaluate(release, ai, now=NOW)[0] == "BLOCK"


def test_ai_has_no_authority_and_only_sanitized_input(release, ai):
    payload = ai_input(release)
    assert "execution_id" not in payload and "run_id" not in payload
    ai["permission_override"] = True
    assert evaluate(release, ai, now=NOW, ai_enabled=True)[0] == "BLOCK"


def test_unauthorized_human_and_other_release_blocked(release, ai, gate):
    release["service"], ai["service"], ai["criticality"] = "orders-api", "orders-api", "HIGH"
    record = human(release)
    record["principal"] = "unauthorized"
    with pytest.raises(PermissionError):
        gate.decide(release, ai, now=NOW, human=record)
    record = human(release)
    record["release_id"] = "f" * 64
    with pytest.raises(PermissionError):
        gate.decide(release, ai, now=NOW, human=record)


def test_human_target_identity_survives_fresh_observation_but_receipt_does_not(release, ai, gate):
    original=release_id(release)
    receipt=gate.decide(release,ai,now=NOW)
    refreshed=dict(release, observed_at=(NOW+timedelta(seconds=30)).isoformat())
    assert release_id(refreshed)==original
    with pytest.raises(PermissionError):
        verify_receipt(receipt,gate.key.public_key(),refreshed,now=NOW+timedelta(seconds=30))
