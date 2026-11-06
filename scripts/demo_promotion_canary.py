"""Exercise real policy/state-machine code with simulated evidence, never AWS."""

from __future__ import annotations

import json
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

from automation.promotion_gate import PromotionGate
from automation.worker_canary import CanaryController
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


def main() -> None:
    now = datetime.now(UTC)
    release = {"service": "orders-worker", "environment": "dev", "commit": "a" * 40,
               "digest": "sha256:" + "2" * 64, "current_digest": "sha256:" + "1" * 64,
               "manifest_sha256": "3" * 64, "run_id": "1", "execution_id": "isolated-demo",
               "observed_at": now.isoformat(), "enforcement_verified": True,
               "checks": dict.fromkeys(["tests_passed", "scan_passed", "digest_verified",
                   "sqs_compatible", "argo_healthy", "incidents_clear", "observability_reliable",
                   "dependencies_reviewed"], True)}
    release["checks"]["sensitive_changes"] = False
    advice = {"service": "orders-worker", "environment": "dev", "criticality": "MEDIUM",
              "risk_level": "LOW", "recommendation": "APPROVE", "justification": "Isolated mock advice",
              "evidence": ["tests_passed"], "model_version": "MOCK-NO-LLM", "timestamp": now.isoformat()}
    with tempfile.TemporaryDirectory() as directory:
        gate = PromotionGate(str(Path(directory) / "gate.db"), Ed25519PrivateKey.generate(), {"demo-owner"})
        receipt = gate.decide(release, advice, now=now)
        controller = CanaryController(str(Path(directory) / "canary.db"))
        state = controller.start(receipt, release, gate.key.public_key(), now=now)
        events = [{"event": "worker_auto_approved", "actor": "policy-engine"},
                  {"stage": state["stage"], "effect": state["effect"]}]
        now += timedelta(seconds=300)
        sample = {"version": release["digest"], "sampled_at": now.isoformat(), "window_seconds": 300,
                  "queue_data_age_seconds": 10, "canary_success": 60, "canary_failure": 0,
                  "canary_ack_errors": 0, "canary_retries": 0, "canary_ddb_errors": 0,
                  "stable_success": 20, "stable_failure": 0, "p95_seconds": 0.2,
                  "dlq_visible": 0, "queue_visible": 0, "oldest_age_seconds": 0,
                  "available_canary": 1, "available_stable": 1, "inflight": 0, "draining": False,
                  "stable_version": release["current_digest"], "stable_synced": True}
        state = controller.step(state["id"], sample, now=now)
        events.append({"stage": state["stage"], "effect": state["effect"]})
        sample.update(available_canary=2, canary_failure=1, inflight=1)
        state = controller.step(state["id"], sample, now=now)
        events.append({"stage": state["stage"], "effect": state["effect"], "reason": state["reason"]})
        sample.update(draining=True)
        state = controller.step(state["id"], sample, now=now)
        events.append({"stage": state["stage"], "inflight": sample["inflight"]})
        sample.update(inflight=0)
        state = controller.step(state["id"], sample, now=now)
        events.append({"stage": state["stage"], "effect": state["effect"]})
        assert state["stage"] == "ROLLED_BACK"
    print(json.dumps({"mode": "ISOLATED_SIMULATION", "aws_access": False, "llm_invoked": False,
                      "deploy_executed": False, "events": events}, indent=2))


if __name__ == "__main__":
    main()
