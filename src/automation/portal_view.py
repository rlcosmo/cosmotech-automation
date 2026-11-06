"""Escaped Brazilian Portuguese interface; no decision or deployment authority."""
from __future__ import annotations

import base64
import hashlib
import html
import re
from datetime import datetime

from automation.operational_view import (
    approval_banner,
    filter_form,
    operational_panel,
    sync_banner,
)
from automation.promotion_gate import instant

CSS='''*{box-sizing:border-box}body{margin:0;background:#081525;color:#eef5ff;font:16px/1.6 system-ui,sans-serif}main{max-width:1180px;margin:auto;padding:28px 22px 60px}header{border-top:5px solid #ff9900;background:linear-gradient(125deg,#142b43,#102033);padding:32px;border-radius:16px;margin-bottom:24px}.approval-banner{padding:16px;border:1px solid #368778;background:#133b39;border-radius:10px;margin:15px 0}.notice,.failure{border:2px solid #ff9900;padding:20px;margin:15px 0}.stage-list{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:14px}.stage{border-left:4px solid #6289a4;padding:15px;background:#081525;border-radius:8px}.stage h3{font-size:16px;margin:0 0 8px}.stage.success{border-color:#7fe4cf}.stage.failed,.stage.blocked{border-color:#ff9900}.stage.running,.stage.human{border-color:#39d5e8}.filters{display:flex;flex-wrap:wrap;gap:16px;margin-bottom:24px}.filters label{display:flex;flex-direction:column;font-size:13px}.filters input,.filters select{background:#081525;color:#eef5ff;border:1px solid #6289a4;padding:10px;border-radius:6px;max-width:100%}pre{overflow:auto;white-space:pre-wrap;overflow-wrap:anywhere;font-size:13px}.brand{color:#39d5e8;letter-spacing:3px;font-size:12px;font-weight:700}h1{font-size:clamp(25px,4vw,38px);line-height:1.25;margin:18px 0 10px}h2{font-size:18px;color:#fff;margin:0 0 16px}.muted,small{color:#a9bed1}.grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:20px}.card{background:#11243a;border:1px solid #25425d;border-radius:14px;padding:24px;min-width:0}.wide{grid-column:1/-1}.badge{display:inline-block;background:#20374b;border:1px solid #3e627d;color:#8fe8f1;border-radius:20px;padding:4px 12px;margin:8px 6px 0 0;font-size:13px}.version{font-size:22px;overflow-wrap:anywhere}a{color:#70e1ec}p{margin:8px 0 14px}dl{margin:0}dt{color:#a9bed1;font-size:13px}dd{margin:2px 0 16px;overflow-wrap:anywhere}code{font-size:13px;overflow-wrap:anywhere;white-space:pre-wrap}details{margin:14px 0;border-top:1px solid #29445a;padding-top:12px}summary{cursor:pointer;color:#78dce8}button,.button{display:inline-block;border:0;border-radius:8px;padding:12px 18px;background:#ff9900;color:#102033;font:600 15px system-ui;cursor:pointer;text-decoration:none;margin:8px 10px 0 0}.secondary{background:#244159;color:#eef5ff}.reject{background:#603540;color:#fff}button:focus,a:focus,summary:focus{outline:3px solid #39d5e8;outline-offset:3px}textarea{width:100%;min-height:110px;background:#081525;color:#fff;border:1px solid #6289a4;border-radius:8px;padding:12px;font:inherit}.timeline{display:flex;flex-wrap:wrap;gap:10px;padding:0;list-style:none}.timeline li{padding:9px 12px;border:1px solid #38566e;border-radius:8px;color:#a9bed1}.timeline .done{color:#7fe4cf;border-color:#368778}.history{list-style:none;padding:0}.history li{border-left:2px solid #39d5e8;padding:0 0 18px 18px;margin-left:4px}.copy{font-size:12px;padding:7px 10px}.criticality{font-size:20px;padding:14px 18px;border-left:5px solid #ff9900;background:#081525;border-radius:8px}.actions-top{margin-bottom:24px}.approval-mode{color:#8fe8f1;font-weight:600}nav{margin:14px 0}.summary{display:flex;flex-wrap:wrap;gap:12px;margin:20px 0}.summary .badge{font-size:16px}.entry{margin-bottom:18px}footer{color:#9fb6c8;margin-top:28px;font-size:13px}@media(max-width:720px){.grid{grid-template-columns:1fr}header{padding:23px}.card{padding:20px}.stage-list{grid-template-columns:1fr}}'''
JS='''document.addEventListener('click',async(e)=>{const b=e.target.closest('button[data-copy]');if(!b)return;const t=document.getElementById(b.dataset.copy);if(!t)return;try{await navigator.clipboard.writeText(t.textContent);b.textContent='Copiado';}catch{b.textContent='Selecione e copie o valor';}});'''


