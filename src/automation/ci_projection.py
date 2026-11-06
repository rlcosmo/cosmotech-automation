"""Read existing real CI callbacks; no release number or permission is invented."""

from __future__ import annotations

import json
import re
from pathlib import Path


def candidates(directory: str | None, known: list[dict]) -> list[dict]:
    if not directory:
        return []
    existing = {
        (e["release"]["service"], str(e["release"]["run_id"]), e["release"]["commit"])
        for e in known
    }
    result = []
    try:
        paths = sorted(
            Path(directory).glob("orders-*.json"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )[:100]
    except OSError:
        return []
    for path in paths:
        if not re.fullmatch(
            r"orders-(api|worker)-[0-9]{1,20}-[0-9]{1,4}\.json", path.name
        ):
            continue
        try:
            value = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        key = (value.get("app"), str(value.get("run_id")), value.get("sha"))
        if key in existing or not re.fullmatch("[a-f0-9]{40}", value.get("sha", "")):
            continue
        if sum(c["application"] == value["app"] for c in result) >= 2:
            continue
        stages = [
            {
                "name": name,
                "state": value.get("stages", {}).get(name, {}).get("state", "pending"),
                "started_at": value.get("stages", {}).get(name, {}).get("started_at"),
                "updated_at": value.get("stages", {}).get(name, {}).get("updated_at"),
            }
            for name in ["tests", "build_scan", "publish"]
        ]
        result.append(
            {
                "application": value["app"],
                "commit": value["sha"],
                "run_id": str(value["run_id"]),
                "attempt": str(value["attempt"]),
                "promotion_error": value.get("promotion_error"),
                "release_execution_id": value.get("release_execution_id"),
                "stages": stages,
                "execution_id": value.get("execution_id"),
            }
        )
    return result
