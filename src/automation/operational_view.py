"""Operational presentation from persisted facts, including unavailable upstreams."""

from __future__ import annotations

import html
import json
from datetime import datetime

from automation.release_projection import STATUS


def esc(value) -> str:
    return html.escape(
        str(value if value is not None else "Não registrado"), quote=True
    )


def approval_banner(context: dict) -> str:
    approved = next(
        (e for e in reversed(context.get("events") or []) if e["type"] == "approved"),
        None,
    )
    human = context.get("human_decision")
    if approved:
        principal = approved.get("details", {}).get("principal")
        message = (
            "GMUD já foi aprovada automaticamente pela política."
            if principal == "policy-engine"
            else "GMUD já foi aprovada por "
            + str(principal or "responsável autenticado")
            + "."
        )
        at = approved["at"]
    elif human and human.get("result") == "APPROVE":
        message = (
            "GMUD já foi aprovada por "
            + human["principal"]
            + ". Aguardando validação do Kestra."
        )
        at = human["timestamp"]
    elif human and human.get("result") == "REJECT":
        message = "GMUD rejeitada por " + human["principal"] + "."
        at = human["timestamp"]
    else:
        return ""
    return (
        '<aside class="approval-banner" role="status"><strong>'
        + esc(message)
        + "</strong><br><small>"
        + esc(at)
        + "</small></aside>"
    )


def timestamp(value) -> str:
    if not value:
        return "Não registrado"
    try:
        return datetime.fromisoformat(value).strftime("%d/%m/%Y %H:%M:%S UTC")
    except (ValueError, TypeError):
        return "Não registrado"


