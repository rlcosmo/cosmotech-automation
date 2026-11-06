"""Durable Worker canary state machine; callers own authenticated GitOps execution.

Effects are returned as data, never executed implicitly. Runtime adapters must
acknowledge an effect before reporting the expected deployment state as ready.
"""

from __future__ import annotations

import json
import math
import sqlite3
from datetime import datetime
from typing import Any

from automation.promotion_gate import instant, release_id, verify_receipt

REQUIRED = {"version", "sampled_at", "window_seconds", "queue_data_age_seconds",
            "canary_success", "canary_failure", "canary_ack_errors", "canary_retries",
            "canary_ddb_errors", "stable_success", "stable_failure", "p95_seconds",
            "dlq_visible", "queue_visible", "oldest_age_seconds", "available_canary",
            "available_stable", "inflight", "draining", "stable_version", "stable_synced"}


def quality(observation: dict, *, candidate: str, now: datetime, replicas: int,
            dlq_baseline: int) -> tuple[str, str]:
    """Conservative defaults for this lab; empty data never means healthy."""
    if type(observation) is not dict or set(observation) != REQUIRED:
        return "ABORT", "missing_metrics"
    try:
        age = (now - instant(observation["sampled_at"])).total_seconds()
        if not 0 <= age <= 90 or observation["version"] != candidate:
            return "ABORT", "stale_or_wrong_version_metrics"
        missing_latency=observation["p95_seconds"] is None and observation["canary_success"]==0
        for field in REQUIRED - {"version", "sampled_at", "stable_version", "draining", "stable_synced"}:
            if field=="p95_seconds" and missing_latency:continue
            value = observation[field]
            if type(value) not in {float, int} or not math.isfinite(value) or value < 0:
                return "ABORT", "invalid_metrics"
        if type(observation["draining"]) is not bool or type(observation["stable_synced"]) is not bool:
            return "ABORT", "invalid_state"
        if observation["queue_data_age_seconds"] > 180:
            return "ABORT", "stale_queue_metrics"
        if (observation["available_stable"] < 1 or observation["available_canary"] != replicas
                or observation["stable_synced"] is not True):
            return "ABORT", "worker_unavailable"
        if any(observation[key] > 0 for key in ["canary_failure", "canary_ack_errors", "canary_ddb_errors", "stable_failure"]):
            return "ABORT", "processing_persistence_or_ack_failure"
        if observation["dlq_visible"] > dlq_baseline:
            return "ABORT", "dlq_growth"
        if observation["oldest_age_seconds"] > 120 or (not missing_latency and observation["p95_seconds"] > 5):
            return "ABORT", "latency_or_backlog_age"
        total = observation["canary_success"] + observation["canary_failure"] + observation["canary_ack_errors"]
        if total and observation["canary_retries"] / total > 0.10:
            return "ABORT", "retry_rate"
        if observation["window_seconds"] < 300 or total < 50 or observation["stable_success"] < 10:
            return "WAIT", "insufficient_business_samples"
        return "PASS", "bounded_observation_passed"
    except (KeyError, ValueError, TypeError):
        return "ABORT", "invalid_observation"