def csp() -> str:
    def digest(value):return base64.b64encode(hashlib.sha256(value.encode()).digest()).decode()
    return "default-src 'none'; style-src 'sha256-"+digest(CSS)+"'; script-src 'sha256-"+digest(JS)+"'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'"


PHASE={'DECISION_RECORDED':'Decisão registrada — aguardando validação','CREATED':'Criada','HUMAN_REVIEW':'Aguardando aprovação humana','APPROVED':'Aprovada','DEPLOYING':'Em implantação','OBSERVING':'Observação de 12 horas','CLOSED_SUCCESS':'Encerrada com sucesso','FAILED':'Falha — análise necessária','REJECTED':'Rejeitada','ROLLED_BACK':'Rollback concluído','ROLLBACK_REQUESTED':'Rollback solicitado','ROLLBACK_RUNNING':'Rollback em execução'}
CRITICALITY={'CRITICAL':'CRÍTICA','NON_CRITICAL':'NÃO CRÍTICA','CUSTOM_CRITICAL':'CRÍTICA CUSTOMIZADA'}
EVENT={'decision_recorded':'Decisão autenticada registrada','created':'Criada','human_review':'Validação concluída; revisão humana','approved':'Aprovada','deploying':'Implantação iniciada','observing':'Implantada e validada; observação de 12 horas','closed_success':'Encerrada após observação','failed':'Falha registrada','rejected':'Rejeitada','rollback_requested':'Rollback solicitado','rollback_running':'Rollback em execução','rolled_back':'Rollback validado'}


