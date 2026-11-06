# Correlação CI → entrega

O gate de releases publicava fatos na projeção da GMUD, mas não completava o registro `/var/lib/cosmotech-pipeline-events/<app>-<run>-<attempt>.json`. As antigas tarefas de acompanhamento expiravam em `gitops`, mesmo após entrega real concluída.

`DeliveryRuntime.record_stage` agora propaga publicação assinada para `gitops`, sincronização Argo para `rollout` e validação técnica para `health`. A identidade exige o mesmo commit, tentativa de CI e execução autorizadora. Não cria registros nem modifica tentativas terminais. Preparação Canary não comprova saúde final; rollback Canary produz falha. Falhas do gate passam ao observador em vez de deixá-lo aguardando.

A espera da etapa GitOps de aplicação permite até 24h para aprovação humana; as demais continuam limitadas a 30min. Os logs de timeout identificam aplicação, run, tentativa e etapa. Isso não modifica validade da evidência, aprovação, políticas nem a observação de 12h. A coleta após aprovação continua exigindo evidência atual.

Validação: suíte unitária e testes do bridge, incluindo duplicação, tentativa ausente, identidades divergentes, pré-requisitos não comprovados, sucesso de entrega e falha Canary. Falhas históricas no Kestra são preservadas. Para reverter esta correção, restaurar os arquivos copiados para o backup no host e reinstalar as revisões anteriores dos três flows; nenhuma alteração IAM ou Terraform é necessária.
