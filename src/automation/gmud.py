"""Native GLPI Change journal and deterministic policies; no deployment authority.

The host supplies protected configuration and an HMAC key. CI cannot provide a
classification or a history result. External effects are journalled before I/O;
ambiguous creates/dispatches require reconciliation, never blind replay.
"""
from __future__ import annotations

import copy
import hashlib
import hmac
import html
import json
import re
import sqlite3
import stat
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from automation.bridge import GLPIClient, PreformattedRecords, request_json
from automation.promotion_gate import canonical, evaluate, instant

CRITICAL = 'CRITICAL'
NON_CRITICAL = 'NON_CRITICAL'
CUSTOM_CRITICAL = 'CUSTOM_CRITICAL'
CATEGORIES = {CRITICAL, NON_CRITICAL, CUSTOM_CRITICAL}
NATIVE_STATUS = {'CREATED': 1, 'HUMAN_REVIEW': 10, 'APPROVED': 7, 'DEPLOYING': 11,
                 'OBSERVING': 8, 'CLOSED_SUCCESS': 6, 'FAILED': 8, 'REJECTED': 13, 'ROLLED_BACK': 14,
                 'ROLLBACK_REQUESTED': 8, 'ROLLBACK_RUNNING': 8}
TERMINAL = {'CLOSED_SUCCESS', 'FAILED', 'ROLLED_BACK'}
BINDING = ('service', 'environment', 'commit', 'digest', 'run_id')


def change_identity(release: dict) -> str:
    """Stable across re-collection; the first Kestra execution owns this release."""
    if (release.get('service') not in {'orders-api', 'orders-worker'}
            or release.get('environment') != 'dev'
            or not re.fullmatch('[a-f0-9]{40}', release.get('commit', ''))
            or not re.fullmatch('sha256:[a-f0-9]{64}', release.get('digest', ''))
            or not re.fullmatch('[0-9]{1,20}', release.get('run_id', ''))):
        raise ValueError('Invalid immutable release binding')
    return hashlib.sha256(canonical({k: release[k] for k in BINDING})).hexdigest()


class ProtectedPolicy:
    def __init__(self, configuration: dict):
        self.configuration = copy.deepcopy(configuration)
        if (set(configuration) != {'version', 'applications', 'rollback_hours'}
                or configuration['version'] != 'cosmotech-gmud-v1'
                or configuration['rollback_hours'] != 12):
            raise ValueError('Unknown protected policy')
        if not configuration['applications'] or any(v not in CATEGORIES for v in configuration['applications'].values()):
            raise ValueError('Invalid criticality registry')

    @classmethod
    def load(cls, path: Path) -> ProtectedPolicy:
        """Runtime config must be root-owned, regular and not group/world writable."""
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
            raise PermissionError('Criticality configuration is not protected')
        return cls(json.loads(path.read_text()))

    def category(self, release: dict) -> str:
        return self.configuration['applications'][release['service'] + '/' + release['environment']]

    def decide(self, release: dict, history: list[dict], *, now: datetime) -> tuple[str, str]:
        technical, reason = evaluate(release, None, now=now, ai_enabled=False)
        if technical in {'BLOCK', 'REJECT'}:
            return technical, reason
        category = self.category(release)
        if category == CRITICAL:
            return 'HUMAN_REVIEW', 'critical_change'
        # Existing sensitive-change/dependency controls still require a human.
        if release['checks']['sensitive_changes'] or not release['checks']['dependencies_reviewed']:
            return 'HUMAN_REVIEW', 'sensitive_change_or_unreviewed_dependencies'
        if category == NON_CRITICAL:
            return 'APPROVE', 'non_critical_technical_controls_passed'
        try:
            rows = [r for r in history if r['release']['service'] == release['service']
                    and r['release']['environment'] == release['environment']
                    and r['synthetic'] is False and r['phase'] in TERMINAL]
            rows.sort(key=lambda r: (instant(r['completed_at']), r['change_id']), reverse=True)
            latest = rows[:5]
            if len(latest) != 5:
                return 'HUMAN_REVIEW', 'five_completed_changes_required'
            valid = (len({r['id'] for r in latest}) == 5 and len({r['change_id'] for r in latest}) == 5
                     and all(r['phase'] == 'CLOSED_SUCCESS' and r['outcome'] == 'SUCCESS'
                        and r['errors'] == [] and r['rollback'] is False
                        and r['native_status'] == 6
                        and instant(r['validated_at']) + timedelta(hours=12) <= instant(r['completed_at']) <= now
                        for r in latest))
            return ('APPROVE', 'five_verified_successes') if valid else ('HUMAN_REVIEW', 'history_contains_failure_or_rollback')
        except (KeyError, TypeError, ValueError):
            return 'HUMAN_REVIEW', 'history_incomplete_or_unauditable'



