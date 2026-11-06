"""GMUD-bound signed effects. Only protected runtime state can call this publisher."""
from __future__ import annotations

import fcntl
import hashlib
import json
import re
import sqlite3

from automation.gitops_publisher import verify_commit
from automation.promotion_gate import canonical


class ControlledPublisher:
    def __init__(self, git, changes, database: str):
        self.git, self.changes, self.database=git,changes,database
        with sqlite3.connect(database) as db:
            db.execute('CREATE TABLE IF NOT EXISTS gitops_effects (id TEXT PRIMARY KEY, gmud TEXT, state TEXT, revision TEXT, plan TEXT)')

    def publish(self, identity: str, effect: str, resources: dict[str,dict], *, expected_digest: str, restored_source_commit: str | None = None) -> str:
        """Intent is consumed before push; exact application version is checked under lock."""
        change=self.changes.read(identity)
        allowed={'deploy':{'DEPLOYING'},'canary':{'DEPLOYING'},'promote':{'DEPLOYING'},
                 'rollback':{'ROLLBACK_RUNNING','DEPLOYING'},'drain-complete':{'DEPLOYING','ROLLBACK_RUNNING','ROLLED_BACK'}}
        operation=effect.split(':')[0]
        if change['phase']=='ROLLED_BACK' and (effect!='drain-complete:rollback' or set(resources)!={'hpa.json'} or expected_digest!=change['release']['current_digest']):
            raise PermissionError('Mudança revertida permite somente restaurar capacidade após drenagem')
        if operation not in allowed or change['phase'] not in allowed[operation]:
            raise PermissionError('GMUD does not authorize this operation')
        service=change['release']['service']
        if expected_digest not in {change['release']['digest'],change['release']['current_digest']}:
            raise PermissionError('Expected version is outside the authorized release')
        if operation=='rollback' and not re.fullmatch('[a-f0-9]{40}',restored_source_commit or ''):
            raise PermissionError('Previous source commit must be preserved for rollback provenance')
        if self.git.git('remote','get-url','origin')!=self.git.origin:
            raise PermissionError('Unexpected GitOps origin')
        kinds={'deployment.json':('Deployment',service),'configmap.json':('ConfigMap','orders-config'),
               'service.json':('Service',service),'hpa.json':('HorizontalPodAutoscaler',service),
               'ingress.json':('Ingress',service+'-dev')}
        if service=='orders-worker':
            kinds.update({'canary-deployment.json':('Deployment','orders-worker-canary'),
                          'canary-control.json':('ConfigMap','orders-worker-canary-control'),
                          'stable-control.json':('ConfigMap','orders-worker-stable-control')})
        if not set(resources)<=set(kinds) or not set(kinds).intersection(resources):
            raise PermissionError('Unknown publication paths')
        for name,resource in resources.items():
            kind,target=kinds[name]
            if (resource.get('kind'),resource.get('metadata',{}).get('name'),resource.get('metadata',{}).get('namespace'))!=(kind,target,service):
                raise PermissionError('Resource escaped the application boundary')
            if kind=='Deployment':
                containers=resource['spec']['template']['spec']['containers']
                if len(containers)!=1 or containers[0]['name']!=service:
                    raise PermissionError('Unexpected companion container')
                image=containers[0]['image']
                if (not re.fullmatch(r'[0-9]{12}\.dkr\.ecr\.us-east-1\.amazonaws\.com/aws-eks-istio-lab-dev-'+re.escape(service)+r'(?::sha-[a-f0-9]{40})?@sha256:[a-f0-9]{64}',image)
                        or image.split('@')[-1] not in {change['release']['digest'],change['release']['current_digest']}):
                    raise PermissionError('Image escaped the approved application versions')
        resources=json.loads(json.dumps(resources))
        if change.get('rcl_id'):
            for resource in resources.values():
                resource['metadata'].setdefault('annotations',{})['cosmotech.io/gmud-id']=change['rcl_id']
                if resource['kind']=='Deployment' and resource['spec']['template']['spec']['containers'][0]['image'].split('@')[-1]==change['release']['digest']:
                    resource['spec']['template'].setdefault('metadata',{}).setdefault('labels',{})['cosmotech.io/gmud-id']=change['rcl_id']
        effect_id=hashlib.sha256(canonical({'gmud':identity,'effect':effect,'resources':resources,'expected':expected_digest})).hexdigest()
        with (self.git.repo/'.git'/'cosmotech-publisher.lock').open('a') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX)
            with sqlite3.connect(self.database) as db:
                old=db.execute('SELECT state,revision FROM gitops_effects WHERE id=?',(effect_id,)).fetchone()
            if old:
                if old[0]=='success':return old[1]
                raise PermissionError('Ambiguous publication requires exact-revision reconciliation')
            if self.git.git('status','--porcelain'):
                raise PermissionError('Publisher checkout is not clean')
            self.git.git('fetch','--quiet','origin','main')
            parent=self.git.git('rev-parse','origin/main')
            verify_commit(self.git.repo,parent,self.git.fingerprint,env=self.git.git_env)
            self.git.git('checkout','--quiet','--detach',parent)
            folder=self.git.repo/'gitops'/'apps'/service/'dev'
            current=json.loads((folder/'deployment.json').read_text())
            image=next(c['image'] for c in current['spec']['template']['spec']['containers'] if c['name']==service)
            if image.split('@')[-1]!=expected_digest:
                raise PermissionError('GitOps already contains a superseding application version')
            plan=json.dumps({'parent':parent,'effect':effect,'files':resources})
            with sqlite3.connect(self.database) as db:
                db.execute('INSERT INTO gitops_effects VALUES (?,?,?,NULL,?)',(effect_id,identity,'dispatching',plan))
            for name,resource in resources.items():
                dest=folder/name
                if any(p.is_symlink() for p in [dest,*dest.parents] if p!=self.git.repo.parent):
                    raise PermissionError('Symlink publication path')
                dest.resolve().relative_to(self.git.repo)
                dest.write_text(json.dumps(resource,indent=2)+'\n')
            proof=self.git.repo/'gitops'/'releases'/(service+'-dev.json')
            if any(p.is_symlink() for p in [proof,*proof.parents] if p!=self.git.repo.parent):
                raise PermissionError('Symlink evidence path')
            proof.parent.mkdir(parents=True,exist_ok=True)
            proof.write_text(json.dumps({'application':service,'gmud_id':change.get('rcl_id',change['change_id']),'glpi_change_id':change['change_id'],
                'gmud_release_id':identity,'effect_id':effect_id,'effect':effect,
                'kestra_execution_id':change['kestra_execution_id'],
                'source_repository':'rlcosmo/cosmotech-'+service,
                'source_sha':restored_source_commit if operation=='rollback' else change['release']['commit'],
                'requested_source_sha':change['release']['commit'],'resources':resources},indent=2)+'\n')
            paths=[str((folder/name).relative_to(self.git.repo)) for name in resources]
            paths.append(str(proof.relative_to(self.git.repo)))
            self.git.git('add','--',*paths)
            if set(self.git.git('diff','--cached','--name-only').splitlines())-set(paths):
                raise PermissionError('Unexpected staged publication')
            self.git.git('-c','user.name=CosmoTech Promotion','-c','user.email=promotion@localhost',
                         '-c','user.signingkey='+self.git.fingerprint,'-c','commit.gpgsign=true',
                         'commit','--quiet','-m',f'feat({service}): {operation} GMUD {change.get("rcl_id",change["change_id"])}')
            revision=self.git.git('rev-parse','HEAD')
            verify_commit(self.git.repo,revision,self.git.fingerprint,env=self.git.git_env)
            self.git.git('push','--quiet','origin','HEAD:main')
            with sqlite3.connect(self.database) as db:
                db.execute('UPDATE gitops_effects SET state=?,revision=? WHERE id=?',('success',revision,effect_id))
            return revision


