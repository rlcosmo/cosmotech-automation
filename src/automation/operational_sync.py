"""Read-only execution/log projection and native correlation labels; no flow execution."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path


def redact(message: str) -> str:
    value = str(message)[:3000]
    if re.search(
        r"(?i)(password|authorization|client_secret|private.key|access.key|secret_access|csrf)",
        value,
    ):
        return "[Conteúdo sensível omitido]"
    value = re.sub(r"\b[0-9]{12}\b", "[conta omitida]", value)
    return re.sub(r"\b10\.[0-9]+\.[0-9]+\.[0-9]+\b", "[endereço omitido]", value)


def correlate(kestra, execution: dict, labels: dict) -> None:
    if execution["state"]["current"] not in {
        "SUCCESS",
        "FAILED",
        "KILLED",
        "CANCELLED",
        "WARNING",
    }:
        return
    merged = {
        v["key"]: v["value"]
        for v in execution.get("labels", [])
        if not v["key"].startswith("system.")
    }
    if all(merged.get(k) == v for k, v in labels.items()):
        return
    merged.update(labels)
    kestra.request(
        "/api/v1/main/executions/" + execution["id"] + "/actions/labels",
        method="POST",
        body=json.dumps([{"key": k, "value": v} for k, v in merged.items()]).encode(),
    )


def refresh(kestra, state: dict, change: dict, *, force: bool = False) -> None:
    now = datetime.now(UTC)
    stamp = state.get("operational_snapshot_at")
    if (
        not force
        and state.get("operational_snapshot_phase") == change["phase"]
        and stamp
        and (now - datetime.fromisoformat(stamp)).total_seconds() < 60
    ):
        return
    execution = kestra.request(
        "/api/v1/main/executions/" + change["kestra_execution_id"]
    )
    state["execution_snapshot"] = {
        k: execution.get(k)
        for k in ["id", "namespace", "flowId", "flowRevision", "state", "labels"]
    }
    state["execution_snapshot"]["tasks"] = [
        {"stage": t["taskId"], "state": t["state"]}
        for t in execution.get("taskRunList", [])
    ]
    logs = kestra.request("/api/v1/main/logs/" + execution["id"] + "?minLevel=INFO")
    rows = logs if isinstance(logs, list) else logs.get("results", [])
    state["execution_logs"] = [
        {
            "stage": l.get("taskId"),
            "level": l.get("level"),
            "at": l.get("timestamp"),
            "message": redact(l.get("message", "")),
        }
        for l in rows[-60:]
    ]
    labels = {
        "execution_type": "release",
        "gmud_id": change.get("rcl_id", str(change["change_id"])),
        "application": change["release"]["service"],
        "version": change["application_version"],
        "release_status": change["phase"],
    }
    correlate(kestra, execution, labels)
    release = change["release"]
    attempt = state.get("bundle", {}).get("ci_attempt", "1")
    path = Path("/var/lib/cosmotech-pipeline-events") / (
        release["service"] + "-" + release["run_id"] + "-" + str(attempt) + ".json"
    )
    if path.exists():
        child = json.loads(path.read_text()).get("execution_id")
        if child and re.fullmatch(r"[A-Za-z0-9_-]{1,100}", child):
            ci = kestra.request("/api/v1/main/executions/" + child)
            if (
                ci.get("inputs", {}).get("commit_sha") == release["commit"]
                and ci.get("namespace") == "cosmotech.clusters.aws_eks_istio_lab_dev"
            ):
                correlate(
                    kestra,
                    ci,
                    {
                        **labels,
                        "execution_type": "ci",
                        "release_execution_id": execution["id"],
                    },
                )
                state["execution_snapshot"]["ci_execution_id"] = child
    state.update(
        operational_snapshot_at=now.isoformat(),
        operational_snapshot_phase=change["phase"],
    )