def render(release: dict, context: dict, csrf: str, *, now: datetime, rollback: bool=False, rollback_pending: bool=False, read_only: bool=False) -> str:
    esc=lambda value:html.escape(str(value if value is not None else 'Não informado'),quote=True)
    app={'orders-api':'Orders API','orders-worker':'Orders Worker'}[release['service']]
    gmud=context.get('rcl_id') or 'GLPI '+str(context.get('change_id','—'))
    phase=context.get('phase','CREATED');label='Análise necessária' if context.get('reconciliation_required') else PHASE.get(phase,'Em análise')
    current=context.get('current_version') or release['current_digest'][:19]
    candidate=context.get('application_version') or release['commit'][:12]
    def card(title,body,wide=False):return '<section class="card'+(' wide' if wide else '')+'"><h2>'+esc(title)+'</h2>'+body+'</section>'
    out='<!doctype html><html lang="pt-BR"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>'+esc(gmud+' — '+app)+'</title><style>'+CSS+'</style></head><body><main>'
    out+='<header><div class="brand">COSMOTECH SOLUTIONS</div><nav><a href="/releases/">Todas as aprovações</a></nav><h1>'+esc(gmud+' — '+app)+'</h1>'+('<p class="muted">Conectado como '+esc(context['viewer'])+'</p>' if context.get('viewer') else '')+approval_banner(context)+'<p class="criticality">Criticidade da aplicação: <strong>'+esc(criticality_label(release,context))+'</strong></p><p class="approval-mode">'+esc(approval_mode(context))+'</p><p class="version">'+esc(current)+' → '+esc(candidate)+'</p><span class="badge">Ambiente '+esc(release['environment'].upper())+'</span><span class="badge">'+esc(CRITICALITY.get(context.get('criticality'), 'CRÍTICA' if release['service']=='orders-api' else 'NÃO CRÍTICA'))+'</span><span class="badge">'+esc(label)+'</span>'+canary_badge(release,context)+tracking_links(release,context)+'</header><!--top-actions--><div class="grid">'
    out+=operational_panel(release,context,now)
    out+=card('Identificação da mudança','<dl><dt>GMUD corporativa</dt><dd>'+esc(gmud)+'</dd><dt>Aplicação e versão</dt><dd>'+esc(app+' · '+candidate)+'</dd><dt>Registro nativo GLPI</dt><dd><a href="https://glpi.cosmotechsolutions.com.br/front/change.form.php?id='+esc(context.get('change_id',''))+'">Abrir mudança '+esc(context.get('change_id','—'))+'</a></dd></dl>')
    policy=('Sempre exige aprovação humana autenticada.' if release['service']=='orders-api' else 'Aprovação automática condicionada aos controles técnicos; mudanças sensíveis exigem revisão humana.')
    if context.get('criticality')=='CUSTOM_CRITICAL':policy='Aprovação automática somente após cinco GMUDs reais, válidas e concluídas com sucesso da mesma aplicação e ambiente; caso contrário, revisão humana.'
    out+=card('Criticidade e política','<p>'+esc(policy)+'</p><p class="muted">A política é protegida. Aprovação não sobrepõe uma falha técnica.</p>')
    out+=card('Pipeline e verificações','<p>Testes: <strong>'+('Aprovados' if release['checks']['tests_passed'] else 'Reprovados')+'</strong></p><p>Trivy: <strong>'+('Aprovado' if release['checks']['scan_passed'] else 'Reprovado')+'</strong></p><a href="https://github.com/rlcosmo/cosmotech-'+esc(release['service'])+'/actions/runs/'+esc(release['run_id'])+'">Abrir pipeline CI</a>')
    details=''
    for key,title in [('commit','Commit'),('current_digest','Digest atual'),('digest','Digest candidato')]:
        details+='<p>'+title+'</p><code id="'+key+'">'+esc(release[key])+'</code><br><button type="button" class="secondary copy" data-copy="'+key+'">Copiar</button>'
    out+=card('Versões e integridade',('<p>Imagem da esteira: <strong>'+esc(context['image_tag'])+'</strong></p>' if context.get('image_tag') else '')+'<p>Atual: '+esc(current)+'</p><p>Candidata: '+esc(candidate)+'</p><details><summary>Expandir commit e digests</summary>'+details+'</details>')
    out+=card('Plano de implantação','<p>'+esc(context.get('execution_plan') or 'Publicação controlada pelo GitOps e validação de saúde no Argo CD.')+'</p>')
    out+=card('Plano de rollback','<p>'+esc(context.get('rollback_plan') or 'Solicitação autenticada durante a janela; conferir versão atual e executar reversão controlada.')+'</p><p>Prazo: '+esc(context.get('rollback_until') or 'A janela inicia após validação técnica.')+'</p>')
    events=context.get('events') or [];types={e['type'] for e in events}
    timeline=''
    for title,done in [('Criada',bool(events)),('Validada',all(release['checks'].get(k) is True for k in ['tests_passed','scan_passed','digest_verified','argo_healthy','observability_reliable'])),('Aprovada','approved' in types),('Implantada','observing' in types),('Observação de 12h',phase=='OBSERVING' or 'closed_success' in types),('Encerrada',phase in {'CLOSED_SUCCESS','ROLLED_BACK','REJECTED'})]:timeline+='<li'+(' class="done"' if done else '')+'>'+title+'</li>'
    out+=card('Linha do tempo','<ol class="timeline">'+timeline+'</ol>',True)
    history='<p>Responsável: '+esc(context.get('owner'))+'</p><ol class="history">'
    for event in events:
        details=event.get('details',{});history+='<li><strong>'+esc(EVENT.get(event['type'],'Evento de acompanhamento'))+'</strong><br><small>'+esc(event['at'])+'</small>'
        if details.get('principal'):history+='<p>Decisão por '+esc(details['principal'])+'</p>'
        history+='</li>'
    out+=card('Histórico de decisões',history+'</ol>',True)
    eligible=(context.get('rollback_eligible') is True and phase=='OBSERVING' and context.get('rollback_until') and now<instant(context['rollback_until']) and not rollback_pending)
    form='<input type="hidden" name="csrf" value="'+esc(csrf)+'">'
    if read_only:
        actions='<p>Consulta de execução e evidências. <a href="/releases/'+release_identity(release)+'">Abrir ações da GMUD</a></p>'
    elif context.get('glpi_sync',{}).get('status')=='UNAVAILABLE':
        actions='<p>Consulta ao histórico disponível. Ações bloqueadas até a reconciliação com GLPI.</p>'
    elif rollback and eligible:
        actions='<p>A solicitação será verificada pelo Kestra antes de qualquer reversão.</p><form method="post">'+form+'<label for="reason">Justificativa</label><textarea id="reason" name="reason" minlength="8" maxlength="2000" required></textarea><button name="result" value="APPROVE">Solicitar rollback</button></form>'
    elif phase=='HUMAN_REVIEW' and context.get('decision')!='APPROVE':
        actions='<form method="post">'+form+'<button name="result" value="APPROVE">Aprovar</button><button class="reject" name="result" value="REJECT">Rejeitar</button></form>'
    elif eligible:actions='<a class="button" href="/releases/'+release_identity(release)+'/rollback">Solicitar rollback</a>'
    else:actions='<p>Somente leitura. '+('A versão já foi substituída ou aguarda conferência; reversão exige análise.' if phase=='OBSERVING' and context.get('rollback_eligible') is False else 'Nenhuma ação disponível neste estado.')+'</p>'
    out=out.replace('<!--top-actions-->','<section class="card actions-top"><h2>Ações da GMUD</h2>'+actions+'</section>',1)
    return out+'</div><footer>CosmoTech Solutions · Mudanças rastreáveis · Horários em UTC</footer></main><script>'+JS+'</script></body></html>'


