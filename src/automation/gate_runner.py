"""Fixed SSM entry point for the pinned Kestra gate and persistent reconciler."""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import re
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from cryptography.hazmat.primitives import serialization

from automation.approval_portal import ApprovalPortal
from automation.github_access import GitHubApp
from automation.gitops_publisher import SignedGitPublisher, validate_bundle
from automation.gmud import ChangeJournal, GLPIChanges, ProtectedPolicy, change_identity
from automation.live_evidence import KubeClient, LiveEvidence, client
from automation.promotion_coordinator import PromotionCoordinator
from automation.promotion_gate import PromotionGate, release_id
from automation.promotion_runtime import KestraClient, SnsApprovalNotifier
from automation.trusted_evidence import collect

ROOT=Path('/var/lib/cosmotech-promotion/authority')


class EffectivePolicy:
    def __init__(self, policy, kube):
        self.policy,self.kube=policy,kube

    def category(self, release):
        return self.policy.category(release)

    def decide(self, release, history, *, now):
        result=self.policy.decide(release,history,now=now)
        if result[0]=='APPROVE' and release['service']=='orders-worker':
            rows=self.kube.query('cosmotech_worker_heartbeat_timestamp_seconds{channel="stable",version="'+release['current_digest']+'"}')
            if not rows:
                return 'HUMAN_REVIEW','initial_observability_bootstrap_requires_human'
        return result


