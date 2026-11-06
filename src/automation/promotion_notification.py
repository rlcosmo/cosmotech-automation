"""Notificação em português; o link nunca aprova ou executa deploy."""
from email.message import EmailMessage
from urllib.parse import urlsplit

from automation.promotion_gate import release_id


def notification(release: dict, ai: dict | None, *, recipient: str, approval_origin: str,
                 change_id: int | None = None, rcl_id: str | None = None,
                 application_version: str | None = None) -> EmailMessage:
    url = urlsplit(approval_origin)
    if url.scheme != 'https' or not url.hostname or url.username or url.password or url.query or url.fragment:
        raise ValueError('A origem de aprovação deve usar HTTPS sem credenciais')
    link = approval_origin.rstrip('/')+'/releases/'+release_id(release)
    identity=rcl_id or ('GLPI '+str(change_id) if change_id is not None else 'Revisão de release')
    version=application_version or release['commit'][:12]
    application={'orders-api':'Orders API','orders-worker':'Orders Worker'}[release['service']]
    message=EmailMessage()
    message['To']=recipient
    message['Subject']=f'{identity} | {application} | {version} | {release["environment"].upper()}'
    message.set_content(f'''{identity} — {application}
Versão candidata: {version}
Ambiente: {release['environment'].upper()}
Criticidade: {'CRÍTICA — aprovação humana obrigatória' if release['service']=='orders-api' else 'NÃO CRÍTICA — controles técnicos obrigatórios'}
Testes: {'aprovados' if release['checks']['tests_passed'] else 'reprovados'}
Trivy: {'aprovado' if release['checks']['scan_passed'] else 'reprovado'}
GMUD GLPI: https://glpi.cosmotechsolutions.com.br/front/change.form.php?id={change_id}
Pipeline CI: https://github.com/rlcosmo/cosmotech-{release['service']}/actions/runs/{release['run_id']}
Execução Kestra: {release['execution_id']}
Portal autenticado: {link}

Esta mensagem é uma notificação. Responder ao e-mail não aprova a mudança.
Consulte planos, evidências, decisão aplicada e elegibilidade de rollback no portal.
''')
    return message
