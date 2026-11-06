"""GMUD-bound rolling bootstrap/API execution, safe rollback and durable observation.

Worker Canary effects remain owned by the existing controller/executor. A normal
Worker release is blocked until that binding has been enabled and verified; only
an explicitly human-approved initial telemetry bootstrap may use rolling delivery.
"""
from __future__ import annotations

import json
import math
import time
from datetime import UTC, datetime

from automation.controlled_publisher import ControlledPublisher, stable_control
from automation.gitops_publisher import validate_bundle
from automation.gmud import change_identity
from automation.gmud_observation import ObservationWindow
from automation.gmud_presentation import context_for
from automation.live_evidence import ssm_result
from automation.promotion_gate import release_id, verify_receipt
from automation.release_projection import stage_event


class DeliveryRuntime:
    def __init__(self, runtime, state: dict):
        self.rt,self.state=runtime,state
        self.release=state['release']
        self.identity=change_identity(self.release)
        self.window=ObservationWindow(runtime.changes.database)

    def save(self):
        path=self.rt.path(self.release['execution_id'])
        path.write_text(json.dumps(self.state));path.chmod(0o600)

    def record_stage(self,key,status,result='',evidence=None):
        stage_event(self.state,key,status,result=result,evidence=evidence);self.save()
        from automation.ci_correlation import delivery_event
        if self.state.get('bundle',{}).get('ci_attempt'):
            delivery_event(self.state,key,status)

    def controlled(self):
        return ControlledPublisher(self.rt.publisher(),self.rt.changes,self.rt.gate.database,observe=lambda status,result,evidence:self.record_stage('gitops',status,result,evidence))

    def sync(self, revision: str):
        self.record_stage('argo','RUNNING','Sincronizando revisão autorizada.',revision)
        try:
            ssm_result(self.rt.ssm,'CosmoTech-Sync-Orders-GitOps',self.rt.config['argo_instance'],
                       {'GitRevision':[revision]},timeout=190,raw=True)
        except (OSError,PermissionError,RuntimeError):
            self.record_stage('argo','FAILED','A revisão não atingiu sincronização e saúde esperadas.',revision)
            raise
        self.record_stage('argo','SUCCESS','Revisão e saúde confirmadas pelo Argo CD.',revision)

    def snapshot(self):
        return self.rt.live.runtime(self.release['service'])

    def context(self, change: dict, *, rollback_eligible: bool = False):
        if hasattr(self.rt,"refresh_operational"):self.rt.refresh_operational(self.state,change)
        self.rt.portal.register(self.release,None)
        self.rt.portal.set_context(release_id(self.release),context_for(change,state=self.state,rollback_eligible=rollback_eligible,eligibility_observed_at=datetime.now(UTC).isoformat(),
            deployment_strategy='CANARY' if self.state.get('delivery',{}).get('bootstrap') is False and self.release['service']=='orders-worker' else 'ROLLING',
            canary_stage=self.state.get('delivery',{}).get('canary_stage')))

    def validate(self, expected: str) -> dict:
        self.record_stage('validation','RUNNING','Verificando digest, disponibilidade e métricas.')
        snapshot=self.snapshot()
        if (snapshot['image'].split('@')[-1]!=expected or not snapshot['argo_healthy']
                or not snapshot['observability_reliable'] or not snapshot['incidents_clear']):
            self.record_stage('validation','FAILED','Digest, saúde ou evidências não conferem.')
            raise PermissionError('Exact application version, health and runtime evidence did not validate')
        self.record_stage('validation','SUCCESS','Digest e saúde validados com dados reais.')
        return snapshot

    def observing(self):
        snapshot=self.validate(self.release['digest'])
        now=datetime.now(UTC)
        change=self.rt.changes.transition(self.identity,'OBSERVING',now=now,
            details={'technical_validation':True,'gitops_revision':snapshot['revision']})
        self.window.record(change,self.sample(snapshot,now),now=now)
        self.context(change,rollback_eligible=True)
        self.state['delivery']['phase']='OBSERVING'
        self.save()

    def publish(self, release, bundle, receipt, *, now):
        verify_receipt(receipt,self.rt.key.public_key(),release,now=now)
        validate_bundle(release,bundle)
        if release['service']!='orders-api':raise PermissionError('Worker requires the dedicated delivery path')
        revision=self.controlled().publish(self.identity,'deploy:api',bundle['resources'],expected_digest=release['current_digest'])
        self.state['delivery']={'phase':'ROLLING','revision':revision,'bootstrap':False}
        self.save()
        try:
            self.sync(revision)
            self.observing()
        except Exception:
            # A recorded push is definite. Observe the actual digest before restoring.
            self.rollback(reason='deployment_validation_failed',automatic=True)
            raise
        return revision

    def start_worker(self, release, bundle, receipt):
        now=datetime.now(UTC)
        verify_receipt(receipt,self.rt.key.public_key(),release,now=now)
        validate_bundle(release,bundle)
        rows=self.rt.kube.query('cosmotech_worker_heartbeat_timestamp_seconds{channel="stable",version="'+release['current_digest']+'"}')
        if rows:
            from automation.canary_delivery import WorkerCanaryDelivery
            return WorkerCanaryDelivery(self).start(bundle,receipt)
        if receipt['payload']['approver'] not in set(self.rt.config['allowed_principals']):
            raise PermissionError('Initial telemetry bootstrap requires authenticated human review')
        resources=json.loads(json.dumps(bundle['resources']))
        resources['deployment.json'],resources['stable-control.json']=stable_control(resources['deployment.json'],release['digest'])
        revision=self.controlled().publish(self.identity,'deploy:worker-telemetry-bootstrap',resources,expected_digest=release['current_digest'])
        self.state['delivery']={'phase':'ROLLING','revision':revision,'bootstrap':True}
        self.save()
        try:
            self.sync(revision)
            deadline=time.monotonic()+60
            while time.monotonic()<deadline:
                points=self.rt.kube.query('cosmotech_worker_heartbeat_timestamp_seconds{channel="stable",version="'+release['digest']+'"}')
                if points and all(0<=time.time()-float(r['value'][1])<=90 for r in points):break
                time.sleep(5)
            else:raise PermissionError('Versioned Worker metrics not observed after bootstrap')
            self.observing()
        except Exception:
            self.rollback(reason='worker_bootstrap_validation_failed',automatic=True)
            raise

    def sample(self, snapshot: dict, now: datetime) -> dict:
        return {'observed_at':now.isoformat(),'current_digest':snapshot['image'].split('@')[-1],
                'argo_synced':snapshot['argo_healthy'],'healthy':snapshot['argo_healthy'],
                'metrics_reliable':snapshot['observability_reliable'],
                'critical_incidents':0 if snapshot['incidents_clear'] else 1,'errors':[]}

    def drain_worker(self) -> dict:
        deployment=self.rt.kube.deployment('orders-worker')
        image=next(c['image'] for c in deployment['spec']['template']['spec']['containers'] if c['name']=='orders-worker')
        if image.split('@')[-1]!=self.release['digest']:raise PermissionError('Worker version was superseded')
        self.rt.kube.request('/api/v1/namespaces/orders-worker/configmaps/orders-worker-stable-control',
                             body={'data':{'mode':'drain'}},method='PATCH')
        deadline=time.monotonic()+170
        while time.monotonic()<deadline:
            selector='{channel="stable",version="'+self.release['digest']+'"}'
            heartbeat=self.rt.kube.query('cosmotech_worker_heartbeat_timestamp_seconds'+selector)
            draining=self.rt.kube.query('cosmotech_worker_draining'+selector)
            inflight=self.rt.kube.query('cosmotech_worker_inflight'+selector)
            pods=self.rt.kube.request('/api/v1/namespaces/orders-worker/pods?labelSelector=app%3Dorders-worker')['items']
            active=[p for p in pods if p['status']['phase']=='Running' and not p['metadata'].get('deletionTimestamp')]
            active_names={p['metadata']['name'] for p in active}
            observed={r['metric'].get('pod') for r in heartbeat if 0<=time.time()-float(r['value'][1])<=90}
            if (active_names and active_names<=observed and len(draining)==len(active_names) and len(inflight)==len(active_names)
                    and all(float(r['value'][1])==1 for r in draining)
                    and all(math.isfinite(float(r['value'][1])) and float(r['value'][1])==0 for r in inflight)):
                return {'draining':True,'inflight':0,'pods':len(active_names),'observed_at':datetime.now(UTC).isoformat()}
            time.sleep(5)
        raise PermissionError('Drain/inflight proof not obtained; preserve consumers for manual analysis')

    def resume_restored_worker(self):
        """Resume only after the previous image has an actual running restored pod."""
        deadline=time.monotonic()+90
        while time.monotonic()<deadline:
            pods=self.rt.kube.request('/api/v1/namespaces/orders-worker/pods?labelSelector=app%3Dorders-worker')['items']
            found=any(not pod['metadata'].get('deletionTimestamp') and any(
                c['name']=='orders-worker' and c.get('imageID','').split('@')[-1]==self.release['current_digest']
                and c.get('state',{}).get('running') for c in pod.get('status',{}).get('containerStatuses',[])) for pod in pods)
            if found:
                self.rt.kube.request('/api/v1/namespaces/orders-worker/configmaps/orders-worker-stable-control',body={'data':{'mode':'run'}},method='PATCH')
                return
            time.sleep(3)
        raise PermissionError('Pod da versão restaurada não confirmado; manter drenagem e analisar')

    def rollback(self, *, reason: str, automatic=False):
        change=self.rt.changes.read(self.identity)
        if change['phase']=='DEPLOYING' and not automatic:raise PermissionError('Deployment is not in its responsible rollback window')
        if change['phase'] not in {'DEPLOYING','ROLLBACK_REQUESTED','ROLLBACK_RUNNING'}:
            raise PermissionError('No authorized rollback intention')
        if change['phase']!='ROLLBACK_RUNNING':
            change=self.rt.changes.transition(self.identity,'ROLLBACK_RUNNING',now=datetime.now(UTC),
                                               details={'reason':reason,'automatic_failure_recovery':automatic})
        proof=self.drain_worker() if self.release['service']=='orders-worker' else {'drain':'not_applicable_to_http_api'}
        revision=self.controlled().publish(self.identity,'rollback:previous',self.state['before_bundle']['resources'],
                                           expected_digest=self.release['digest'],restored_source_commit=self.state['bundle']['baseline_commit'])
        self.state['delivery'].update(phase='ROLLBACK_RUNNING',revision=revision)
        self.save()
        if self.release['service']=='orders-worker':self.resume_restored_worker()
        self.sync(revision)
        snapshot=self.validate(self.release['current_digest'])
        change=self.rt.changes.transition(self.identity,'ROLLED_BACK',now=datetime.now(UTC),
            details={'rollback_validated':True,'gitops_revision':snapshot['revision'],'drain_proof':proof,'reason':reason})
        self.state['delivery']['phase']='ROLLED_BACK'
        self.save();self.context(change)
        return {'gmud_id':change.get('rcl_id'),'change_id':change['change_id'],'phase':change['phase']}

    def tick(self, change: dict):
        now=datetime.now(UTC)
        if change['phase']=='DEPLOYING':
            delivery=self.state.get('delivery',{})
            if delivery.get('phase')=='CANARY':
                from automation.canary_delivery import WorkerCanaryDelivery
                return WorkerCanaryDelivery(self).tick()
            if delivery.get('phase')=='ROLLING':
                # Reconcile an already recorded signed revision, never publish again.
                self.sync(delivery['revision']);self.observing()
                return {'gmud_id':change.get('rcl_id'),'change_id':change['change_id'],'phase':'OBSERVING'}
            return {'gmud_id':change.get('rcl_id'),'change_id':change['change_id'],'phase':'RECONCILE_REQUIRED','reason':'No acknowledged delivery effect'}
        if change['phase']=='ROLLBACK_REQUESTED':return self.rollback(reason='authenticated_responsible_request')
        if change['phase']=='ROLLBACK_RUNNING':
            return {'gmud_id':change.get('rcl_id'),'change_id':change['change_id'],'phase':'RECONCILE_REQUIRED','reason':'Verify the recorded rollback revision; no blind replay'}
        snapshot=self.snapshot()
        self.context(change,rollback_eligible=snapshot['image'].split('@')[-1]==self.release['digest'])
        request=self.rt.portal.rollback_request(release_id(self.release))
        if request is not None:
            self.rt.changes.request_rollback(self.identity,principal=request['principal'],reason=request['reason'],now=now,
                allowed_principals=set(self.rt.config['allowed_principals']),current_digest=snapshot['image'].split('@')[-1])
            return self.rollback(reason='authenticated_responsible_request')
        self.window.record(change,self.sample(snapshot,now),now=now)
        from automation.promotion_gate import instant
        if now>=instant(change['rollback_until']):
            self.record_stage('closure','RUNNING','Verificando as12h reais e o encerramento no GLPI.')
            evidence=self.window.completion(change,now=now)
            change=self.rt.changes.transition(self.identity,'CLOSED_SUCCESS',now=now,details=evidence)
            self.record_stage('closure','SUCCESS','GMUD encerrada no GLPI após observação comprovada.')
            self.context(change)
        return {'gmud_id':change.get('rcl_id'),'change_id':change['change_id'],'phase':change['phase']}
