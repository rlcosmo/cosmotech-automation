from datetime import timedelta

import pytest
from test_promotion_gate import NOW

from automation.worker_canary import CanaryController, quality


def observation(release, now, *, replicas=1, **changes):
    value = {"version": release["digest"], "sampled_at": now.isoformat(), "window_seconds": 300,
             "queue_data_age_seconds": 30, "canary_success": 50, "canary_failure": 0,
             "canary_poll_errors":0,"canary_visibility_errors":0,"canary_ack_errors": 0, "canary_retries": 0, "canary_ddb_errors": 0,
             "stable_success": 20, "stable_failure": 0, "p95_seconds": 0.3,
             "dlq_visible": 0, "queue_visible": 0, "oldest_age_seconds": 0,
             "available_canary": replicas, "available_stable": 1, "inflight": 0,
             "draining": False, "stable_version": release["current_digest"], "stable_synced": True}
    return {**value, **changes}


def start(tmp_path, gate, release, ai):
    controller = CanaryController(str(tmp_path / "canary.db"))
    receipt = gate.decide(release, ai, now=NOW)
    state = controller.start(receipt, release, gate.key.public_key(), now=NOW)
    return controller, state


def test_progression_only_changes_worker_after_measured_windows(tmp_path, gate, release, ai):
    controller, state = start(tmp_path, gate, release, ai)
    t = NOW + timedelta(seconds=300)
    state = controller.step(state["id"], observation(release, t), now=t)
    assert state["stage"] == "CANARY_2" and state["effect"]["replicas"] == 2
    t += timedelta(seconds=300)
    state = controller.step(state["id"], observation(release, t, replicas=2), now=t)
    assert state["stage"] == "PROMOTING"
    assert state["effect"]["promote_stable"] == release["digest"]
    t += timedelta(seconds=20)
    sample = observation(release, t, replicas=2, stable_version=release["digest"])
    state = controller.step(state["id"], sample, now=t)
    assert state["stage"] == "DRAIN_PROMOTED"
    sample.update(draining=True)
    state = controller.step(state["id"], sample, now=t)
    assert state["stage"] == "COMPLETE" and state["effect"]["replicas"] == 0


@pytest.mark.parametrize("change", [
    {"canary_poll_errors":1},{"canary_visibility_errors":1},{"canary_failure": 1}, {"canary_ack_errors": 1}, {"canary_ddb_errors": 1},
    {"dlq_visible": 1}, {"p95_seconds": 8}, {"oldest_age_seconds": 200},
    {"queue_data_age_seconds": 181}, {"available_stable": 0}, {"canary_retries": 10},
    {"sampled_at": (NOW - timedelta(minutes=10)).isoformat()}, {"p95_seconds": float("nan")},
])
def test_objective_failures_request_drain_and_keep_stable(tmp_path, gate, release, ai, change):
    controller, state = start(tmp_path, gate, release, ai)
    state = controller.step(state["id"], observation(release, NOW, **change), now=NOW)
    assert state["stage"] == "DRAIN_ROLLBACK"
    assert state["effect"]["mode"] == "drain"
    assert state["effect"]["restore_stable"] == release["current_digest"]
    assert "replicas" not in state["effect"]  # Do not kill in-flight work.


def test_absent_metrics_abort_but_cannot_prove_drain(tmp_path, gate, release, ai):
    controller, state = start(tmp_path, gate, release, ai)
    state = controller.step(state["id"], {}, now=NOW)
    assert state["stage"] == "DRAIN_ROLLBACK"
    state = controller.step(state["id"], {}, now=NOW + timedelta(seconds=301))
    assert state["stage"] == "DRAIN_ROLLBACK" and "replicas" not in state["effect"]


def test_no_samples_wait_then_timeout_and_rollback_survives_restart(tmp_path, gate, release, ai):
    controller, state = start(tmp_path, gate, release, ai)
    sample = observation(release, NOW, canary_success=0)
    state = controller.step(state["id"], sample, now=NOW)
    assert state["stage"] == "CANARY_1"
    t = NOW + timedelta(seconds=1801)
    sample.update(sampled_at=t.isoformat())
    restarted = CanaryController(controller.database)
    state = restarted.step(state["id"], sample, now=t)
    assert state["stage"] == "DRAIN_ROLLBACK"
    sample.update(draining=True, inflight=1)
    state = restarted.step(state["id"], sample, now=t)
    assert state["stage"] == "DRAIN_ROLLBACK"
    sample.update(inflight=0)
    state = restarted.step(state["id"], sample, now=t)
    assert state["stage"] == "ROLLED_BACK" and state["effect"]["replicas"] == 0


def test_overlapping_or_replayed_release_denied(tmp_path, gate, release, ai):
    controller, _state = start(tmp_path, gate, release, ai)
    receipt = gate.decide(release, ai, now=NOW)
    with pytest.raises(PermissionError):
        controller.start(receipt, release, gate.key.public_key(), now=NOW)


def test_api_can_never_enter_canary(tmp_path, gate, release, ai):
    from test_promotion_gate import human
    release["service"], ai["service"], ai["criticality"] = "orders-api", "orders-api", "HIGH"
    receipt = gate.decide(release, ai, now=NOW, human=human(release))
    with pytest.raises(PermissionError):
        CanaryController(str(tmp_path / "state.db")).start(receipt, release, gate.key.public_key(), now=NOW)


def test_missing_field_never_silently_defaults_to_zero(release):
    sample = observation(release, NOW)
    del sample["dlq_visible"]
    assert quality(sample, candidate=release["digest"], now=NOW, replicas=1, dlq_baseline=0)[0] == "ABORT"
