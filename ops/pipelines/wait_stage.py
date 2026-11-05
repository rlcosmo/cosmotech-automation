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
for _ in range(360):
    try:
        with urllib.request.urlopen(url, timeout=5) as response:
            record = json.load(response)
    except (OSError, ValueError):
        time.sleep(5)
        continue
    current = record.get('stages', {}).get(stage, {})
    status = current.get('state', 'pending')
    if status == 'success':
        print(json.dumps({'stage': stage, **current, 'source': record['url'], 'commit': record['sha'], 'cluster': record['cluster']}))
        break
    if status in {'failure', 'cancelled', 'skipped'} or record.get('state') in {'failure', 'cancelled'}:
        print(json.dumps({'stage': stage, 'state': status, 'pipeline_state': record.get('state'), 'source': record.get('url')}))
        raise SystemExit('Source pipeline did not successfully complete this stage')
    time.sleep(5)
else:
    raise SystemExit('Timed out waiting for actual CI evidence; no success was fabricated')