def release_identity(release):
    from automation.promotion_gate import release_id
    return release_id(release)


def confirmation(message: str, identity: str) -> str:
    return ('<!doctype html><html lang="pt-BR"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>CosmoTech — solicitação registrada</title><style>'+CSS+'</style></head><body><main><header><div class="brand">COSMOTECH SOLUTIONS</div><h1>Solicitação registrada</h1></header><section class="card"><p>'+html.escape(message)+'</p><a class="button" href="/releases/'+identity+'">Acompanhar GMUD</a></section></main></body></html>')


def criticality_label(release: dict, context: dict) -> str:
    return CRITICALITY.get(context.get('criticality'), 'CRÍTICA' if release['service']=='orders-api' else 'NÃO CRÍTICA')


def approval_mode(context: dict, recorded: dict | None = None) -> str:
    """Describe actual decisions; noncritical alone never means auto-approved."""
    approved=next((e for e in reversed(context.get('events') or []) if e['type']=='approved'),None)
    if approved:
        principal=approved.get('details',{}).get('principal')
        if principal=='policy-engine':return 'Aprovada automaticamente pela política'
        if principal:return 'Aprovada por interação humana'
        return 'Aprovação registrada — origem não informada'
    if recorded is not None:
        return 'Aprovação humana registrada — aguardando validação' if recorded.get('result')=='APPROVE' else 'Rejeição humana registrada'
    if context.get('phase')=='HUMAN_REVIEW':return 'Interação humana necessária'
    if context.get('phase')=='DECISION_RECORDED':return 'Decisão humana registrada — aguardando validação'
    return 'Aguardando validação ou sem aprovação registrada'