class GLPIChanges(GLPIClient):
    """Installed GLPI high-level API; native status IDs checked by read-after-write."""
    def api(self, suffix: str = '', *, body: dict | None = None, method: str = 'GET') -> Any:
        return request_json(self.base + '/api.php/v2/Assistance/Change' + suffix,
                            headers={**self._headers(), **({'Content-Type': 'application/json'} if body is not None else {})},
                            data=json.dumps(body).encode() if body is not None else None, method=method)

    def all_changes(self) -> list[dict]:
        result, seen = [], set()
        for start in range(0, 10000, 20):
            page = self.api(f'?start={start}&limit=20&sort=id:asc')
            if not isinstance(page, list) or any(type(r.get('id')) is not int or r['id'] in seen for r in page):
                raise PermissionError('Ambiguous or incomplete GLPI pagination')
            result.extend(page)
            seen.update(r['id'] for r in page)
            if len(page) < 20:
                return result
        raise PermissionError('History exceeds bounded scan; manual review required')

    def create(self, name: str, content: str) -> int:
        value = self.api(body={'name': name, 'content': content, 'urgency': 2, 'impact': 2}, method='POST')
        if not isinstance(value, dict) or type(value.get('id')) is not int:
            raise PermissionError('Ambiguous GLPI creation')
        return value['id']

    def write(self, identifier: int, content: str, native_status: int) -> dict:
        self.api('/' + str(identifier), body={'content': content, 'status': {'id': native_status}}, method='PATCH')
        current = self.get_change(identifier)
        wanted, observed = PreformattedRecords(), PreformattedRecords()
        wanted.feed(content)
        observed.feed(current['content'])
        if current['status']['id'] != native_status or wanted.records != observed.records:
            raise PermissionError('GLPI did not persist the requested native state')
        return current


