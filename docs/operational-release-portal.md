# Portal operacional de releases

O portal existente é a interface principal: https://kestra.cosmotechsolutions.com.br/releases/.
A entrada operacional é /releases/login. O administrador existente continua válido.
Kestra OSS2.0.8 usa a autenticação Basic nativa de uma conta; usuários/RBAC nativos
são recursos Enterprise. Referências oficiais:
https://kestra.io/docs/oss-vs-paid e
https://kestra.io/docs/administrator-guide/basic-auth-troubleshooting.
Não foi instalado frontend customizado no Kestra.

## Identidade operacional

`laboratorio` é autenticado no portal, com PBKDF2-SHA256/600000 iterações, salt
aleatório e arquivo root-owned0640 em /etc/cosmotech/promotion/operators.json.
O arquivo não está no repositório. A senha inicial foi recebida por entrada
protegida e não é registrada nesta documentação. Papel `operator`: consulta a
todas as mudanças do laboratório, decisões humanas, solicitação de rollback e
leitura de etapas, evidências e logs persistidos. Não administra usuários, flows,
infraestrutura, criticidade ou chaves. O acesso nativo do Kestra com essa conta
é negado; as integrações são feitas pela camada confiável existente.

HTTPS, allowlist protegida, desafios CSRF por release/ação, validação de Origin,
limite de tentativas e auditoria por identidade continuam obrigatórios.
A autenticação HTTP Basic usa TLS e não cria cookie de sessão na aplicação.
O registro de uma decisão não é uma autorização de deploy: Kestra revalida
CI, GLPI, política, digest, identidade e recibo antes do publisher.
Nenhuma aprovação deve ser feita pelo agente como `laboratorio` ou admin.

## Uma execução principal e fatos persistidos

Novas solicitações entram em `cosmotech.promotion/release_pipeline`, revisão1.
O código de gate, journal RCL, publisher exclusivo, assinatura e controlador
Canary foram reutilizados. A CI real observada pelas execuções existentes é
correlacionada com a principal por commit/run e labels release_execution_id/RCL.
O painel inclui CI antes da GMUD; a GMUD só nasce depois da CI aprovada.
O fluxo principal inicia após a publicação da imagem e correlaciona os horários
reais da CI. Não inventa uma execução prévia do gate durante build/testes.

`cosmotech.operations/promotion_reconcile`, revisão1, mantém o agendamento
persistente. Os dois flows legados em cosmotech.promotion foram desabilitados
para novas execuções; históricos/IDs/revisões foram preservados. A execução
principal pode terminar após iniciar o controle; `release_status` e o portal
mostram o ciclo real posterior, sem manter um processo artificialmente ativo12h.
SUCCESS do controle inicial não significa GMUD encerrada.

As dez etapas mostram estado, início, término, duração e evidência quando
registrados. Etapas futuras e horários históricos ausentes permanecem assim
identificados. Logs/digests/IDs completos ficam em detalhes. O cabeçalho informa
quem já aprovou, a conta conectada e a estratégia efetivamente iniciada.
Filtros são somente leitura. Não alteram criticidade nem estado.

## GLPI e indisponibilidade

GLPI continua oficial: título, estado nativo e envelope assinado são lidos e
conferidos. A projeção na SQLite existente permite consulta sem GLPI, incluindo
decisões, timestamps, evidências e estado de sincronização. O cache nunca
substitui GLPI para autorizar promoção. Falha de consulta, histórico ou política
bloqueia efeitos. O aviso preserva a última confirmação; ações são bloqueadas
até reconciliação. Nenhuma GMUD é duplicada por restauração ou retry.

O teste real de indisponibilidade parou brevemente somente o container GLPI,
com recuperação independente limitada a60s. A conta operacional continuou
lendo o portal, com aviso e ações bloqueadas. Após restaurar o mesmo container,
8 mudanças, decisões e efeitos GitOps conferiram; nenhuma aprovação/post de
release foi feita. Esse teste não é uma falha produtiva espontânea nem prova de
encerramento12h. Evidências finais ficam em docs/linkedin-showcase/14-evidence-index.md.

## Nome de imagem e segurança

CI publica uma alias legível `worker-<versão>-<build>` ou `api-<versão>-<build>`
para o mesmo manifesto/digest já testado. Não sobrescreve alias de outro digest;
erros de permissão não são tratados como alias ausente. A versão literal é lida
sem executar código; na ausência usa commit curto. O build vem da execução real.
O coletor confere alias e digest independentemente no ECR e utiliza
`repository:alias@sha256:digest`. O digest continua sendo a autoridade de pull.
Labels app.kubernetes.io/version e cosmotech.io/release também identificam pods.
RELEASE_VERSION das métricas continua sendo o digest. Não foram renomeados
Deployments, seletores ou controles Worker para contornar a proteção GitOps.

API continua crítica/humana; Worker não crítico pode ser automático se todos
os controles passarem. CUSTOM_CRITICAL requer cinco GMUDs reais encerradas sem
falha/rollback. Observação real12h e rollback autenticado preservam as verificações
de prazo, concorrência, versão substituída, lease e idempotência. Bedrock segue
desabilitado; NLB/CloudTrail não fazem parte desta alteração.

### Referências por tag imutável — 06/11/2026

