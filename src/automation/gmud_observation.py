"""Persistent samples for the real twelve-hour window; Kestra schedules every tick."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime
from itertools import pairwise

from automation.promotion_gate import canonical, instant

FIELDS = {'observed_at', 'current_digest', 'argo_synced', 'healthy', 'metrics_reliable', 'critical_incidents', 'errors'}


class ObservationWindow:
    def __init__(self, database: str):
        self.database = database
        with sqlite3.connect(database) as db:
            db.execute('CREATE TABLE IF NOT EXISTS gmud_observations (id TEXT, sampled_at TEXT, sample TEXT, PRIMARY KEY(id,sampled_at))')

    def record(self, change: dict, sample: dict, *, now: datetime) -> None:
        if change['phase'] != 'OBSERVING' or set(sample) != FIELDS:
            raise PermissionError('Only complete observations of active windows are accepted')
        if not 0 <= (now-instant(sample['observed_at'])).total_seconds() <= 90:
            raise PermissionError('Observation is stale or from the future')
        if (any(type(sample[k]) is not bool for k in ['argo_synced','healthy','metrics_reliable'])
                or type(sample['critical_incidents']) is not int or sample['critical_incidents'] < 0
                or not isinstance(sample['errors'], list)):
            raise ValueError('Unknown health signals cannot be counted as success')
        with sqlite3.connect(self.database) as db:
            db.execute('INSERT OR IGNORE INTO gmud_observations VALUES (?,?,?)',
                       (change['id'], sample['observed_at'], json.dumps(sample)))

    def completion(self, change: dict, *, now: datetime) -> dict:
        if change['phase'] != 'OBSERVING' or change['rollback'] or change['errors']:
            raise PermissionError('Change is not eligible for successful closure')
        start, end = instant(change['validated_at']), instant(change['rollback_until'])
        if (end-start).total_seconds() != 43200 or now < end:
            raise PermissionError('The full twelve-hour window has not elapsed')
        with sqlite3.connect(self.database) as db:
            rows = [json.loads(row[0]) for row in db.execute(
                'SELECT sample FROM gmud_observations WHERE id=? ORDER BY sampled_at', (change['id'],))]
        samples = [s for s in rows if start <= instant(s['observed_at']) <= now]
        if not samples:
            raise PermissionError('No independently collected observations')
        times = [instant(s['observed_at']) for s in samples]
        gaps = [(times[0]-start).total_seconds(), (now-times[-1]).total_seconds(),
                *[(right-left).total_seconds() for left,right in pairwise(times)]]
        if times[-1] < end or max(gaps) > 180:
            raise PermissionError('Observation gaps require manual analysis; do not auto-close')
        if any(s['current_digest'] != change['release']['digest'] or not s['argo_synced']
               or not s['healthy'] or not s['metrics_reliable'] or s['critical_incidents'] != 0
               or s['errors'] for s in samples):
            raise PermissionError('An observed error, rollback or superseding release prevents successful closure')
        return {'continuous_observation_verified':True, 'samples':len(samples),
                'start':start.isoformat(), 'end':end.isoformat(), 'max_gap_seconds':max(gaps),
                'observations_sha256':hashlib.sha256(canonical(samples)).hexdigest()}
