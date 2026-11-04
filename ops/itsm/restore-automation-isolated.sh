#!/usr/bin/env bash
# Restore the existing logical dump into a new, network-isolated test database.
set -Eeuo pipefail
umask 077
[[ "$EUID" == 0 ]] || exit 1
[[ "$(awk '/MemAvailable:/{print $2}' /proc/meminfo)" -ge 1048576 ]] || { echo 'Insufficient memory reserve'; exit 1; }
image=postgres@sha256:266d3c257594e195b309ae9d1805e54c788f1d7b375f56bf6c367aac6c917e7b
docker image inspect "$image" >/dev/null
backup_dir="$(find /opt/cosmotech/backups/logical -mindepth 1 -maxdepth 1 -type d | sort | tail -1)"
[[ -s "$backup_dir/kestra-automation.dump" && -f "$backup_dir/COMPLETE" ]]
(cd "$backup_dir" && sha256sum -c SHA256SUMS)
run_id="$(date -u +%Y%m%dT%H%M%SZ)"
restore_dir="/opt/cosmotech/backups/restore-tests/automation-$run_id"
container="cosmotech-automation-restore-$run_id"
volume="cosmotech-automation-restore-$run_id"
if docker container inspect "$container" >/dev/null 2>&1 || docker volume inspect "$volume" >/dev/null 2>&1; then
  echo 'Refusing to reuse an existing restore target'; exit 1
fi
install -d -m 0700 "$restore_dir"
python3 - "$restore_dir/postgres.env" <<'PY'
import secrets,sys
from pathlib import Path
Path(sys.argv[1]).write_text('POSTGRES_USER=kestra_automation\nPOSTGRES_DB=kestra_automation\nPOSTGRES_PASSWORD='+secrets.token_hex(32)+'\n')
PY
created=false
cleanup() { if [[ "$created" == true ]]; then docker stop "$container" >/dev/null 2>&1 || true; fi; }
trap cleanup EXIT
# No host port, host socket, live mount, or external network is available.
docker run -d --name "$container" --network none --memory 512m --cpus 0.5 --pids-limit 256 \
  --cap-drop NET_RAW --security-opt no-new-privileges:true --log-driver local \
  --label cosmotech.purpose=isolated-restore \
  --env-file "$restore_dir/postgres.env" \
  --mount "type=volume,source=$volume,target=/var/lib/postgresql/data" "$image" >/dev/null
created=true
for _ in $(seq 1 60); do
  if docker exec "$container" pg_isready -U kestra_automation -d kestra_automation >/dev/null 2>&1; then break; fi
  sleep 1
done
if ! docker exec -i "$container" pg_restore -U kestra_automation -d kestra_automation --exit-on-error \
  < "$backup_dir/kestra-automation.dump" > "$restore_dir/restore.stdout" 2> "$restore_dir/restore.stderr"; then
  echo 'Restore failed; private diagnostics retained'; exit 1
fi
docker exec "$container" psql -U kestra_automation -d kestra_automation -tAc \
  "SELECT json_build_object('tables',(SELECT count(*) FROM information_schema.tables WHERE table_schema='public'),'flows',(SELECT count(*) FROM flows),'executions',(SELECT count(*) FROM executions),'logs',(SELECT count(*) FROM logs));" > "$restore_dir/counts.json"
python3 - "$restore_dir" "$backup_dir" "$volume" <<'PY'
import json,sys
from pathlib import Path
root=Path(sys.argv[1]);counts=json.loads((root/'counts.json').read_text())
assert counts['tables']>0 and counts['flows']>0 and counts['executions']>0 and counts['logs']>0
result={'result':'passed','source_backup':Path(sys.argv[2]).name,'network':'none','host_ports':0,'live_data_modified':False,'restored_volume':sys.argv[3],**counts,'scope':'PostgreSQL logical state; engine startup and local storage artifact recovery not exercised'}
(root/'evidence.json').write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps(result))
PY
docker stop "$container" >/dev/null
created=false
# Retain only stopped test container/volume and protected evidence for review.
