# Identidade corporativa RCL, portal e Canary

## Sequência e preservação histórica

Todas as novas GMUDs de release criadas pelo gate recebem RCL sequencial global no SQLite protegido da autoridade existente (`gmud.db`, tabela `rcl_allocations`, PRIMARY KEY AUTOINCREMENT e identidade de release UNIQUE). `BEGIN IMMEDIATE` serializa a reserva junto com o intent de criação. O commit ocorre antes do POST GLPI; resposta perdida bloqueia retry e mantém o número reservado. Cancelamento, falha e rollback não excluem a reserva. Reenvio retorna a mesma GMUD ou exige reconciliação, nunca um novo POST cego.

Não há contador no CI, portal, memória ou relógio. Um único host/ledger de autoridade é a fronteira existente; não se afirma HA ou sequência distribuída multi-região. Cópia/restauração antiga com RCL remoto maior bloqueia novas reservas até reconciliação. Não remover linhas, reinicializar sequência ou restaurar banco antigo sobre efeitos novos. Fazer backup SQLite consistente e reconciliar GLPI/journals/efeitos antes de recuperar a autoridade. O backup privado anterior ao primeiro RCL foi preservado em `/opt/cosmotech/promotion/backups/rcl-before-first-release-20261106`.

ID nativo GLPI e ID Kestra permanecem imutáveis. GMUDs anteriores não são renumeradas. Título: `RCL000001 | DEPLOY | ORDERS-WORKER | DEV | v1.0.1`. Versão vem de literal no código testado (sem executar código do repositório); se ausente, commit curto. A API anterior usa versão literal no handler, também lida por AST. Somente404 do arquivo opcional permite fallback; falhas de autorização e rede não são convertidas em ausência.

RCL aparece no título/conteúdo assinado GLPI, contexto portal, label Kestra `gmud_id`, identificação aplicação/versão, assunto SNS, registros GitOps assinados (`gmud_id` corporativo e `glpi_change_id` nativo), annotations e labels de pods candidatos. O PodMonitor propaga o label para Prometheus/Grafana. O envelope GLPI é autenticado por HMAC e lido antes/depois de transições. Reconciliação aceita apenas efeitos observados que correspondem ao intent protegido. Edição externa/replay/divergência bloqueiam; mudar estado no GLPI não concede autorização.

## Portal

Interface pt-BR responsiva em azul-marinho/grafite/laranja/ciano, cards, timeline, planos, CI/Trivy e histórico autenticado. Digests/commit completos ficam em detalhes expansíveis e botões copiar. Aprovação/rejeição só na fase HUMAN_REVIEW sem decisão já registrada; após decisão a tela aguarda reconciliação e não repete botões. Rollback aparece somente em OBSERVING, dentro do prazo e sem solicitação duplicada. Concluídas são leitura.

Autenticação existente, autorização por identidade, CSRF por ação/release, Origin, binding de versão e auditoria permanecem. CSS/JS locais autorizados por hash na CSP, sem unsafe-inline, dependências externas ou tokens em URL. O portal continua sem chave de publicação. GET HTTPS autenticado foi verificado; nenhum POST de aprovação de produção foi feito pelo agente. Testes locais exercitam aprovação e CSRF com identidades sintéticas.

## Canary e scheduler

Reutilizados CanaryController e CanaryExecutor. Binding publica candidata inicialmente com0 réplicas, mantém stable, fixa capacidade estável1 durante a prova, controla lease180s e réplicas1→2 via RBAC nomeado. Primeiros90s de cada etapa aguardam inicialização/coletas sem renovar lease ou promover. As janelas exigem300s,50 eventos candidatos e10 estáveis, métricas frescas de todos os pods, p95, falhas, retries, persistência, fila e DLQ. Falta de evidência bloqueia; nenhum vetor vazio é declarado saudável. Latência sem eventos permanece ausente e não permite aprovação.

Observações reais são persistidas em `rollout_observations` junto com a geração do controller. O scheduler Kestra persistente executa progressão, drenagem e observação; não depende do processo temporário de carga. A carga sintética é limitada, identifica RCL, usa SKU exclusivo e pedidos rastreáveis, sem apagar dados ou substituir estoque existente. Pagamentos/fulfillment do laboratório são simulados pela própria aplicação; CI, SQS, DynamoDB, métricas e efeitos GitOps são reais.

RCL000001 provou aborto por métricas insuficientes, drenagem e preservação da stable; não provou reversão de uma stable já promovida. O primeiro tick ocorreu antes das primeiras coletas. Foi adicionada espera inicial limitada, preservado o registro cancelado e gerada versão1.0.2 com melhoria real de identificação no health. A restauração de capacidade pós-rollback usa efeito restrito `drain-complete:rollback`, somente HPA, com digest anterior exato e assinatura; uma GMUD revertida não pode republicar aplicações. A alteração de capacidade é reconciliada sem alterar o histórico nativo encerrado.

A janela12h só começa depois de validação técnica final; nenhum fechamento é antecipado. Rollback responsável exige identidade, justificativa, prazo, versão atual e drenagem. Lacunas>180s, erros ou versão substituída impedem fechar como sucesso. Uma janela inválida/versão substituída é isolada por release e impede seu encerramento sem bloquear a amostragem das demais. A aprovação humana de API continua pendente e nunca é feita pelo agente.

## Evidências e limites

RCL000001/GLPI5, RCL000002/GLPI6 e RCL000003/GLPI7 são reais; números não reutilizados. Fontes completas em showcase10/14 do laboratório. API2.0.1-lab aguarda humano; Worker1.0.2 concluiu1→2 consumidores, promoção e drenagem e entrou em OBSERVING até22:04:27Z de06/11/2026. Foram800 pedidos sintéticos aceitos; três amostras lidas pela API estavam COMPLETED, com pagamento SIMULATED_CAPTURED (simulação da aplicação, não pagamento externo real). Dashboard Grafana `orders-worker-canary` instalado; painéis SQS do exporter ainda podem apresentar SEM DADOS, explicitamente sem equivalência a zero. O gate consulta SQS/CW diretamente com permissão somente GetQueueAttributes nas duas filas e GetMetricStatistics já existente.

Sem novas instâncias/nós/volumes ou alterações NLB/CloudTrail. Bedrock permanece opcional e desabilitado, sem inferência. Custos variáveis de testes/notificações/telemetria não foram isolados; não declarar custo incremental zero.

## Central de aprovações e ações superiores

Página autenticada: https://kestra.cosmotechsolutions.com.br/releases/ . O proxy existente já atende esse prefixo; não houve alteração de rede, autenticação do provedor ou fluxos Kestra. Pendências HUMAN_REVIEW sem decisão ficam primeiro. A outra seção acompanha validações, decisões já registradas e estados implantados/encerrados. O nível de criticidade aparece explicitamente em cada card e em destaque na página da GMUD.

O método de aprovação vem do histórico: principal policy-engine significa automática; outro principal auditado significa humana; ausência de decisão não é inferida como aprovação por ser não crítica. Decisão humana já registrada deixa a fila de pendentes enquanto aguarda reconciliação. Rollback ou falha posteriores permanecem visíveis no estado da mudança, mesmo que a autorização inicial tenha sido automática.

Aprovar/Rejeitar foram movidos para cima dos detalhes. Formulário único, contextual, autenticado e com o mesmo CSRF/Origin; nenhuma aprovação em massa ou por GET foi introduzida. A central exige o mesmo provedor e a mesma lista de identidades autorizadas, inclusive vazia.172 testes e4 subtestes passaram; Ruff passou. A verificação real usa somente GET e confere que a contagem de decisões permanece inalterada.
