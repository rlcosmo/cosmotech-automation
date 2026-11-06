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
