"""Observe a real CI stage; never execute or approve a deployment."""
import json
import os
import re
import time
import urllib.request

app = os.environ['PIPELINE_APP']
run_id = os.environ['PIPELINE_RUN']
attempt = os.environ['PIPELINE_ATTEMPT']
stage = os.environ['PIPELINE_STAGE']
assert app in {'orders-api', 'orders-worker', 'gitops'}
assert re.fullmatch(r'[0-9]+', run_id) and re.fullmatch(r'[0-9]+', attempt)
assert re.fullmatch(r'[a-z_]+', stage)
url = f'http://127.0.0.1:8094/{app}/{run_id}/{attempt}'
timeout = 86400 if app != 'gitops' and stage == 'gitops' else 1800
deadline = time.monotonic() + timeout
last_state = 'pending'
while time.monotonic() < deadline:
    try:
        with urllib.request.urlopen(url, timeout=5) as response:
            record = json.load(response)
    except (OSError, ValueError):
        time.sleep(5)
        continue
    current = record.get('stages', {}).get(stage, {})
    status = current.get('state', 'pending')
    if status != last_state:
        print(json.dumps({'stage': stage, 'state': status, 'source': record.get('url'), 'release_execution_id': record.get('release_execution_id')}), flush=True)
        last_state = status
    if status == 'success':
        print(json.dumps({'stage': stage, **current, 'source': record['url'], 'commit': record['sha'], 'cluster': record['cluster']}))
        break
    if status in {'failure', 'cancelled', 'skipped'} or record.get('state') in {'failure', 'cancelled'}:
        print(json.dumps({'stage': stage, 'state': status, 'pipeline_state': record.get('state'), 'source': record.get('url')}))
        raise SystemExit('Source pipeline did not successfully complete this stage')
    time.sleep(5)
else:
    raise SystemExit(f'Timed out after {timeout}s waiting for {app} run {run_id} attempt {attempt} stage {stage}; last state={last_state}. Check the linked release/approval and evidence service; no success was fabricated')
