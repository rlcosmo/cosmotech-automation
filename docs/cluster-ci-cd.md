# Esteiras por cluster

Todas as etapas da aplicação são observadas no Kestra público existente em https://kestra.cosmotechsolutions.com.br . Nenhuma aprovação humana é exigida nesta fase. A integração privada de automação continua existindo; os fluxos visíveis são publicados também no console público, sem trocar contas pessoais.

| Cluster | Namespace Kestra | Esteiras |
|---|---|---|
| aws-eks-istio-lab-dev | cosmotech.clusters.aws_eks_istio_lab_dev | orders_api_ci_cd, orders_worker_ci_cd |
| argocd-management-k3s | cosmotech.clusters.argocd_management | gitops_ci_cd |

As aplicações executam testes, build/scan, publicação ECR, promoção da configuração completa, rollout e health check. A esteira GitOps valida a entrega, observa a sincronização Argo e verifica HTTPS. Kestra acompanha evidências reais das Actions por SSM e recebe o resultado do rollout; sucesso do CI de origem não é confundido com sucesso da implantação. Cada execução aponta para a URL original nas Actions.

Configuração oficial: `deploy/dev` em cada repositório individual. GitOps recebe um espelho automático do SHA testado; não exige edição manual. Cada aplicação contém Deployment, ConfigMap, Service, HPA e Ingress. A promoção confere o conjunto completo e o digest imutável da imagem antes de merge automático.

As evidências ficam em `/var/lib/cosmotech-pipeline-events` no host ITSM. São metadados sem segredos, protegidos por lock e escrita atômica; o leitor local é somente leitura e roda como nobody. Dados de autenticação permanecem em arquivo root e não são acessíveis aos jobs de observação. Uma repetição de callback não cria outra execução e eventos atrasados não reescrevem estados terminais. Em retry somente de jobs falhos, pré-requisitos bem-sucedidos do mesmo run e SHA são reaproveitados com referência explícita à tentativa anterior. Falhas anteriores permanecem no histórico.

Após falha de entrega, corrigir a causa e repetir a esteira. Para ambiguidade de dispatch Kestra, reconciliar a execução com o run/attempt registrado antes de remover o marcador; não criar execução duplicada às cegas. Para indisponibilidade transitória do leitor, o observador usa retry limitado. Execuções de observação podem ser reiniciadas para ler evidências já existentes; isso não refaz o deploy nem fabrica sucesso de etapa.

Evidências de 2026-11-05:

- API: Actions 41974174691, tentativa 2, Kestra `3F2RKVVttlADcL9Xgsnx7q`, SUCCESS nas sete tarefas, incluindo rollout e health.
- Worker: Actions 41974177919, tentativa 2, Kestra `2IqKcrjQdumQtdr3vGVBII`, SUCCESS nas sete tarefas.
- Entrega GitOps: Actions 41975120785, Kestra `4SaZXnF1hmhUo020nIl9Hv`, SUCCESS.
- API e worker: uma réplica pronta, HPA de 1 a 4 réplicas e CPU alvo 70%; hosts `orders-api-dev.cosmotechsolutions.com.br` e `orders-worker-dev.cosmotechsolutions.com.br` responderam HTTPS 200.

O serviço do leitor e os fluxos estão nos snapshots versionados `ops/pipelines`. O documento `CosmoTech-Install-Cluster-Pipelines` no repositório infrastructure-iac distribui esse snapshot. Mantê-lo sincronizado com mudanças deste repositório. Retenção segue a configuração existente do banco Kestra e backups do host; arquivos locais de evidência não recebem remoção automática para preservar a correlação de releases ainda implantadas.
