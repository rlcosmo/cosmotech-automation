"""Correlate existing CI facts with the one release execution, without authorizing it."""

from __future__ import annotations

import fcntl
import json
import re
from datetime import UTC, datetime
from pathlib import Path

from automation.operational_sync import redact


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
    root = Path("/var/lib/cosmotech-pipeline-events")
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
        tmp = path.with_suffix(".promotion.tmp")
        tmp.write_text(json.dumps(value))
        tmp.chmod(0o644)
        tmp.replace(path)