Por decisão explícita do proprietário, o Deployment passa a usar o endereço ECR completo com a tag legível, sem `@sha256`. A tag contém aplicação, versão e número real da CI. O digest continua na evidência assinada e nas métricas, não precisa ser informado pelo operador ao selecionar a tag.

O publisher transforma somente aliases comprovados por `DescribeImages` em repositórios integralmente `IMMUTABLE`, sem exclusões. Falha de consulta, divergência ou mutabilidade bloqueia a publicação. Adapters internos resolvem referências por tag novamente para preservar comparação de versão, Canary, proteção contra versão substituída e rollback. Histórico sem alias continua aceitando referências fixadas por digest.

A operação restrita `image-reference:tag-only` permite converter uma GMUD em observação sem nova versão nem nova aprovação: aceita somente os Deployments da própria aplicação, o digest já aprovado e a remoção do sufixo. Qualquer alteração adicional é recusada por comparação integral com o GitOps assinado. Não muda a janela de observação, o aprovador ou os estados GLPI. GitOps continua oficial; edição direta no cluster pode ser reconciliada pelo Argo CD.

### Correção de acesso e acompanhamento CD — 06/11/2026

Identidade operacional preferencial: `laboratorio@cosmotechsolutions.com.br`, com a mesma senha e papel do laboratório. O alias anterior permanece válido; decisões históricas não são renomeadas. A conta é autenticada em `/releases/login`, não no formulário administrativo nativo do Kestra OSS.

`Acompanhar esteira CD` abre `/releases/<identidade>/execution`, protegido pela mesma autenticação e com etapas, logs e evidências persistidas. O link técnico separado usa a rota real da versão2.0.8: `/ui/main/executions/<namespace>/<flow>/<execution>`. Links históricos com `executions/main` recebem redirecionamento308 no proxy. Um HTTP200 no shell SPA não comprova que a rota é válida; o padrão foi conferido em `ui/src/components/executions/executionTabs.ts` da tag oficial v2.0.8. Nenhuma alteração no frontend interno do Kestra.

### Consulta na interface nativa do Kestra — 06/11/2026

`laboratorio@cosmotechsolutions.com.br` pode entrar no formulário original de `https://kestra.cosmotechsolutions.com.br/` usando o hash de senha operacional existente. Não é uma segunda conta/RBAC nativo do OSS: o adaptador reutiliza `OperatorAuthenticator`, emite uma sessão opaca de8h (somente SHA256 no SQLite) e o Nginx autoriza os endpoints de consulta antes de encaminhá-los ao Kestra. Nenhuma mudança no frontend ou nos serviços de promoção.

A credencial nativa administrativa é mapeada exclusivamente no servidor por configuração root0600, ilegível pelo processo do portal; nunca é enviada ao navegador do operador. Cookies operacionais são Secure, HttpOnly e SameSite=Strict; o indicador não sensível requerido pela UI é emitido separadamente. Login/logout exigem mesma origem. O cookie administrativo anterior é removido somente do navegador que escolhe entrar como operador; outras sessões/admin permanecem válidas.

A consulta cobre fluxos, execuções, logs, namespaces, métricas e dashboards. `POST /api/v1/main/dashboards/charts/preview` é a exceção de consulta revisada no código2.0.8; criar/editar/excluir fluxos, replay/resume, webhooks GET e endpoints de IA são bloqueados para essa sessão. GET não é presumido seguro: webhooks e actions são explicitamente negados. Aprovar/rejeitar/rollback continuam no portal, com identidade autenticada e CSRF existentes.

O admin sem cookie operacional continua indo diretamente ao backend nativo, mantendo autorização e funcionalidades. Login administrativo é encaminhado sem troca de credenciais e possui fallback para o backend nativo quando o adaptador não responde. Falhas na consulta operacional bloqueiam somente essa sessão. A configuração/proteções do admin não são substituídas.

Operação: instalar os módulos revisados, executar `ops/promotion/install-native-viewer.sh` no host existente e validar `nginx -t` sem imprimir o mapa secreto. A instalação preserva backup privado. Após rotação da senha nativa administrativa, executar `render-viewer-auth.py` e recarregar Nginx; nunca registrar seu resultado em Git. Sessões operacionais expiram em8h e são revogadas no logout; eventos de acesso são registrados pelo serviço com a identidade operacional, sem corpos, senhas ou tokens. O portal segue sem acesso à chave de assinatura/publisher.

### Capacidade estável definida em duas réplicas

As próximas versões API2.0.4-lab e Worker1.0.6 propõem `minReplicas=2` e `maxReplicas=2` no HPA DEV, mantendo-o como único proprietário da escala. O executor Canary passa a congelar o HPA no mínimo aprovado de cada versão, preservando duas réplicas quando essa for a capacidade do baseline ou da candidata; não força mais uma réplica indiscriminadamente. Após drenagem, restaura a configuração original do bundle aprovado; rollback restaura o HPA da versão anterior. Candidate temporário continua1→2, exclusivo Worker.

A alteração de HPA é sensível segundo a política existente. Worker continua NÃO CRÍTICA, mas essa release exige decisão humana por conter configuração de capacidade; API continua CRÍTICA e sempre humana. Nenhuma recategorização, exceção automática ou aprovação pelo agente. Aplicar somente após as CIs reais e decisões autenticadas. Não criar nós AWS para esta solicitação; verificar acomodação no cluster existente e registrar um bloqueio se faltar capacidade.
