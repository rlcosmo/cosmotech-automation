#!/usr/bin/env bash
# Deploy only the decision recorder. No image/GitOps/SQS promotion capability.
set -Eeuo pipefail
umask 077
[[ "$EUID" == 0 ]] || { echo 'Run as the authorized host administrator'; exit 1; }
archive="${1:?Trusted source archive is required}"
package_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
python=/opt/cosmotech/promotion/venv/bin/python
[[ -x "$python" ]] || { echo 'Prepare the isolated Python 3.12 runtime first'; exit 1; }
"$python" -c 'import boto3,cryptography; from botocore.session import Session; assert "Converse" in Session().get_service_model("bedrock-runtime").operation_names'
id cosmotech-promotion >/dev/null 2>&1 || useradd --system --no-create-home --shell /usr/sbin/nologin cosmotech-promotion
install -d -m 755 /opt/cosmotech/promotion/source /etc/cosmotech/promotion
chmod 751 /opt/cosmotech/promotion
# Traverse these parents without granting directory-list access to other users.
chmod o+x /opt/cosmotech
install -d -m 751 /var/lib/cosmotech-promotion
install -d -m 700 -o cosmotech-promotion -g cosmotech-promotion /var/lib/cosmotech-promotion/portal
"$python" - "$archive" <<'PY'
import json,os,pwd,secrets,sys,tarfile
from pathlib import Path
with tarfile.open(sys.argv[1]) as archive:
    if any(not m.name.startswith('automation/') or not m.isfile() or not m.name.endswith('.py') for m in archive.getmembers()):
        raise SystemExit('Only reviewed Python module files are accepted')
    archive.extractall('/opt/cosmotech/promotion/source',filter='data')
for path in Path('/opt/cosmotech/promotion/source').rglob('*'):
    path.chmod(0o755 if path.is_dir() else 0o644)
values=dict(line.split('=',1) for line in Path('/opt/cosmotech/secrets/itsm/runtime.env').read_text().splitlines()
            if '=' in line and not line.startswith('#'))
key=Path('/var/lib/cosmotech-promotion/portal/csrf.key')
if not key.exists():key.write_bytes(secrets.token_bytes(32))
identity=pwd.getpwnam('cosmotech-promotion')
os.chown(key,identity.pw_uid,identity.pw_gid);key.chmod(0o600)
config={'database':'/var/lib/cosmotech-promotion/portal/approvals.db','csrf_key':str(key),
        'allowed_principals':[values['KESTRA_ADMIN_USERNAME']],
        'kestra_origin':'https://kestra.cosmotechsolutions.com.br',
        'public_origin':'https://kestra.cosmotechsolutions.com.br'}
path=Path('/etc/cosmotech/promotion/portal.json');path.write_text(json.dumps(config));path.chmod(0o644)
PY
chmod -R a+rX /opt/cosmotech/promotion/venv
install -m 644 "$package_dir/approval-portal.service" /etc/systemd/system/cosmotech-approval-portal.service
"$python" - "$package_dir/approval-location.conf" <<'PY'
from pathlib import Path
import sys
path=Path('/etc/nginx/conf.d/cosmotech-itsm-public.conf')
text=path.read_text();marker='    server_name kestra.cosmotechsolutions.com.br;'
if 'proxy_pass http://127.0.0.1:8095;' not in text:
    if text.count(marker)!=1:raise SystemExit('Unexpected proxy configuration')
    backup=Path('/opt/cosmotech/promotion/nginx-before-approval.conf')
    if not backup.exists():backup.write_text(text)
    path.write_text(text.replace(marker,marker+'\n'+Path(sys.argv[1]).read_text()))
PY
if ! nginx -t; then
    cp /opt/cosmotech/promotion/nginx-before-approval.conf /etc/nginx/conf.d/cosmotech-itsm-public.conf
    exit 1
fi
systemctl daemon-reload
systemctl enable cosmotech-approval-portal
systemctl restart cosmotech-approval-portal
curl --retry 10 --retry-delay 1 --retry-connrefused --silent --fail http://127.0.0.1:8095/healthz
systemctl reload nginx
systemctl is-active cosmotech-approval-portal