def stable_control(deployment: dict, digest: str) -> tuple[dict,dict]:
    """Add drain control without changing business configuration or replica ownership."""
    deployment=json.loads(json.dumps(deployment))
    pod=deployment['spec']['template']
    pod['metadata']['labels'].update({'cosmotech.io/component':'orders-worker','cosmotech.io/channel':'stable'})
    pod['spec']['terminationGracePeriodSeconds']=240
    container=next(c for c in pod['spec']['containers'] if c['name']=='orders-worker')
    container['env']=[e for e in container.get('env',[]) if e['name'] not in {'WORKER_CONTROL_FILE','RELEASE_CHANNEL','RELEASE_VERSION'}]+[
        {'name':'WORKER_CONTROL_FILE','value':'/etc/cosmotech/stable/mode'},
        {'name':'RELEASE_CHANNEL','value':'stable'},{'name':'RELEASE_VERSION','value':digest}]
    pod['spec']['volumes']=[v for v in pod['spec'].get('volumes',[]) if v['name']!='stable-control']+[
        {'name':'stable-control','configMap':{'name':'orders-worker-stable-control'}}]
    container['volumeMounts']=[v for v in container.get('volumeMounts',[]) if v['name']!='stable-control']+[
        {'name':'stable-control','mountPath':'/etc/cosmotech/stable','readOnly':True}]
    control={'apiVersion':'v1','kind':'ConfigMap','metadata':{'name':'orders-worker-stable-control','namespace':'orders-worker'},'data':{'mode':'run'}}
    return deployment,control
