"""Durable, read-only operational projection; never authorizes a deployment."""

from __future__ import annotations

from datetime import UTC, datetime

NAMES = [
    ("ci", "CI — build, testes e Trivy"),
    ("gmud", "GMUD no GLPI"),
    ("policy", "Política e controles"),
    ("approval", "Aprovação"),
    ("gitops", "Publicação GitOps"),
    ("argo", "Argo CD e saúde"),
    ("canary", "Canary Worker"),
    ("validation", "Validação técnica"),
    ("observation", "Observação de12 horas"),
    ("closure", "Encerramento da GMUD"),
]
STATUS = {
    "PENDING": "Pendente",
    "RUNNING": "Em andamento",
    "SUCCESS": "Concluída",
    "FAILED": "Falha",
    "BLOCKED": "Bloqueada",
    "HUMAN": "Aguardando interação humana",
    "NA": "Não se aplica",
}
REASONS = {
    "missing_metrics": (
        "Canary",
        "Métricas obrigatórias não foram obtidas.",
        "Promoção interrompida; candidata em drenagem.",
        "Verificar coleta e saúde dos pods.",
    ),
    "latency_or_backlog_age": (
        "Canary",
        "Latência ou idade da fila excedeu o limite.",
        "Promoção interrompida e rollback solicitado pelo controlador.",
        "Verificar métricas SQS, processamento e permissões de drenagem.",
    ),
    "processing_persistence_or_ack_failure": (
        "Canary",
        "Falha de processamento, persistência ou controle SQS.",
        "Promoção bloqueada.",
        "Verificar erros do Worker e permissões da fila.",
    ),
    "worker_unavailable": (
        "Canary",
        "Um consumidor obrigatório não está disponível.",
        "Promoção interrompida.",
        "Verificar readiness e estado dos pods.",
    ),
    "drain_not_proven_manual_intervention_required": (
        "Rollback",
        "Drenagem não comprovada no prazo.",
        "Consumidores permanecem em drenagem; sem encerramento automático.",
        "Revisar inflight e lease antes de qualquer intervenção.",
    ),
}


def stage_event(
    state: dict, key: str, status: str, *, result: str = "", evidence: str | None = None
) -> None:
    """Called only by the trusted runner around real operations."""
    now = datetime.now(UTC).isoformat()
    stages = state.setdefault("operational_stages", {})
    old = stages.get(key, {})
    started = (
        now
        if status == "RUNNING" and old.get("status") != "RUNNING"
        else old.get("started_at")
    )
    state.setdefault("operational_stage_events", []).append(
        {
            "key": key,
            "status": status,
            "at": now,
            "result": result,
            "evidence": evidence,
        }
    )
    stages[key] = {
        "status": status,
        "started_at": started,
        "ended_at": None if status in {"RUNNING", "HUMAN", "PENDING"} else now,
        "result": result,
        "evidence": evidence,
    }