def render_index(entries: list[dict], filters: dict | None = None, ci_entries: list[dict] | None = None, viewer: str | None = None) -> str:
    esc=lambda value:html.escape(str(value),quote=True)
    pending=[e for e in entries if e['context'].get('phase')=='HUMAN_REVIEW' and e['decision'] is None]
    others=[e for e in entries if e not in pending]
    automatic=sum(any(x['type']=='approved' and x.get('details',{}).get('principal')=='policy-engine' for x in e['context'].get('events') or []) for e in entries)
    out='<!doctype html><html lang="pt-BR"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta http-equiv="refresh" content="30"><title>CosmoTech — Central de releases</title><style>'+CSS+'</style></head><body><main><header><div class="brand">COSMOTECH SOLUTIONS</div><nav><a href="/releases/login">Entrar como laboratorio</a></nav><h1>Central de releases</h1><p class="muted">Central de aprovações e acompanhamento operacional</p><p>Identifique o que exige sua decisão e acompanhe as aprovações automáticas.</p><div class="summary"><span class="badge">'+str(len(pending))+' pendente(s) de interação humana</span><span class="badge">'+str(automatic)+' aprovação(ões) automática(s) no histórico</span></div></header>'
    if viewer:out+='<p class="muted">Conectado como '+esc(viewer)+'</p>'
    out+=filter_form(filters or {})
    if ci_entries and not any((filters or {}).values()):
        out+='<h2>CI — antes da criação da GMUD</h2>'
        for ci in ci_entries:
            out+='<article class="card entry"><h2>'+esc({'orders-api':'Orders API','orders-worker':'Orders Worker'}[ci['application']])+' — CI</h2><p>Commit '+esc(ci['commit'][:12])+' · Aguardando GMUD após CI aprovada</p>'
            for step in ci['stages']:
                name={'tests':'Testes','build_scan':'Build e Trivy','publish':'Publicação ECR'}[step['name']]
                state={'running':'Em andamento','success':'Concluída','failure':'Falha','cancelled':'Cancelada','skipped':'Bloqueada','pending':'Pendente'}.get(step['state'],'Não confirmado')
                out+='<span class="badge">'+name+': '+esc(state)+'</span>'
            if ci.get('promotion_error'):
                out+='<p class="notice"><strong>FALHA — '+esc(ci['promotion_error']['stage'])+'</strong><br>'+esc(ci['promotion_error']['problem'])+'</p>'
            out+='<details><summary>CI e evidências</summary><p>Execução GitHub '+esc(ci['run_id'])+' · tentativa '+esc(ci['attempt'])+'</p></details></article>'
    for title,rows in [('Pendentes de aprovação humana',pending),('Validação, aprovações registradas e acompanhamento',others)]:
        out+='<h2>'+title+'</h2>'
        if not rows:
            out+='<section class="card entry"><p>'+('Nenhuma aprovação humana pendente.' if not rows and title.startswith('Pendentes') else 'Nenhuma outra GMUD disponível.')+'</p></section>'
        for entry in rows:
            release,context,decision=entry['release'],entry['context'],entry['decision']
            app={'orders-api':'Orders API','orders-worker':'Orders Worker'}[release['service']]
            identity=context.get('rcl_id') or 'GLPI '+str(context.get('change_id','—'))
            version=context.get('application_version') or release['commit'][:12]
            phase='Análise necessária' if context.get('reconciliation_required') else PHASE.get('DECISION_RECORDED' if context.get('phase')=='HUMAN_REVIEW' and decision is not None else context.get('phase'),'Em análise')
            out+='<article class="card entry"><h2>'+esc(identity+' — '+app)+'</h2>'+canary_badge(release,context)+sync_banner(context)+'<p>'+esc(version+' · '+release['environment'].upper())+'</p><span class="badge">Criticidade: '+esc(criticality_label(release,context))+'</span><span class="badge">'+esc(phase)+'</span><p class="approval-mode">'+esc(approval_mode(context,decision))+'</p><a class="button" href="/releases/'+esc(entry['id'])+'">'+('Revisar e decidir' if entry in pending else 'Ver GMUD')+'</a>'+tracking_links(release,context)+'</article>'
    return out+'<footer>As decisões exigem identidade autenticada. A listagem não aprova ou promove releases.</footer></main></body></html>'


def canary_badge(release: dict, context: dict) -> str:
    """Label only an observed, protected Worker delivery strategy."""
    if release['service']!='orders-worker' or context.get('deployment_strategy')!='CANARY':return ''
    stage=context.get('canary_stage')
    suffix={'CANARY_1':' · 1 consumidor','CANARY_2':' · 2 consumidores','PROMOTING':' · promovendo',
            'DRAIN_PROMOTED':' · drenando','DRAIN_ROLLBACK':' · drenando para rollback',
            'COMPLETE':' · concluído','ROLLED_BACK':' · revertido'}.get(stage,' · em andamento')
    return '<span class="badge">Canary'+suffix+'</span>'


def tracking_links(release: dict, context: dict) -> str:
    """Construct links exclusively under the fixed CD origins and known app names."""
    execution=release.get('execution_id','')
    links='<a class="button secondary" href="/releases/'+release_identity(release)+'/execution">Etapas e evidências do CD</a>'
    if re.fullmatch(r'[A-Za-z0-9_-]{1,128}',execution):
        flow=context.get('kestra_flow_id','promotion_gate')
        if flow not in {'promotion_gate','release_pipeline'}:flow='promotion_gate'
        url='https://kestra.cosmotechsolutions.com.br/ui/executions/main/cosmotech.promotion/'+flow+'/'+execution
        links+='<a class="button secondary" href="'+url+'">Acompanhar esteira CD</a>'
    app=release['service']+'-dev'
    links+='<a class="button secondary" href="https://argocd.cosmotechsolutions.com.br/applications/'+html.escape(app,quote=True)+'">Acompanhar Argo CD</a>'
    return '<nav aria-label="Acompanhamento CD">'+links+'</nav>'