class Runtime:
    def __init__(self):
        config_path=Path('/etc/cosmotech/promotion/gate.json')
        info=config_path.lstat()
        if info.st_uid!=0 or info.st_mode&0o022 or config_path.is_symlink():
            raise PermissionError('Gate configuration is not protected')
        self.config=json.loads(config_path.read_text())
        values=dict(line.split('=',1) for line in Path('/opt/cosmotech/secrets/itsm/runtime.env').read_text().splitlines()
                    if '=' in line and not line.startswith('#'))
        self.kestra=KestraClient('https://kestra.cosmotechsolutions.com.br',values['KESTRA_ADMIN_USERNAME'],values['KESTRA_ADMIN_PASSWORD'])
        self.ssm=client('ssm')
        self.kube=KubeClient(client('eks'),cluster=self.config['cluster'])
        self.live=LiveEvidence(self.config,ssm=self.ssm,secrets=client('secretsmanager'),ecr=client('ecr'),kube=self.kube)
        credentials=json.loads(self.ssm.get_parameter(Name='/cosmotech/itsm/glpi/automation-credentials',WithDecryption=True)['Parameter']['Value'])
        self.policy=EffectivePolicy(ProtectedPolicy.load(Path('/etc/cosmotech/promotion/gmud-policy.json')),self.kube)
        if self.policy.policy.configuration['applications']['orders-api/dev']!='CRITICAL':
            raise PermissionError('Orders API must retain critical classification')
        self.changes=ChangeJournal(str(ROOT/'gmud.db'),GLPIChanges(credentials),(ROOT/'gmud.key').read_bytes(),self.policy)
        self.key=serialization.load_pem_private_key((ROOT/'receipt.pem').read_bytes(),password=None)
        self.gate=PromotionGate(str(ROOT/'gate.db'),self.key,set(self.config['allowed_principals']),ai_enabled=False)
        portal=json.loads(Path('/etc/cosmotech/promotion/portal.json').read_text())
        self.portal=ApprovalPortal(portal['database'],Path(portal['csrf_key']).read_bytes(),
            set(portal['allowed_principals']),lambda *args:False,portal['public_origin'])
        self.notifier=SnsApprovalNotifier(client('sns'),self.config['sns_topic'],self.config['recipient'],portal['public_origin'])
        with sqlite3.connect(self.gate.database) as db:
            db.execute('CREATE TABLE IF NOT EXISTS notifications (id TEXT PRIMARY KEY,state TEXT,message_id TEXT)')
        (ROOT/'executions').mkdir(mode=0o700,exist_ok=True)

    def path(self, execution: str) -> Path:
        if not re.fullmatch('[A-Za-z0-9_-]{1,100}',execution):raise ValueError('Invalid execution reference')
        return ROOT/'executions'/(execution+'.json')

    def execution(self, identifier: str, *, scheduler=False) -> dict:
        execution=self.kestra.request('/api/v1/main/executions/'+identifier)
        flow='promotion_reconcile' if scheduler else 'promotion_gate'
        revision=self.config['scheduler_revision' if scheduler else 'flow_revision']
        if (execution.get('namespace')!='cosmotech.promotion' or execution.get('flowId')!=flow
                or execution.get('flowRevision')!=revision or execution['state']['current']!='RUNNING'):
            raise PermissionError('Only the pinned running Kestra flow can invoke this stage')
        return execution

    def collect(self, identifier: str) -> dict:
        execution=self.execution(identifier)
        request={**execution['inputs'],'execution_id':identifier}
        github=GitHubApp(self.live.credentials,'cosmotech-'+request['service'])
        captured={}
        def runtime(service):
            captured.update(self.live.runtime(service));return captured
        release,bundle=collect(request,now=datetime.now(UTC),github=github,ecr=self.live.ecr,runtime=runtime,
            workflow_hash=self.config['workflow_hashes'][request['service']],fingerprint=self.config['gpg_fingerprint'],account=self.config['account'])
        if release['digest']==release['current_digest']:raise PermissionError('A distinct tested candidate digest is required')
        validate_bundle(release,bundle)
        path=self.path(identifier)
        if path.exists():
            previous=json.loads(path.read_text())
            if release_id(previous['release'])!=release_id(release):raise PermissionError('Immutable release target changed')
            previous.update(release=release,bundle=bundle)
            state=previous
        else:
            state={'release':release,'bundle':bundle,'before_bundle':{'resources':captured['resources']},
                   'before_revision':captured['revision']}
        path.write_text(json.dumps(state));path.chmod(0o600)
        return {'release_id':release_id(release),'version':release['commit'][:12],'evidence_sha256':hashlib.sha256(path.read_bytes()).hexdigest()}

    def policy_stage(self, identifier: str) -> dict:
        self.execution(identifier)
        state=json.loads(self.path(identifier).read_text());release=state['release'];now=datetime.now(UTC)
        record=self.changes.ensure(release,owner=self.config['owner'],
            ci_links=['https://github.com/rlcosmo/cosmotech-'+release['service']+'/actions/runs/'+release['run_id']],
            implementation_plan=('Signed GitOps deployment and exact Argo/health validation. Initial Worker telemetry bootstrap requires human review; subsequent Worker delivery requires Canary metrics.'),
            rollback_plan='Authenticated responsible request within 12h, compare current version, drain Worker inflight, signed restoration of the saved previous bundle; no blind retry.',now=now)
        decision,reason=self.policy.decide(release,self.changes.history(),now=now)
        if decision in {'BLOCK','REJECT'}:
            self.changes.transition(record['id'],'REJECTED' if decision=='REJECT' else 'FAILED',now=now,details={'reason':reason})
            raise PermissionError(reason)
        self.portal.register(release,None)
        if decision=='HUMAN_REVIEW' and record['phase']=='CREATED':
            record=self.changes.transition(record['id'],'HUMAN_REVIEW',now=now,details={'reason':reason})
        self.portal.set_context(release_id(release),{'phase':record['phase'],'change_id':record['change_id'],'decision':decision,'criticality':record['criticality']})
        identity=release_id(release)
        with sqlite3.connect(self.gate.database) as db:
            old=db.execute('SELECT state FROM notifications WHERE id=?',(identity,)).fetchone()
            if old is None:db.execute('INSERT INTO notifications VALUES (?,\'dispatching\',NULL)',(identity,))
        if old is None:
            message=self.notifier.send(release,None,change_id=record['change_id'])
            with sqlite3.connect(self.gate.database) as db:
                db.execute('UPDATE notifications SET state=\'accepted\',message_id=? WHERE id=?',(message,identity))
        elif old[0]!='accepted':raise PermissionError('Notification outcome ambiguous; reconcile before retry')
        return {'decision':decision,'reason':reason,'change_id':record['change_id'],'release_id':identity}

    def publisher(self):
        app=GitHubApp(self.live.credentials,'cosmotech-gitops',write=True)
        env=app.git_environment(dict(os.environ,GNUPGHOME=str(ROOT/'gnupg')))
        return SignedGitPublisher(ROOT/'gitops','https://github.com/rlcosmo/cosmotech-gitops.git',
            self.config['gpg_fingerprint'],self.config['signed_anchor'],self.key.public_key(),git_env=env)

    def dispatch(self, identifier: str) -> dict:
        from automation.delivery_runtime import DeliveryRuntime
        self.collect(identifier)  # Fresh evidence after a real human's pause.
        state=json.loads(self.path(identifier).read_text())
        delivery=DeliveryRuntime(self,state)
        coordinator=PromotionCoordinator(self.gate,self.kestra,self.portal,self.notifier,delivery,
            delivery.start_worker,flow_revision=self.config['flow_revision'],changes=self.changes,owner=self.config['owner'])
        result=coordinator.complete(state['release'],state['bundle'],None,now=datetime.now(UTC))
        if result['decision']!='APPROVE':raise PermissionError('Human decision is still required')
        return result

    def tick(self, identifier: str) -> dict:
        from automation.delivery_runtime import DeliveryRuntime
        self.execution(identifier,scheduler=True)
        result=[]
        for path in sorted((ROOT/'executions').glob('*.json')):
            state=json.loads(path.read_text())
            identity=change_identity(state['release'])
            try:change=self.changes.read(identity)
            except PermissionError:
                continue  # Missing/pre-creation records confer no authority.
            if change['phase']=='HUMAN_REVIEW':
                human=self.portal.decision(release_id(state['release']))
                if human is not None:
                    execution=self.kestra.request('/api/v1/main/executions/'+state['release']['execution_id'])
                    if execution['state']['current']=='PAUSED':
                        self.kestra.request('/api/v1/main/executions/'+execution['id']+'/actions/resume',
                            method='POST',body=b'--CosmoTechResume--\r\n',
                            content_type='multipart/form-data; boundary=CosmoTechResume')
                        result.append({'change_id':change['change_id'],'action':'resume_after_authenticated_decision'})
            elif change['phase'] in {'DEPLOYING','OBSERVING','ROLLBACK_REQUESTED','ROLLBACK_RUNNING'}:
                result.append(DeliveryRuntime(self,state).tick(change))
        return {'reconciled':result,'scheduler_execution':identifier}


def main() -> None:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stage',required=True,choices=['collect','policy','dispatch','tick'])
    parser.add_argument('--execution-id',required=True)
    args=parser.parse_args()
    ROOT.mkdir(mode=0o700,exist_ok=True)
    # One controller effect at a time; file lock is released on process death.
    with (ROOT/'runtime.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        runtime=Runtime()
        function={'collect':runtime.collect,'policy':runtime.policy_stage,'dispatch':runtime.dispatch,'tick':runtime.tick}[args.stage]
        print(json.dumps(function(args.execution_id)))


if __name__=='__main__':
    main()
