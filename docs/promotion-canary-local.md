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
