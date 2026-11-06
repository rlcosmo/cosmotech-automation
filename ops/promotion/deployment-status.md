# GLPI GMUD — operação e estado real

Atualização06/11/2026 UTC. Gate Kestra revision2 e scheduler persistente revision2 ativos; GMUD3/API e GMUD4/Worker aguardam humano autenticado. Coleta/política SUCCESS; SNS aceitou as duas notificações, caixa postal não verificada. Actions read resolvido. CI sem chave de escrita GitOps e Argo exige a autoridade GPG exclusiva. Commit assinado de controle não promoveu aplicação.

Política protegida: CRITICAL/API sempre humana, NON_CRITICAL/Worker automática condicional, CUSTOM_CRITICAL exige cinco sucessos distintos, reais e auditáveis da mesma app/ambiente. Esta primeira mudança Worker altera workflow/dependências e precisa de revisão. CI e IA não reclassificam. Bedrock opcional/desabilitado, sem inferências.

GLPI usa Change nativo: New1, Approval10, Accepted7, Testing11, Review8, Closed6, Refused13, Cancelled14. Sucesso técnico fica Review8 por12h. Falha técnica permanece Review8 com outcome FAILED, exige análise e nunca conta como sucesso. Recusa humana vira Refused13; rollback vira Cancelled14 com outcome explícito. Não existe enum Failed inventado. A decisão autenticada do portal é registrada no journal; o cliente OAuth técnico não se faz passar pelo usuário no GLPI.

Identidade da GMUD vincula app/ambiente/commit/digest/run; primeira execução Kestra é dona. Evidências frescas são recoletadas após pausa sem alterar alvo. Escritas externas são precedidas por intent durável, recibos e hashes. Uma resposta ambígua não autoriza retry cego. GLPI indisponível, histórico inválido/copias, divergência nativa/journal ou política inválida bloqueiam promoção.

## Recuperação, persistência e retenção

Autoridade e journals root-only em /var/lib/cosmotech-promotion/authority; configuração protegida em /etc/cosmotech/promotion. Chaves e journals permanecem no disco existente e não entram no Git. Há backups locais de fonte; restauração off-host/HA da autoridade ainda não foi validada. Não afirmar DR completo. Não remover ou restaurar ledger antigo sobre efeitos novos.

Se criação GLPI perdeu resposta: consultar Change real e identidade antes de ChangeJournal.reconcile. Esse método só confirma o intent observado, nunca inventa sucesso. Para push ambíguo, conferir branch/revisão exata, assinatura e resultado antes de continuar; sem prova, análise humana. Não resetar histórico nem excluir GMUDs. Sem expurgo automático; retenção definitiva e backup off-host precisam de revisão operacional do proprietário.

Observação é persistente: scheduler Kestra a cada minuto e amostras em SQLite, sem processo aguardando12h. Lacunas>180s, erros, incidentes, troca de digest, rollback ou falta de continuidade impedem encerramento. Responsável autenticado pode solicitar rollback com justificativa até12h após validação. Prazo vencido exige nova GMUD; versão substituída, concorrência ou efeito ambíguo exige análise. Worker drena polls e exige inflight zero fresco antes de restaurar versão conhecida.

## Limites ainda pendentes

Nenhuma aprovação humana, promoção de aplicação, candidata Canary ou janela real12h nesta etapa. O Worker inicial precisa de bootstrap humano de telemetria; execução Canary normal ainda precisa ser ligada. O runtime recusa rolling normal quando já existem métricas versionadas. Não apresentar bootstrap como Canary integrado. Componentes de Canary/lease/drenagem permanecem reaproveitados e testados isoladamente.

Validação final local:155 testes,4 subtestes e Ruff passaram. API65 e Worker81 testes locais e duas CIs reais verdes após correção apt/libapt. Relógio sintético/mocks não provam rollback integrado. Check nativo final confirmou zero sucessos reais encerrados e retries idempotentes de GMUD3/4 com zero tentativas de POST.

Custos incrementais não isolados; nenhuma nova instância/nó/volume. SNS e telemetria têm custos variáveis. Não executar NLB/CloudTrail ou chamadas Bedrock. Evidências/documentos visuais canônicos em /opt/aws-serverless-lab/docs/linkedin-showcase/10-action-plan.md e14-evidence-index.md.

## Registro anterior preservado

<details><summary>Histórico anterior (estado superado)</summary>

# Estado da implantação — retomada determinística

Atualização: 2026-11-06T00:44Z. Decisão do proprietário substitui IA obrigatória:
Kestra determinístico, Bedrock opcional e desabilitado, API sempre humana.

Implantação anterior: IAM Bedrock/SNS/KMS e runtime isolado; portal HTTPS
decision-only com autenticação/CSRF; teste SNS aceito, mailbox não comprovada;
probe Bedrock falhou por quota0. Isso não demonstra gate principal nem Canary.

Nesta retomada: seis módulos atualizados no host e backup preservado; portal
reiniciado e healthcheck200; defaults IA false conferidos no host. Probe validado
sem warnings e atualizado para disabled=true revisão2. Nenhuma inferência,
novo SNS Publish, Terraform apply, promoção de aplicação ou alteração remota CI.

A leitura imediata de healthcheck após restart retornou conexão recusada;
verificação posterior confirmou active e HTTP200. Nenhuma inferência foi repetida.

Bloqueio externo confirmado: a instalação178643233 do App
rlcosmo-cosmotech-gitops não concedeu Actions read declarado no App. Token com
Actions read retorna422; consulta aos runs sem essa permissão retorna403, em
orders-api e orders-worker. Aceitação pelo proprietário em
https://github.com/settings/installations/178643233. Nenhum segredo precisa ser
enviado. Não usar claims do CI para substituir essa verificação.

Gate principal/bindings de evidências/publisher/enforcement GPG/SSM e Canary
continuam não ativados. Permissões/SSM/access entry preparados não foram
aplicados nesta retomada. Não executar cutover parcial nem afirmar exclusividade
no ambiente atual. Nenhuma aprovação humana foi registrada pelo agente.

Preservar ledger/CSRF e backups em /opt/cosmotech/promotion/backups. Para regressão
de portal, restaurar módulos do backup correspondente e reiniciar apenas
cosmotech-approval-portal; não excluir ledger. Recuperação nginx anterior:
/opt/cosmotech/promotion/nginx-before-approval.conf, seguida de nginx -t e reload.
Não restaurar dados/estado antigo sobre atividade nova sem reconciliação.

Depois da concessão: revalidar fontes, integrar gate/portal/SNS/publisher,
comprovar recusa de bypass no Argo e então instrumentar/ativar Canary somente
Worker com métricas/dados sintéticos e rollback real. Não ampliar nós nem
operar NLB/CloudTrail. Custos e evidências em10/14 do repositório de laboratório.

</details>
