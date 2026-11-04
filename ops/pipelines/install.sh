#!/usr/bin/env bash
set -Eeuo pipefail
umask 077
install -d -m 0755 /opt/cosmotech/pipeline-events
cat > /etc/systemd/system/cosmotech-pipeline-evidence.service <<'UNIT'
[Unit]
Description=Read-only local CI stage evidence for Kestra
After=network.target
[Service]
User=nobody
Group=nogroup
ExecStart=/usr/bin/python3 /opt/cosmotech/pipelines/bridge.py serve
Restart=on-failure
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadOnlyPaths=/opt/cosmotech/pipeline-events
[Install]
WantedBy=multi-user.target
UNIT
chmod 0755 /opt/cosmotech/pipelines /opt/cosmotech/pipeline-events
chmod 0644 /opt/cosmotech/pipelines/bridge.py
systemctl daemon-reload
systemctl enable --now cosmotech-pipeline-evidence
python3 /opt/cosmotech/pipelines/bridge.py install
