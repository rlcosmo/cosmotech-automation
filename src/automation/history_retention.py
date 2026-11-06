"""Kestra reconciliation of the protected ReplicaSet retention standard.

No Kubernetes patch, image promotion, approval or resource deletion is performed.
Only the exclusive signed publisher may change revisionHistoryLimit in GitOps.
"""
from __future__ import annotations

import copy
import json
import re

from automation.controlled_publisher import ControlledPublisher
from automation.gitops_publisher import verify_commit
from automation.image_references import pinned_reference


def reconcile(runtime) -> list[dict]:
    enabled = runtime.config.get('revision_history_limit')
    if enabled is None:
        return []
    if type(enabled) is not int or enabled != 2:
        raise PermissionError('Protected retention policy must retain exactly two histories')
    records = runtime.changes.history()  # Native signed GLPI history; fail closed.
    applications = runtime.policy.policy.configuration['applications']
    results = []
    for key in sorted(applications):
        service, environment = key.split('/')
        if environment != 'dev' or not re.fullmatch(r'orders-(api|worker)', service):
            continue
        git = runtime.publisher()
        if git.git('status', '--porcelain'):
            raise PermissionError('Retention publisher checkout is not clean')
        git.git('fetch', '--quiet', 'origin', 'main')
        parent = git.git('rev-parse', 'origin/main')
        verify_commit(git.repo, parent, git.fingerprint, env=git.git_env)
        prefix = 'gitops/apps/' + service + '/dev/'
        main = json.loads(git.git('show', parent + ':' + prefix + 'deployment.json'))
        image = next(c['image'] for c in main['spec']['template']['spec']['containers'] if c['name'] == service)
        expected = pinned_reference(image).split('@')[-1]
        candidates = [c for c in records if c['release']['service'] == service
                      and c['release']['environment'] == environment
                      and c['phase'] in {'DEPLOYING', 'OBSERVING', 'CLOSED_SUCCESS', 'ROLLED_BACK'}
                      and expected in {c['release']['digest'], c['release']['current_digest']}
                      and any(e['type'] == 'approved' for e in c.get('events', []))]
        if not candidates:
            results.append({'application': service, 'retention': 'blocked', 'reason': 'No approved current GMUD context'})
            continue
        change = max(candidates, key=lambda c: c['change_id'])
        names = ['deployment.json']
        if service == 'orders-worker' and git.git('ls-tree', '--name-only', parent, prefix + 'canary-deployment.json'):
            names += ['canary-deployment.json']
        resources = {}
        changed = False
        for name in names:
            original = main if name == 'deployment.json' else json.loads(git.git('show', parent + ':' + prefix + name))
            changed |= original['spec'].get('revisionHistoryLimit', 10) != 2
            proposed = copy.deepcopy(original)
            proposed['spec']['revisionHistoryLimit'] = 2
            resources[name] = proposed
        if not changed:
            continue
        revision = ControlledPublisher(git, runtime.changes, runtime.gate.database).publish(
            change['id'], 'history-limit:2', resources, expected_digest=expected)
        results.append({'application': service, 'retention': 'published',
                        'gitops_revision': revision, 'history_limit': 2,
                        'gmud_context': change.get('rcl_id'), 'files': names})
    return results
