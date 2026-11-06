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
from automation.gmud_presentation import context_for
from automation.live_evidence import KubeClient, LiveEvidence, client
from automation.promotion_coordinator import PromotionCoordinator
from automation.promotion_gate import PromotionGate, release_id
from automation.promotion_runtime import KestraClient, SnsApprovalNotifier
from automation.release_projection import stage_event
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

    def refresh_operational(self,state,change):
        from automation.operational_sync import refresh
        try:
            refresh(self.kestra,state,change)
            state.pop('operational_snapshot_error',None)
        except (OSError,ValueError,KeyError):
            state['operational_snapshot_error']='Detalhes do Kestra temporariamente indisponíveis; fatos persistidos preservados.'
        self.path(state['release']['execution_id']).write_text(json.dumps(state))

    def path(self, execution: str) -> Path:
        if not re.fullmatch('[A-Za-z0-9_-]{1,100}',execution):raise ValueError('Invalid execution reference')
        return ROOT/'executions'/(execution+'.json')

    def execution(self, identifier: str, *, scheduler=False) -> dict:
        execution=self.kestra.request('/api/v1/main/executions/'+identifier)
        flow='promotion_reconcile' if scheduler else self.config.get('release_flow','promotion_gate')
        revision=self.config['scheduler_revision' if scheduler else 'flow_revision']
        if (execution.get('namespace')!=(self.config.get('scheduler_namespace','cosmotech.promotion') if scheduler else 'cosmotech.promotion') or execution.get('flowId')!=flow
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
        return {'release_id':release_id(release),'version':bundle.get('application_version',release['commit'][:12]),'evidence_sha256':hashlib.sha256(path.read_bytes()).hexdigest()}

    def policy_stage(self, identifier: str) -> dict:
        self.execution(identifier)
        state=json.loads(self.path(identifier).read_text());release=state['release'];now=datetime.now(UTC)
        stage_event(state,'gmud','RUNNING',result='Criando ou reconciliando a GMUD oficial.');self.path(identifier).write_text(json.dumps(state))
        record=self.changes.ensure(release,owner=self.config['owner'],
            ci_links=['https://github.com/rlcosmo/cosmotech-'+release['service']+'/actions/runs/'+release['run_id']],
            implementation_plan='Publicar pelo GitOps assinado; validar revisão e saúde no Argo CD. Worker: Canary com métricas e drenagem.',
            rollback_plan='Solicitação autenticada até12h; conferir versão atual, drenar Worker e comprovar inflight zero antes de restaurar o digest anterior pelo publisher.',now=now,kestra_flow=self.config.get('release_flow','promotion_gate'),application_version=state['bundle'].get('application_version'),current_version=state['bundle'].get('current_version'))
        stage_event(state,'gmud','SUCCESS',result='GMUD '+str(record.get('rcl_id',record['change_id']))+' criada e conferida no GLPI.')
        stage_event(state,'policy','RUNNING',result='Avaliando criticidade e controles técnicos.')
        decision,reason=self.policy.decide(release,self.changes.history(),now=now)
        stage_event(state,'policy','SUCCESS' if decision in {'APPROVE','HUMAN_REVIEW'} else 'BLOCKED',result='Aprovação humana obrigatória.' if decision=='HUMAN_REVIEW' else 'Controles técnicos avaliados.')
        self.path(identifier).write_text(json.dumps(state))
        if decision in {'BLOCK','REJECT'}:
            self.changes.transition(record['id'],'REJECTED' if decision=='REJECT' else 'FAILED',now=now,details={'reason':reason})
            raise PermissionError(reason)
        self.portal.register(release,None)
        if decision=='HUMAN_REVIEW' and record['phase']=='CREATED':
            record=self.changes.transition(record['id'],'HUMAN_REVIEW',now=now,details={'reason':reason})
        self.refresh_operational(state,record)
        self.portal.set_context(release_id(release),context_for(record,state=state,decision=decision,policy_reason=reason))
        identity=release_id(release)
        with sqlite3.connect(self.gate.database) as db:
            old=db.execute('SELECT state FROM notifications WHERE id=?',(identity,)).fetchone()
            if old is None:db.execute('INSERT INTO notifications VALUES (?,\'dispatching\',NULL)',(identity,))
        if old is None:
            message=self.notifier.send(release,None,change_id=record['change_id'],rcl_id=record.get('rcl_id'),application_version=record.get('application_version'))
            with sqlite3.connect(self.gate.database) as db:
                db.execute('UPDATE notifications SET state=\'accepted\',message_id=? WHERE id=?',(message,identity))
        elif old[0]!='accepted':raise PermissionError('Notification outcome ambiguous; reconcile before retry')
        return {'decision':decision,'reason':reason,'change_id':record['change_id'],'gmud_id':record.get('rcl_id',str(record['change_id'])),'release_id':identity}

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
            delivery.start_worker,flow_revision=self.config['flow_revision'],changes=self.changes,owner=self.config['owner'],flow_id=self.config.get('release_flow','promotion_gate'))
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
            except (PermissionError,OSError,RuntimeError):
                self.portal.mark_sync_error(release_id(state['release']))
                continue  # Cached history is readable; unavailable GLPI confers no authority.
            self.portal.mark_sync_success(release_id(state['release']))
            if change['phase']=='HUMAN_REVIEW':
                self.refresh_operational(state,change)
                self.portal.set_context(release_id(state['release']),context_for(change,state=state,rollback_eligible=False))
                human=self.portal.decision(release_id(state['release']))
                if human is not None:
                    execution=self.kestra.request('/api/v1/main/executions/'+state['release']['execution_id'])
                    if execution['state']['current']=='PAUSED':
                        self.kestra.request('/api/v1/main/executions/'+execution['id']+'/actions/resume',
                            method='POST',body=b'--CosmoTechResume--\r\n',
                            content_type='multipart/form-data; boundary=CosmoTechResume')
                        result.append({'change_id':change['change_id'],'action':'resume_after_authenticated_decision'})
            elif change['phase'] in {'DEPLOYING','OBSERVING','ROLLBACK_REQUESTED','ROLLBACK_RUNNING'}:
                try:
                    result.append(DeliveryRuntime(self,state).tick(change))
                except (PermissionError,TimeoutError,OSError,RuntimeError) as error:
                    state['reconciliation_error']={'type':type(error).__name__,'message':str(error)[:300],'at':datetime.now(UTC).isoformat()}
                    path.write_text(json.dumps(state));path.chmod(0o600)
                    self.portal.set_context(release_id(state['release']),context_for(change,state=state,rollback_eligible=False,reconciliation_required=True))
                    result.append({'gmud_id':change.get('rcl_id'),'change_id':change['change_id'],'phase':'RECONCILIATION_REQUIRED','error_type':type(error).__name__})
            elif change['phase']=='ROLLED_BACK' and state.get('delivery',{}).get('phase')=='CANARY' and not state['delivery'].get('capacity_restored'):
                from automation.canary_delivery import WorkerCanaryDelivery
                result.append(WorkerCanaryDelivery(DeliveryRuntime(self,state)).tick())
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
        try:
            output=function(args.execution_id)
        except Exception as error:
            path=runtime.path(args.execution_id)
            if path.exists():
                state=json.loads(path.read_text());identity=release_id(state['release'])
                state['reconciliation_error']={'type':type(error).__name__,'message':str(error)[:300],'at':datetime.now(UTC).isoformat()}
                path.write_text(json.dumps(state))
                try:
                    change=runtime.changes.read(change_identity(state['release']))
                    runtime.portal.set_context(identity,context_for(change,state=state,rollback_eligible=False,reconciliation_required=True))
                except (OSError,RuntimeError,PermissionError):runtime.portal.mark_sync_error(identity)
            if args.stage in {'collect','policy','dispatch'}:
                from automation.ci_correlation import update
                try:
                    execution=runtime.execution(args.execution_id)
                    update(execution['inputs'],execution_id=args.execution_id,error=str(error),stage=args.stage)
                except (OSError,PermissionError):pass
            raise
        print(json.dumps(output))


if __name__=='__main__':
    main()
