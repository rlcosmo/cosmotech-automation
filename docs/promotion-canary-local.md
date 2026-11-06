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

# Gate determinístico e Canary exclusivo Worker

Decisão vigente do proprietário: Kestra autoriza por criticidade; Bedrock é
opcional e desabilitado. A exigência antiga de IA para Worker foi substituída.
Não chamar o modelo enquanto quotas zeradas. API exige humano real.

Componentes existentes reutilizados: política/schema/recibo Ed25519/ledger,
classificação conservadora de manifests, coletor confiável, portal/CSRF,
notificação SNS, publisher GPG e máquina persistente de Canary/lease/drain.
Defaults do motor e CLI desabilitam IA; coordinator, verificação de execução
Kestra, portal e notificação aceitam avaliação sem resposta LLM. A verificação
Kestra exige verified_evidence e deterministic_policy; Bedrock só no modo opt-in.

Sem testes/Trivy/digest/contrato válidos, runtime confiável e enforcement
independente, não há promoção. Worker com mudança sensível ou dependência não
revisada requer humano. API sempre requer humano. Aprovação não sobrepõe falha
grave. Recibos expiram em10min; dispatch é consumido uma vez; falha ambígua exige
reconciliação. Nunca aceitar aprovação/enforcement/checks do JSON do CI.

## Implantado e verificado nesta retomada

Seis módulos atualizados no host existente e backup preservado. Portal active e
healthcheck200 após reinício. Probe remoto validado e desabilitado, revisão2,
com guarda local contra execução Bedrock. Nenhuma inferência, SNS Publish ou
promoção de aplicação nesta retomada. Portal é decision-only, sem publisher.

## Bloqueio humano

App rlcosmo-cosmotech-gitops declara Actions read, mas a instalação178643233
não concedeu: token Actions read retorna422 e consulta de runs sem ela retorna403
nos dois apps. Repositórios já selecionados. Proprietário precisa aceitar
Actions Read-only em https://github.com/settings/installations/178643233.
Não substituir o coletor por claims do CI ou token pessoal permanente.

Fluxo principal, bindings de evidência/staging/publicação/reconciliação,
assinatura obrigatória no Argo e exclusividade do publisher continuam pendentes.
Os workflows remotos mantêm o caminho antigo; não foram substituídos parcialmente.
Os arquivos .disabled e o probe não são o gate principal. Não habilitar
`enforcement_verified` antes de prova independente no destino.

## Validação local

Com ambiente existente, sem instalar ferramentas:

```bash
PYTHONPATH=src /opt/aws-serverless-lab/.venv/bin/python -m pytest tests/unit -q
/opt/aws-serverless-lab/.venv/bin/ruff check src tests
```

Testes usam mocks/SQLite/Git/GPG temporários; não provam integração operacional.
A demo local permanece ISOLATED_SIMULATION; não foi reapresentada como rollback
EKS. Próximo passo após permissão: conectar adaptadores e cutover conjunto,
depois Canary Worker com dados sintéticos e observação/drenagem reais. Não
aprovar release API pelo proprietário. Não ampliar nós ou criar componentes
sem revisão de capacidade/custo. NLB/CloudTrail fora do escopo.

Plano e evidências canônicos: /opt/aws-serverless-lab/docs/linkedin-showcase/10-action-plan.md e14-evidence-index.md.

</details>

## Correção após o teste real RCL000005

O teste1.0.3 revelou permissão ausente em ChangeMessageVisibility durante drain;
a mensagem sem ack ficou sujeita à visibilidade180s, o backlog excedeu120s e a
promoção foi revertida pelo controlador. A candidata drenou e stable1.0.2/HPA
foram restaurados. Esse teste não é sucesso.

A correção1.0.4 preserva a mensagem e a drenagem quando a devolução falha, emite
visibility_errors_total e não reinicia o processo. Poll/visibility errors agora
bloqueiam qualidade; qualquer reinício da candidata bloqueia a progressão mesmo
se contadores tiverem zerado. A prova de drain usa somente heartbeat fresco,
drain/inflight e stable sincronizada, sem exigir janela/amostras de negócio.

Lease do controlador/executor unificada em300s, dentro do teto300s já validado
pelo consumidor. Isso acomoda aquecimento90s, tick persistente de60s e propagação
eventual do volume ConfigMap. A interrupção automática de consumo por ausência
de renovação passa de180s a300s; não é renovação cega. Frescor90s, janelas300s,
limites de backlog/p95, assinatura, digest, exclusividade e expiração/drain
permanecem exigidos. Referência: documentação Kubernetes ConfigMaps, seção
Mounted ConfigMaps are updated automatically.

Reconciliação terminal: após COMPLETE/ROLLED_BACK, não consultar novamente pods
candidatos removidos. Usar o estado persistido de drain validado e a execução
idempotente dos efeitos; validação da stable/Argo continua obrigatória antes de
OBSERVING. Isso evita que a ausência esperada de pods reabra erro de métricas.