class ChangeJournal:
    """Signed GLPI state, durable local intentions and serialized per-release effects."""
    def __init__(self, database: str, glpi, key: bytes, policy: ProtectedPolicy):
        if len(key) < 32:
            raise ValueError('Dedicated journal signing key required')
        self.database, self.glpi, self.key, self.policy = database, glpi, key, policy
        with self.db() as db:
            db.execute('CREATE TABLE IF NOT EXISTS gmud (id TEXT PRIMARY KEY, change_id INTEGER, state TEXT, record TEXT, pending TEXT)')
            db.execute('CREATE TABLE IF NOT EXISTS rollback_requests (id TEXT PRIMARY KEY, record TEXT, state TEXT)')

    def db(self):
        db = sqlite3.connect(self.database, timeout=30)
        db.execute('PRAGMA busy_timeout=30000')
        return db

    def encode(self, record: dict) -> str:
        envelope = {'cosmotech_gmud': record, 'signature': hmac.new(self.key, canonical(record), hashlib.sha256).hexdigest()}
        return '<pre>' + html.escape(json.dumps(envelope, sort_keys=True)) + '</pre>'

    def decode(self, change: dict) -> dict:
        parser = PreformattedRecords()
        parser.feed(change['content'])
        rows = [r for r in parser.records if 'cosmotech_gmud' in r]
        if len(rows) != 1:
            raise PermissionError('GLPI record lacks a unique auditable change envelope')
        envelope = rows[0]
        record = envelope['cosmotech_gmud']
        expected = hmac.new(self.key, canonical(record), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(envelope.get('signature', ''), expected):
            raise PermissionError('GLPI evidence was modified outside the gate')
        if (record['id'] != change_identity(record['release']) or change.get('is_deleted') is not False
                or change['status']['id'] != NATIVE_STATUS[record['phase']]):
            raise PermissionError('Native GLPI state and signed journal disagree')
        return dict(record, change_id=change['id'], native_status=change['status']['id'])

    def history(self) -> list[dict]:
        # Never use local SQLite as an authoritative substitute for GLPI history.
        rows = [self.decode(row) for row in self.glpi.all_changes()
                if row.get('name', '').startswith('COSMOTECH GMUD ')]
        if len({row['id'] for row in rows}) != len(rows):
            raise PermissionError('Duplicate release identities in GLPI; history is not auditable')
        with self.db() as db:
            known = dict(db.execute('SELECT id,change_id FROM gmud WHERE change_id IS NOT NULL'))
        if any(row['id'] in known and known[row['id']] != row['change_id'] for row in rows):
            raise PermissionError('Native GLPI identity differs from the durable creation journal')
        return rows

    def ensure(self, release: dict, *, owner: str, ci_links: list[str],
               implementation_plan: str, rollback_plan: str, now: datetime, synthetic: bool = False) -> dict:
        identity = change_identity(release)
        if not owner or not ci_links or not implementation_plan or not rollback_plan or type(synthetic) is not bool:
            raise ValueError('Incomplete mandatory GMUD metadata')
        record = {'schema': 'cosmotech-gmud-v1', 'id': identity, 'release': release,
                  'criticality': self.policy.category(release), 'owner': owner, 'ci_links': ci_links,
                  'execution_plan': implementation_plan, 'rollback_plan': rollback_plan,
                  'kestra_execution_id': release['execution_id'],
                  'kestra_url': 'https://kestra.cosmotechsolutions.com.br/ui/executions/main/cosmotech.promotion/promotion_gate/' + release['execution_id'],
                  'argo_application': release['service'] + '-dev',
                  'synthetic': synthetic, 'phase': 'CREATED', 'outcome': None, 'errors': [],
                  'rollback': False, 'validated_at': None, 'completed_at': None,
                  'rollback_until': None, 'events': [{'type': 'created', 'at': now.isoformat()}]}
        # Persist creation intent before any mutating request. Reads may safely retry.
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT change_id,state,record FROM gmud WHERE id=?', (identity,)).fetchone()
            if row and row[0]:
                current = self.read(identity)
                if (current['kestra_execution_id'] != release['execution_id']
                        or current['release']['current_digest'] != release['current_digest']):
                    raise PermissionError('Original Kestra execution owns this GMUD')
                return current
            if row:
                raise PermissionError('Ambiguous creation; reconcile GLPI before retry')
            matches = [r for r in self.history() if r['id'] == identity]
            if len(matches) > 1:
                raise PermissionError('Duplicate GMUD detected')
            if matches:
                found = matches[0]
                if found['kestra_execution_id'] != release['execution_id']:
                    raise PermissionError('Original Kestra execution owns this GMUD')
                db.execute('INSERT INTO gmud VALUES (?,?,?,?,NULL)',
                           (identity, found['change_id'], 'ready', json.dumps({k:v for k,v in found.items() if k not in {'change_id','native_status'}})))
                return found
            db.execute('INSERT INTO gmud VALUES (?,NULL,?,?,NULL)', (identity, 'creating', json.dumps(record)))
        identifier = self.glpi.create('COSMOTECH GMUD ' + identity, self.encode(record))
        with self.db() as db:
            db.execute('UPDATE gmud SET change_id=?,state=? WHERE id=?', (identifier, 'ready', identity))
        return self.read(identity)

    def read(self, identity: str) -> dict:
        with self.db() as db:
            row = db.execute('SELECT change_id,state,record FROM gmud WHERE id=?', (identity,)).fetchone()
        if not row or row[0] is None or row[1] != 'ready':
            raise PermissionError('GMUD has unresolved or missing effects')
        observed = self.decode(self.glpi.get_change(row[0]))
        local = json.loads(row[2])
        if {k:v for k,v in observed.items() if k not in {'change_id','native_status'}} != local:
            raise PermissionError('GLPI journal replay or unacknowledged update detected')
        return observed

    def transition(self, identity: str, phase: str, *, now: datetime, details: dict | None = None) -> dict:
        details = copy.deepcopy(details or {})
        allowed = {'CREATED': {'HUMAN_REVIEW', 'APPROVED', 'FAILED', 'REJECTED'},
                   'HUMAN_REVIEW': {'APPROVED', 'FAILED', 'REJECTED'}, 'APPROVED': {'DEPLOYING', 'FAILED'},
                   'DEPLOYING': {'OBSERVING', 'FAILED', 'ROLLBACK_RUNNING'},
                   'OBSERVING': {'CLOSED_SUCCESS', 'FAILED', 'ROLLBACK_REQUESTED'},
                   'ROLLBACK_REQUESTED': {'ROLLBACK_RUNNING'}, 'ROLLBACK_RUNNING': {'ROLLED_BACK', 'FAILED'}}
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT change_id,state,record FROM gmud WHERE id=?', (identity,)).fetchone()
            if not row or row[1] != 'ready':
                raise PermissionError('Concurrent or ambiguous GMUD operation')
            record = self.decode(self.glpi.get_change(row[0]))
            record.pop('change_id');record.pop('native_status')
            if record != json.loads(row[2]):
                raise PermissionError('GLPI journal differs from the acknowledged state')
            if phase not in allowed.get(record['phase'], set()):
                raise PermissionError('Invalid or replayed GMUD transition')
            if phase == 'OBSERVING':
                if details.get('technical_validation') is not True:
                    raise PermissionError('Deploy completion is not technical validation')
                record.update(validated_at=now.isoformat(), rollback_until=(now + timedelta(hours=12)).isoformat())
            if phase == 'CLOSED_SUCCESS':
                if (record['rollback'] or record['errors'] or now < instant(record['rollback_until'])
                        or details.get('continuous_observation_verified') is not True):
                    raise PermissionError('Twelve-hour clean observation not proven')
                record.update(outcome='SUCCESS', completed_at=now.isoformat())
            if phase in {'ROLLBACK_REQUESTED','ROLLBACK_RUNNING','ROLLED_BACK'}:
                record['rollback'] = True
            if phase == 'ROLLED_BACK':
                if details.get('rollback_validated') is not True:
                    raise PermissionError('Rollback completion is unverified')
                record.update(outcome='ROLLED_BACK', completed_at=now.isoformat())
            if phase in {'FAILED', 'REJECTED'}:
                if not details.get('reason'):
                    raise ValueError('Actionable failure reason required')
                record['errors'].append(details['reason'])
                record.update(outcome=phase, completed_at=now.isoformat())
            record['phase'] = phase
            record['events'].append({'type': phase.lower(), 'at': now.isoformat(), 'details': details})
            db.execute('UPDATE gmud SET state=?,pending=? WHERE id=?', ('updating', json.dumps(record), identity))
        self.glpi.write(row[0], self.encode(record), NATIVE_STATUS[phase])
        with self.db() as db:
            db.execute('UPDATE gmud SET state=?,record=?,pending=NULL WHERE id=?', ('ready', json.dumps(record), identity))
        return self.read(identity)

    def request_rollback(self, identity: str, *, principal: str, reason: str, now: datetime,
                         allowed_principals: set[str], current_digest: str) -> dict:
        """Called only after portal authentication/CSRF; never from caller identity claims."""
        if principal not in allowed_principals or not isinstance(reason, str) or not 8 <= len(reason.strip()) <= 2000:
            raise PermissionError('Authorized responsible identity and justification required')
        current = self.read(identity)
        if (current['phase'] != 'OBSERVING' or not instant(current['validated_at']) <= now < instant(current['rollback_until'])
                or current_digest != current['release']['digest']):
            raise PermissionError('Expired, superseded or inactive release; a new GMUD/manual analysis is required')
        intent = {'principal': principal, 'reason': reason.strip(), 'at': now.isoformat(),
                  'release_id': identity, 'expected_digest': current_digest}
        with self.db() as db:
            try:
                db.execute('INSERT INTO rollback_requests VALUES (?,?,?)', (identity, json.dumps(intent), 'requested'))
            except sqlite3.IntegrityError as error:
                raise PermissionError('Rollback already requested; no duplicate/concurrent dispatch') from error
        return self.transition(identity, 'ROLLBACK_REQUESTED', now=now, details=intent)

    def reconcile(self, identity: str) -> dict:
        """Acknowledge an observed completed effect; never issue another POST/PATCH."""
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT change_id,state,record,pending FROM gmud WHERE id=?', (identity,)).fetchone()
            if row is None:
                raise ValueError('Unknown GMUD intention')
            if row[1] == 'ready':
                return self.read(identity)
            if row[1] == 'creating':
                matches = [r for r in self.history() if r['id'] == identity]
                if len(matches) != 1:
                    raise PermissionError('Creation outcome remains ambiguous')
                observed = matches[0]
                expected = json.loads(row[2])
            else:
                observed = self.decode(self.glpi.get_change(row[0]))
                expected = json.loads(row[3])
            unsigned = {k:v for k,v in observed.items() if k not in {'change_id', 'native_status'}}
            if unsigned != expected:
                raise PermissionError('Observed GLPI record does not match the intended effect')
            db.execute('UPDATE gmud SET change_id=?,state=?,record=?,pending=NULL WHERE id=?',
                       (observed['change_id'], 'ready', json.dumps(expected), identity))
        return self.read(identity)
