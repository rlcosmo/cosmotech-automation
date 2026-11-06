"""Correlate existing CI facts with the one release execution, without authorizing it."""

from __future__ import annotations

import fcntl
import json
import re
from datetime import UTC, datetime
from pathlib import Path

from automation.operational_sync import redact

ROOT = Path("/var/lib/cosmotech-pipeline-events")


def update(
    request: dict,
    *,
    execution_id: str,
    error: str | None = None,
    stage: str = "collect",
) -> None:
    app, run, attempt, sha = (
        request.get(k, "") for k in ["service", "run_id", "run_attempt", "commit"]
    )
    if (
        app not in {"orders-api", "orders-worker"}
        or not re.fullmatch(r"[0-9]{1,20}", str(run))
        or not re.fullmatch(r"[0-9]{1,4}", str(attempt))
        or not re.fullmatch("[a-f0-9]{40}", sha)
    ):
        return
    root = ROOT
    path = root / f"{app}-{run}-{attempt}.json"
    if not path.is_file():
        return
    with (root / ".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        value = json.loads(path.read_text())
        if value["sha"] != sha:
            return
        value["release_execution_id"] = execution_id
        if error:
            value["promotion_error"] = {
                "at": datetime.now(UTC).isoformat(),
                "stage": {
                    "collect": "Verificação da CI e imagem",
                    "policy": "GMUD e política",
                    "dispatch": "Implantação controlada",
                }.get(stage, "Acompanhamento"),
                "problem": "Não foi possível confirmar CI, imagem ou controles obrigatórios. Promoção bloqueada.",
                "technical": redact(error),
            }
        if error and value.get("state") not in {"success", "failure", "cancelled"}:
            for pending in ("gitops", "rollout", "health"):
                current = value.get("stages", {}).get(pending, {})
                if current.get("state") not in {"success", "failure", "cancelled", "skipped"}:
                    now = datetime.now(UTC).isoformat()
                    value.setdefault("stages", {})[pending] = {
                        "state": "failure", "started_at": current.get("started_at", now),
                        "updated_at": now, "release_execution_id": execution_id,
                    }
                    value["state"] = "failure"
                    break
        tmp = path.with_suffix(".promotion.tmp")
        tmp.write_text(json.dumps(value))
        tmp.chmod(0o644)
        tmp.replace(path)


def delivery_event(state: dict, key: str, status: str) -> bool:
    """Project trusted delivery facts into the existing immutable CI attempt.

    No new pipeline or execution is created. An earlier failed attempt remains
    failed. Canary preparation is not final health, and rollback is not success.
    """
    stages = {"gitops": "gitops", "argo": "rollout", "validation": "health"}
    stage = stages.get(key)
    if key == "canary" and status == "FAILED":
        stage = "health"
    if stage is None or status not in {"RUNNING", "SUCCESS", "FAILED"}:
        return False
    release = state["release"]
    app, run, attempt, sha = (release["service"], str(release["run_id"]),
                              str(state["bundle"]["ci_attempt"]), release["commit"])
    if (app not in {"orders-api", "orders-worker"}
            or not re.fullmatch(r"[0-9]{1,20}", run)
            or not re.fullmatch(r"[0-9]{1,4}", attempt)
            or not re.fullmatch(r"[a-f0-9]{40}", sha)):
        raise ValueError("Invalid delivery source identity")
    path = ROOT / f"{app}-{run}-{attempt}.json"
    if not path.is_file():
        return False
    with (ROOT / ".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        value = json.loads(path.read_text())
        if (value.get("sha") != sha
                or value.get("release_execution_id") != release["execution_id"]):
            raise PermissionError("Delivery does not match the recorded CI release")
        if value.get("state") in {"success", "failure", "cancelled"}:
            return False
        current = value.get("stages", {}).get(stage, {})
        if current.get("state") in {"success", "failure", "cancelled", "skipped"}:
            return False
        # A signed publication cannot stand in for missing CI prerequisites.
        required = ["tests", "build_scan", "publish", "gitops", "rollout", "health"]
        if status == "SUCCESS" and any(
            value.get("stages", {}).get(s, {}).get("state") != "success"
            for s in required[:required.index(stage)]
        ):
            raise PermissionError("Delivery prerequisites are not proven")
        now = datetime.now(UTC).isoformat()
        value.setdefault("stages", {})[stage] = {
            "state": {"RUNNING": "running", "SUCCESS": "success", "FAILED": "failure"}[status],
            "started_at": current.get("started_at", now), "updated_at": now,
            "release_execution_id": release["execution_id"],
        }
        if status == "FAILED":
            value["state"] = "failure"
        elif all(value["stages"].get(s, {}).get("state") == "success" for s in required):
            value["state"] = "success"
        temporary = path.with_suffix(".delivery.tmp")
        temporary.write_text(json.dumps(value))
        temporary.chmod(0o644)
        temporary.replace(path)
    return True
