## Estado vigente: RCL e portal —06/11/2026

Gate revisão4 e nova identidade corporativa RCL implantados; portal pt-BR ativo, preservando autenticação/CSRF. RCL000001/GLPI5 teve Canary abortado com drenagem; RCL000002/GLPI6 API aguarda humano; RCL000003/GLPI7 Worker1.0.2 promovido após Canary1→2 e drenagem, em OBSERVING até22:04:27Z. Ver [operação RCL](rcl-portal-operations.md) e documentos showcase10/14. A observação12h não foi encerrada antecipadamente.

## Registro anterior preservado

## Atualização06/11/2026 após aprovação do responsável

As GMUDs3/API e4/Worker foram aprovadas por identidade autenticada e implantadas. Kestra SUCCESS na revisão2; GLPI permanece Review8/OBSERVING, sem erros registrados. API usa RollingUpdate; Worker executou o bootstrap rolling de telemetria, explicitamente aprovado. Não existe deployment Canary ativo; a ligação Canary normal permanece pendente. Janela API até20:45:17Z e Worker até20:46:26Z de06/11, sujeita aos controles de continuidade.

Gate atualizado à revisão3 apenas para apresentação: tarefa nativa Labels depois da política, antes da aprovação. Versão é commit12 caracteres, digest completo permanece na evidência. Labels application/version/gmud/release foram aplicados às duas execuções concluídas e lidos de volta, sem executar deploy ou mudar decisão/GMUD. Exemplo: `orders-worker | a9dac69545cd | GMUD 4`. O ID técnico imutável permanece para auditoria. Na lista Executions, habilitar coluna Labels se estiver oculta pela preferência do navegador; filtrar application/gmud. Não se modifica frontend ou ID de execução.

Kestra OSS2.0.8 não persiste dashboards customizados no controller instalado; foi usada a identificação nativa por labels. Não se anuncia dashboard novo ativo. Fluxo revisão3 validado pela API sem warnings/constraints; config protegida fixa revisão3. Scheduler revisão2 e observação das releases revisão2 preservados.155 testes e4 subtestes locais passaram; Ruff passou. Nenhuma nova release foi submetida ou aprovada por esta correção.

Erro inicial15021: uma falha por pod, proxy ainda abrindo listener, não falha contínua das aplicações. Corrigido no injector Istio da infraestrutura, somente novos pods Orders; dois pods reais isolados confirmaram Ready e ausência de Warning/restart. Aplicações aprovadas não reiniciadas.

Registros SSM: aprovação/deploy e observação d49b7dac-43f2-43c4-b59a-63d3a7ee2575; labels/flow revision3 1a75aa4f-3096-40f6-8004-5df38bc64153. Commits GitOps de release:999f4ad946ef4f67bdb05d44bd0a5135e319ddb2 e4de7fac9527fcf27ac127a328032aa05fc0a2838.

## Snapshot anterior preservado

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