def project(change: dict, state: dict | None = None) -> dict:
    state = state or {}
    release = change["release"]
    phase = change["phase"]
    events = change.get("events") or []
    by_type = {e["type"]: e for e in events}
    stages = {
        key: {
            "key": key,
            "name": name,
            "status": "PENDING",
            "started_at": None,
            "ended_at": None,
            "result": "",
            "evidence": None,
        }
        for key, name in NAMES
    }

    def assign(key, status, event=None, result="", end=None):
        stages[key].update(status=status, result=result)
        if event:
            stages[key].update(started_at=event.get("at"), ended_at=end)

    assign(
        "ci",
        "SUCCESS",
        result="Testes, Trivy e digest verificados pelo coletor."
        if release["checks"]["tests_passed"] and release["checks"]["scan_passed"]
        else "Verificações não aprovadas.",
    )
    if not release["checks"]["tests_passed"] or not release["checks"]["scan_passed"]:
        stages["ci"]["status"] = "FAILED"
    ci = state.get("bundle", {}).get("ci_evidence", {})
    stages["ci"].update(
        started_at=ci.get("started_at"),
        ended_at=ci.get("ended_at"),
        evidence=(change.get("ci_links") or [None])[0],
    )
    assign(
        "gmud",
        "SUCCESS",
        by_type.get("created"),
        result="Registro oficial criado no GLPI.",
    )
    assign(
        "policy",
        "SUCCESS" if phase != "CREATED" else "RUNNING",
        by_type.get("human_review") or by_type.get("approved"),
        result="Criticidade protegida; controles obrigatórios preservados.",
    )
    approval = by_type.get("approved")
    assign(
        "approval",
        "SUCCESS"
        if approval
        else "HUMAN"
        if phase == "HUMAN_REVIEW"
        else "FAILED"
        if phase == "REJECTED"
        else "PENDING",
        approval or by_type.get("human_review"),
        result="Decisão registrada na trilha da GMUD." if approval else "",
    )
    delivery = state.get("delivery", {})
    if delivery.get("revision"):
        assign(
            "gitops",
            "SUCCESS",
            by_type.get("deploying"),
            result="Publicação assinada: " + delivery["revision"][:12],
        )
    if "observing" in by_type:
        for key in ["gitops", "argo", "validation"]:
            assign(
                key,
                "SUCCESS",
                by_type["observing"],
                result="Validado na implantação; acompanhamento continua na observação.",
            )
    elif phase == "DEPLOYING":
        assign("argo", "RUNNING", by_type.get("deploying"))
    stage = delivery.get("canary_stage")
    is_canary = (
        release["service"] == "orders-worker" and delivery.get("bootstrap") is False
    )
    assign(
        "canary",
        "SUCCESS"
        if stage == "COMPLETE"
        else "FAILED"
        if stage in {"ROLLED_BACK", "DRAIN_ROLLBACK"}
        else "RUNNING"
        if is_canary
        else "PENDING" if release["service"]=="orders-worker" and delivery.get("bootstrap") is not True else "NA",
        by_type.get("deploying"),
        result={
            "CANARY_1": "Um consumidor",
            "CANARY_2": "Dois consumidores",
            "PROMOTING": "Promoção da candidata",
            "DRAIN_PROMOTED": "Drenagem final",
            "COMPLETE": "Progressão e drenagem comprovadas",
            "ROLLED_BACK": "Revertida",
        }.get(stage, ""),
    )
    if "observing" in by_type:
        assign(
            "observation",
            "SUCCESS"
            if phase == "CLOSED_SUCCESS"
            else "FAILED"
            if phase
            in {"FAILED", "ROLLED_BACK", "ROLLBACK_RUNNING", "ROLLBACK_REQUESTED"}
            else "RUNNING",
            by_type["observing"],
            result="Prazo: " + str(change.get("rollback_until")),
            end=by_type.get("closed_success", {}).get("at"),
        )
    if phase == "CLOSED_SUCCESS":
        assign(
            "closure",
            "SUCCESS",
            by_type.get("closed_success"),
            result="Sucesso após a janela real de12 horas.",
        )
    elif phase == "ROLLED_BACK":
        assign(
            "closure",
            "FAILED",
            by_type.get("rolled_back"),
            result="Mudança revertida; não conta como sucesso.",
        )
    for key, saved in state.get("operational_stages", {}).items():
        if key in stages:
            stages[key].update(saved)
    if approval:
        stages["approval"].update(
            started_at=by_type.get("human_review", {}).get("at")
            or state.get("operational_stages", {}).get("policy", {}).get("ended_at"),
            ended_at=approval["at"],
        )
    failure = None
    reason = state.get("canary_reason")
    for e in reversed(events):
        if e["type"] in {"failed", "rolled_back", "rollback_running"} and e.get(
            "details", {}
        ).get("reason"):
            reason = e["details"]["reason"]
            break
    if phase in {"FAILED", "ROLLED_BACK", "ROLLBACK_RUNNING"} or state.get(
        "reconciliation_error"
    ):
        active = max(
            state.get("operational_stages", {}).items(),
            key=lambda x: x[1].get("ended_at") or x[1].get("started_at") or "",
            default=(None, {}),
        )[0]
        default = {
            "argo": (
                "Argo CD",
                "A aplicação não atingiu a revisão e a saúde esperadas.",
                "Implantação ou encerramento bloqueado.",
                "Verificar sincronização, saúde dos pods e revisão GitOps.",
            ),
            "gitops": (
                "GitOps",
                "A publicação assinada não foi confirmada.",
                "Promoção bloqueada.",
                "Verificar revisão, assinatura e evidências do publisher.",
            ),
            "validation": (
                "Validação",
                "Digest, disponibilidade ou métricas não conferem.",
                "Implantação não validada.",
                "Verificar versão executada e observabilidade.",
            ),
        }
        step, problem, impact, action = REASONS.get(
            reason,
            default.get(
                active,
                (
                    "Acompanhamento",
                    "A execução exige reconciliação com as evidências do laboratório.",
                    "A promoção ou o encerramento permanece bloqueado.",
                    "Consultar detalhes da etapa e logs técnicos.",
                ),
            ),
        )
        failure = {
            "stage": step,
            "problem": problem,
            "impact": impact,
            "action": action,
        }
    return {
        "stages": list(stages.values()),
        "failure": failure,
        "deployment_strategy": "CANARY" if is_canary else "ROLLING",
        "execution_snapshot": state.get("execution_snapshot"),
        "execution_logs": state.get("execution_logs"),
        "canary_stage": stage,
        "gitops_revision": delivery.get("revision"),
        "image_tag": state.get("bundle", {}).get("image_tag"),
        "metrics_snapshot": state.get("last_canary_quality_sample"),
        "stage_history": state.get("operational_stage_events"),
        "technical_error": state.get("reconciliation_error", {}).get("message"),
    }
