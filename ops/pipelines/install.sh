#!/usr/bin/env bash
set -Eeuo pipefail
umask 077
install -d -m 0755 /var/lib/cosmotech-pipeline-events /usr/local/lib/cosmotech-pipelines
if [[ -d /opt/cosmotech/pipeline-events ]]; then
  cp -a --update=none /opt/cosmotech/pipeline-events/. /var/lib/cosmotech-pipeline-events/
fi
install -m 0644 /opt/cosmotech/pipelines/bridge.py /usr/local/lib/cosmotech-pipelines/bridge.py
# The public engine runs as UID 1000; preserve its existing identity and mounted path.
install -d -m 0750 -o 1000 -g 1000 /opt/cosmotech/itsm/tmp /opt/cosmotech/itsm/tmp/tmp
cat > /etc/systemd/system/cosmotech-pipeline-evidence.service <<'UNIT'
[Unit]
Description=Read-only local CI stage evidence for Kestra
After=network.target
[Service]
User=nobody
Group=nogroup
ExecStart=/usr/bin/python3 /usr/local/lib/cosmotech-pipelines/bridge.py serve
Restart=on-failure
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadOnlyPaths=/var/lib/cosmotech-pipeline-events
[Install]
WantedBy=multi-user.target
UNIT
chmod 0755 /opt/cosmotech/pipelines /var/lib/cosmotech-pipeline-events
chmod 0644 /opt/cosmotech/pipelines/bridge.py
systemctl daemon-reload
systemctl enable cosmotech-pipeline-evidence
systemctl restart cosmotech-pipeline-evidence
python3 /opt/cosmotech/pipelines/bridge.py install