class CanaryController:
    """Persist stage changes and the required effect atomically; restart-safe."""

    def __init__(self, database: str):
        self.database = database
        with sqlite3.connect(database) as db:
            db.execute("CREATE TABLE IF NOT EXISTS rollouts (id TEXT PRIMARY KEY, state TEXT NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS rollout_events (id TEXT, event TEXT NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS rollout_observations (id TEXT, generation INTEGER, observed_at TEXT, observation TEXT NOT NULL, PRIMARY KEY(id,generation))")

    def start(self, receipt: dict, release: dict, public_key: Any, *, now: datetime,
              dlq_baseline: int = 0, gmud_id: str | None = None) -> dict:
        verify_receipt(receipt, public_key, release, now=now)
        if release["service"] != "orders-worker" or type(dlq_baseline) is not int or dlq_baseline != 0:
            raise PermissionError("Worker only; clean queue/DLQ baseline required")
        state = {"id": release_id(release), "gmud_id": gmud_id, "stage": "CANARY_1", "generation": 1,
                 "stable": release["current_digest"], "candidate": release["digest"],
                 "entered_at": now.isoformat(), "dlq_baseline": dlq_baseline,
                 "reason": "authorized_initial_canary",
                 "effect": {"mode": "run", "replicas": 1, "expires_at": now.timestamp() + 180}}
        try:
            with sqlite3.connect(self.database) as db:
                db.execute("BEGIN IMMEDIATE")
                active = [json.loads(row[0]) for row in db.execute("SELECT state FROM rollouts")]
                if any(row["stage"] not in {"COMPLETE", "ROLLED_BACK"} for row in active):
                    raise PermissionError("An existing Worker rollout owns progression")
                db.execute("INSERT INTO rollouts VALUES (?,?)", (state["id"], json.dumps(state)))
                db.execute("INSERT INTO rollout_events VALUES (?,?)", (state["id"], json.dumps(state)))
        except sqlite3.IntegrityError as error:
            raise PermissionError("Release rollout already exists; no replay") from error
        return state

    def step(self, identity: str, observation: dict, *, now: datetime) -> dict:
        with sqlite3.connect(self.database) as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT state FROM rollouts WHERE id=?", (identity,)).fetchone()
            if row is None:
                raise ValueError("Unknown authorized rollout")
            state = json.loads(row[0])
            stage = state["stage"]
            if stage in {"COMPLETE", "ROLLED_BACK"}:
                return state
            db.execute("INSERT OR IGNORE INTO rollout_observations VALUES (?,?,?,?)",
                       (identity,state["generation"],now.isoformat(),json.dumps(observation)))
            elapsed = (now - instant(state["entered_at"])).total_seconds()
            if stage in {"DRAIN_ROLLBACK", "DRAIN_PROMOTED"}:
                # No scaling to zero until fresh pod evidence proves drain.
                try:
                    drained = (0 <= (now - instant(observation["sampled_at"])).total_seconds() <= 90
                               and observation["version"] == state["candidate"]
                               and observation["draining"] is True
                               and type(observation["inflight"]) in {int, float}
                               and observation["inflight"] == 0)
                    stable_ok = (observation["available_stable"] >= 1 and observation["stable_synced"] is True
                                 and observation["stable_version"] == (
                                     state["stable"] if stage == "DRAIN_ROLLBACK" else state["candidate"]))
                except (KeyError, ValueError, TypeError):
                    drained = stable_ok = False
                if drained and stable_ok:
                    state["stage"] = "ROLLED_BACK" if stage == "DRAIN_ROLLBACK" else "COMPLETE"
                    state["effect"] = {"mode": "drain", "replicas": 0, "notify": True}
                elif elapsed > 300:
                    state["reason"] = "drain_not_proven_manual_intervention_required"
                    state["effect"] = {"mode": "drain", "notify": True}
            elif stage == "PROMOTING":
                status, reason = quality(observation, candidate=state["candidate"], now=now,
                                         replicas=2, dlq_baseline=state["dlq_baseline"])
                try:
                    promoted = (0 <= (now - instant(observation["sampled_at"])).total_seconds() <= 90
                                and observation["stable_version"] == state["candidate"]
                                and observation["stable_synced"] is True and observation["available_stable"] >= 1)
                except (KeyError, ValueError, TypeError):
                    promoted = False
                if status == "ABORT":
                    state.update(stage="DRAIN_ROLLBACK", entered_at=now.isoformat(),
                                 reason=reason, effect={"mode": "drain",
                                     "restore_stable": state["stable"], "notify": True})
                elif promoted:
                    state.update(stage="DRAIN_PROMOTED", entered_at=now.isoformat(),
                                 effect={"mode": "drain", "replicas": 2})
                elif elapsed > 600:
                    state.update(stage="DRAIN_ROLLBACK", entered_at=now.isoformat(),
                                 reason="promotion_not_confirmed", effect={"mode": "drain",
                                     "restore_stable": state["stable"], "notify": True})
            else:
                replicas = 1 if stage == "CANARY_1" else 2
                status, reason = quality(observation, candidate=state["candidate"], now=now,
                                         replicas=replicas, dlq_baseline=state["dlq_baseline"])
                if status != "ABORT" and observation["stable_version"] != state["stable"]:
                    status, reason = "ABORT", "stable_version_drift"
                if status == "PASS" and observation["window_seconds"] > elapsed + 1:
                    status, reason = "WAIT", "window_contains_prior_stage"
                if status == "ABORT" or elapsed > 1800:
                    state.update(stage="DRAIN_ROLLBACK", entered_at=now.isoformat(),
                                 reason=reason if status == "ABORT" else "observation_timeout",
                                 effect={"mode": "drain", "restore_stable": state["stable"], "notify": True})
                elif status == "PASS" and elapsed >= 300:
                    if stage == "CANARY_1":
                        state.update(stage="CANARY_2", entered_at=now.isoformat(),
                                     reason=reason, effect={"mode": "run", "replicas": 2})
                    else:
                        state.update(stage="PROMOTING", entered_at=now.isoformat(), reason=reason,
                                     effect={"mode": "run", "replicas": 2, "promote_stable": state["candidate"]})
                else:
                    state["reason"] = reason
            state["generation"] += 1
            if state["effect"].get("mode") == "run":
                # Only a healthy/reviewed progression step can renew consumption.
                state["effect"]["expires_at"] = now.timestamp() + 180
            db.execute("UPDATE rollouts SET state=? WHERE id=?", (json.dumps(state), identity))
            db.execute("INSERT INTO rollout_events VALUES (?,?)", (identity, json.dumps(state)))
            return state
