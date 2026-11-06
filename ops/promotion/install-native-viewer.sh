#!/usr/bin/env bash
# Existing host only. Run after installing the reviewed Python source modules.
set -Eeuo pipefail
umask 077
[[ "$EUID" == 0 ]] || { echo 'Root on the authorized existing host is required'; exit 1; }
package_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
backup="/opt/cosmotech/promotion/backups/native-view-$(date -u +%Y%m%dT%H%M%SZ)"
install -d -m 0700 "$backup"
cp -p /etc/nginx/conf.d/cosmotech-itsm-public.conf "$backup/nginx.conf"
for file in cosmotech-kestra-viewer-http.conf cosmotech-kestra-viewer-locations.conf cosmotech-kestra-viewer-auth.map; do
    if [[ -f "/etc/nginx/$file" ]]; then cp -p "/etc/nginx/$file" "$backup/$file"; fi
done
python3 "$package_dir/render-viewer-auth.py"
install -o root -g root -m 0600 "$package_dir/kestra-viewer-http.conf" /etc/nginx/cosmotech-kestra-viewer-http.conf
install -o root -g root -m 0600 "$package_dir/kestra-viewer-locations.conf" /etc/nginx/cosmotech-kestra-viewer-locations.conf
python3 - <<'PY'
from pathlib import Path
p=Path('/etc/nginx/conf.d/cosmotech-itsm-public.conf');s=p.read_text()
http='include /etc/nginx/cosmotech-kestra-viewer-http*.conf;'
locations='    include /etc/nginx/cosmotech-kestra-viewer-locations*.conf;'
if http not in s:s=http+'\n\n'+s
marker='    server_name kestra.cosmotechsolutions.com.br;'
if locations not in s:
    assert s.count(marker)==1
    s=s.replace(marker,marker+'\n\n'+locations)
p.write_text(s)
PY
systemctl restart cosmotech-approval-portal
curl --retry 10 --retry-delay 1 --retry-connrefused --silent --fail http://127.0.0.1:8095/healthz >/dev/null
if ! nginx -t >"$backup/nginx-validation.log" 2>&1; then
    cp -p "$backup/nginx.conf" /etc/nginx/conf.d/cosmotech-itsm-public.conf
    for file in cosmotech-kestra-viewer-http.conf cosmotech-kestra-viewer-locations.conf cosmotech-kestra-viewer-auth.map; do
        if [[ -f "$backup/$file" ]]; then cp -p "$backup/$file" "/etc/nginx/$file"; else rm -f "/etc/nginx/$file"; fi
    done
    echo 'Native view proxy validation failed; original proxy configuration restored. Inspect private validation log.'
    exit 1
fi
systemctl reload nginx
printf '%s\n' 'Native view adapter active; original native administrator route preserved.'
