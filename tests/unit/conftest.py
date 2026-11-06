from datetime import UTC, datetime

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from automation.promotion_gate import PromotionGate

NOW = datetime(2026, 11, 5, 20, tzinfo=UTC)

@pytest.fixture
def release():
    return {"service": "orders-worker", "environment": "dev", "commit": "a" * 40,
            "digest": "sha256:" + "b" * 64, "current_digest": "sha256:" + "c" * 64,
            "manifest_sha256": "d" * 64, "run_id": "123", "execution_id": "kestra-test",
            "observed_at": NOW.isoformat(), "enforcement_verified": True,
            "checks": {"tests_passed": True, "scan_passed": True, "digest_verified": True,
                       "sqs_compatible": True, "argo_healthy": True, "incidents_clear": True,
                       "observability_reliable": True, "dependencies_reviewed": True,
                       "sensitive_changes": False}}


@pytest.fixture
def ai():
    return {"service": "orders-worker", "environment": "dev", "criticality": "MEDIUM",
            "risk_level": "LOW", "recommendation": "APPROVE", "justification": "All checks passed",
            "evidence": ["tests_passed", "scan_passed"], "model_version": "mock-v1",
            "timestamp": NOW.isoformat()}


@pytest.fixture
def gate(tmp_path):
    return PromotionGate(str(tmp_path / "gate.db"), Ed25519PrivateKey.generate(), {"authorized-owner"}, ai_enabled=True)


@pytest.fixture
def journal(tmp_path):
    from test_gmud import FakeGLPI, policy

    from automation.gmud import ChangeJournal

    return ChangeJournal(str(tmp_path / "gmud.db"), FakeGLPI(), b"test-only-key-" * 4, policy())
