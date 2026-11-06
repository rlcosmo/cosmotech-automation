# CosmoTech — Automação GLPI e Kestra

Este repositório concentra a integração de automação entre **GLPI** (gestão de chamados e mudanças) e **Kestra** (orquestração de fluxos). O motor de automação é independente; as contas pessoais e suas configurações não são alteradas por este projeto.

## Como utilizar

1. Consulte os arquivos e fluxos versionados neste repositório antes de executar qualquer automação.
2. Configure as integrações e permissões necessárias no ambiente autorizado, sem registrar credenciais no Git.
3. Mantenha senhas e tokens no **AWS Systems Manager Parameter Store (SSM)** ou no **AWS Secrets Manager**.
4. Verifique a instalação e as permissões do GitHub App antes de habilitar fluxos que dependam dele.
5. Valide os resultados no GLPI e no Kestra. **Execução técnica não equivale a aprovação humana**; confira a política de aprovação aplicável ao fluxo.

## Origem e cuidados

O projeto foi extraído de [aws-serverless-lab](https://github.com/rlcosmo/aws-serverless-lab) no commit `6f3e67f9e93d626393b23b6884b031767a63fea0`, preservando o histórico original. Os arquivos antigos permanecem no repositório de origem apenas como registros da migração. A extração **não recriou infraestrutura**.

> Não publique segredos, dados pessoais, identificadores de chamados reais ou informações internas nos exemplos e logs.