def duration(stage: dict, now: datetime) -> str:
    if not stage.get("started_at"):
        return "Não registrada"
    end = stage.get("ended_at")
    if not end and stage["status"] not in {"RUNNING", "HUMAN"}:
        return "Não registrada"
    try:
        start = datetime.fromisoformat(stage["started_at"])
        stop = datetime.fromisoformat(end) if end else now
        seconds = max(0, int((stop - start).total_seconds()))
        return str(seconds // 60) + "min " + str(seconds % 60) + "s"
    except (ValueError, TypeError):
        return "Não registrada"


def sync_banner(context: dict) -> str:
    sync = context.get("glpi_sync") or {}
    if sync.get("status") != "UNAVAILABLE":
        return ""
    return (
        '<aside class="notice" role="alert"><strong>GLPI indisponível — histórico persistido</strong><p>Exibindo o último estado confirmado. Novas promoções e ações desta página estão bloqueadas até a reconciliação.</p><small>Última confirmação: '
        + esc(timestamp(sync.get("last_success_at")))
        + "</small></aside>"
    )


def operational_panel(release: dict, context: dict, now: datetime) -> str:
    out = sync_banner(context)
    failure = context.get("failure")
    if failure:
        out += (
            '<section class="card wide failure" role="alert"><h2>FALHA — '
            + esc(failure["stage"])
            + "</h2>"
        )
        for name, key in [
            ("Problema", "problem"),
            ("Impacto", "impact"),
            ("Ação recomendada", "action"),
        ]:
            out += "<p><strong>" + name + ":</strong> " + esc(failure[key]) + "</p>"
        out += (
            '<a href="/releases/'
            + __import__(
                "automation.promotion_gate", fromlist=["release_id"]
            ).release_id(release)
            + '/logs">Consultar logs técnicos</a></section>'
        )
    stages = context.get("stages") or []
    if stages:
        out += '<section class="card wide" id="etapas"><h2>Etapas da release</h2><div class="stage-list">'
        for stage in stages:
            status = stage["status"]
            out += (
                '<article class="stage '
                + esc(status.lower())
                + '"><h3>'
                + esc(stage["name"])
                + "</h3><strong>"
                + esc(STATUS.get(status, "Estado não confirmado"))
                + "</strong><p>"
                + esc(stage.get("result") or "Aguardando evidência da etapa.")
                + "</p><details><summary>Horários e evidências</summary><dl><dt>Início</dt><dd>"
                + esc(timestamp(stage.get("started_at")))
                + "</dd><dt>Término</dt><dd>"
                + esc(timestamp(stage.get("ended_at")))
                + "</dd><dt>Duração</dt><dd>"
                + esc(duration(stage, now))
                + "</dd></dl>"
            )
            if stage.get("evidence"):
                # Evidence URLs are rendered only as text; fixed origin links exist elsewhere.
                out += "<p>" + esc(stage["evidence"]) + "</p>"
            out += "</details></article>"
        out += "</div></section>"
    sample = context.get("metrics_snapshot") or {}
    if sample:
        out += (
            '<section class="card wide"><h2>Observabilidade Canary — amostra registrada</h2><p>'
            + esc(timestamp(sample.get("sampled_at")))
            + "</p>"
        )
        for name, key in [
            ("Janela (s)", "window_seconds"),
            ("Sucessos candidata (increase)", "canary_success"),
            ("Sucessos stable (increase)", "stable_success"),
            ("p95 (s)", "p95_seconds"),
            ("Falhas", "canary_failure"),
            ("Falhas SQS", "canary_visibility_errors"),
            ("Idade da fila (s)", "oldest_age_seconds"),
            ("DLQ", "dlq_visible"),
        ]:
            out += (
                '<span class="badge">' + name + ": " + esc(sample.get(key)) + "</span>"
            )
        out += "</section>"
    technical = {
        key: context.get(key)
        for key in [
            "execution_snapshot",
            "execution_logs",
            "stage_history",
            "technical_error",
            "gitops_revision",
            "image_tag",
            "glpi_sync",
        ]
        if context.get(key) is not None
    }
    if technical:
        out += (
            '<section class="card wide"><h2>Detalhes técnicos</h2><details><summary>Expandir execução, logs e evidências</summary><pre>'
            + esc(json.dumps(technical, ensure_ascii=False, indent=2))
            + "</pre></details></section>"
        )
    return out


def filter_form(filters: dict) -> str:
    out = (
        '<form method="get" class="card filters"><label>RCL ou aplicação <input name="q" maxlength="100" value="'
        + esc(filters.get("q", ""))
        + '" placeholder="RCL000006 ou Orders Worker"></label>'
    )
    for key, label, choices in [
        (
            "application",
            "Aplicação",
            [
                ("", "Todas"),
                ("orders-api", "Orders API"),
                ("orders-worker", "Orders Worker"),
            ],
        ),
        ("environment", "Ambiente", [("", "Todos"), ("dev", "DEV")]),
        (
            "status",
            "Status",
            [
                ("", "Todos"),
                ("HUMAN_REVIEW", "Aguardando aprovação"),
                ("DEPLOYING", "Em implantação"),
                ("OBSERVING", "Em observação"),
                ("CLOSED_SUCCESS", "Encerrada"),
                ("ROLLED_BACK", "Revertida"),
                ("FAILED", "Falha"),
            ],
        ),
    ]:
        out += "<label>" + label + '<select name="' + key + '">'
        for value, title in choices:
            out += (
                '<option value="'
                + value
                + '"'
                + (" selected" if filters.get(key) == value else "")
                + ">"
                + title
                + "</option>"
            )
        out += "</select></label>"
    return (
        out + '<button>Filtrar releases</button><a href="/releases/">Limpar</a></form>'
    )


def filter_entries(entries: list[dict], filters: dict) -> list[dict]:
    def keep(e):
        release, context = e["release"], e["context"]
        text = (
            " ".join(
                str(x)
                for x in [
                    context.get("rcl_id", ""),
                    release["service"],
                    context.get("application_version", ""),
                ]
            )
            .lower()
            .replace("-", " ")
        )
        return filters.get("q", "").lower().replace("-", " ") in text and all(
            not filters.get(k) or filters[k] == value
            for k, value in [
                ("application", release["service"]),
                ("environment", release["environment"]),
                ("status", context.get("phase")),
            ]
        )

    return [e for e in entries if keep(e)]
