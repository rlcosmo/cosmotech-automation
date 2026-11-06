"""Role-authenticated, read-only evidence adapters for the protected gate runtime."""
from __future__ import annotations

import base64
import json
import re
import ssl
import subprocess
import time
import urllib.parse
import urllib.request
from datetime import UTC, datetime

import boto3
from botocore.config import Config

from automation.github_access import GitHubApp

AWS_CONFIG = Config(connect_timeout=3, read_timeout=15, retries={'total_max_attempts':3, 'mode':'standard'})


def client(service: str):
    return boto3.client(service, region_name='us-east-1', config=AWS_CONFIG)


def ssm_result(ssm, document: str, instance: str, parameters: dict, *, timeout: int = 90, raw: bool = False) -> dict:
    response = ssm.send_command(DocumentName=document, InstanceIds=[instance], Parameters=parameters)
    command = response['Command']['CommandId']
    deadline = time.monotonic()+timeout
    while time.monotonic() < deadline:
        try:
            result = ssm.get_command_invocation(CommandId=command, InstanceId=instance)
        except ssm.exceptions.InvocationDoesNotExist:
            time.sleep(1)
            continue
        state = result['Status']
        if state == 'Success':
            # Callers emit one bounded object; never parse a truncated/mixed transcript.
            return {'output':result['StandardOutputContent']} if raw else json.loads(result['StandardOutputContent'])
        if state in {'Failed','Cancelled','TimedOut','Cancelling'}:
            raise PermissionError('Trusted management command failed; inspect its private evidence')
        time.sleep(2)
    raise TimeoutError('Management command outcome unresolved; reconcile before resubmission: '+command)


class KubeClient:
    def __init__(self, eks, *, cluster: str):
        self.cluster = cluster
        response = eks.describe_cluster(name=cluster)['cluster']
        self.endpoint = response['endpoint']
        self.ssl = ssl.create_default_context(cadata=base64.b64decode(response['certificateAuthority']['data']).decode())
        self.token, self.expires = None, 0

    def request(self, path: str, *, body: dict | None = None, method: str = 'GET'):
        if not path.startswith(('/api/v1/namespaces/','/apis/apps/v1/namespaces/')) or '..' in path:
            raise ValueError('Unexpected Kubernetes boundary')
        if self.token is None or time.monotonic() >= self.expires:
            value=json.loads(subprocess.check_output(['aws','eks','get-token','--cluster-name',self.cluster,
                                                      '--region','us-east-1','--output','json']))
            self.token, self.expires = value['status']['token'], time.monotonic()+600
        headers={'Authorization':'Bearer '+self.token}
        if body is not None:
            headers['Content-Type']='application/merge-patch+json'
        request=urllib.request.Request(self.endpoint+path, headers=headers, method=method,
                                       data=json.dumps(body).encode() if body is not None else None)
        with urllib.request.urlopen(request, context=self.ssl, timeout=20) as response:
            return json.load(response)

    def deployment(self, application: str) -> dict:
        if application not in {'orders-api','orders-worker','orders-worker-canary'}:
            raise PermissionError('Unsupported application')
        namespace='orders-worker' if application.endswith('-canary') else application
        return self.request('/apis/apps/v1/namespaces/'+namespace+'/deployments/'+application)

    def query(self, expression: str) -> list[dict]:
        path='/api/v1/namespaces/observability/services/prometheus-kube-prometheus-prometheus:9090/proxy/api/v1/query?'
        result=self.request(path+urllib.parse.urlencode({'query':expression}))
        if result.get('status')!='success' or result['data']['resultType']!='vector':
            raise PermissionError('Prometheus did not return an explicit vector result')
        return result['data']['result']

    def worker_command(self, command: list[str]) -> None:
        """Adapt the existing executor without installing kubectl on the runtime host."""
        if command[:5] != ['kubectl','--context',self.cluster,'-n','orders-worker']:
            raise PermissionError('Executor escaped the fixed Worker namespace')
        args=command[5:]
        if args[:3]==['patch','configmap','orders-worker-canary-control'] and args[3:5]==['--type=merge','-p'] and len(args)==6:
            body=json.loads(args[5])
            if set(body)!= {'data'} or set(body['data'])!= {'lease.json'}:
                raise PermissionError('Only the consumption lease may be patched')
            self.request('/api/v1/namespaces/orders-worker/configmaps/orders-worker-canary-control',body=body,method='PATCH')
        elif args[:3]==['scale','deployment','orders-worker-canary'] and len(args)==4 and re.fullmatch('--replicas=[012]',args[3]):
            self.request('/apis/apps/v1/namespaces/orders-worker/deployments/orders-worker-canary/scale',
                         body={'spec':{'replicas':int(args[3][-1])}},method='PATCH')
        else:
            raise PermissionError('Unsupported Worker executor operation')


class LiveEvidence:
    def __init__(self, configuration: dict, *, ssm, secrets, ecr, kube):
        self.config, self.ssm, self.ecr, self.kube=configuration,ssm,ecr,kube
        self.credentials=json.loads(secrets.get_secret_value(SecretId='cosmotech/github/app')['SecretString'])
        self.gitops=GitHubApp(self.credentials,'cosmotech-gitops')

    def runtime(self, application: str) -> dict:
        argo=ssm_result(self.ssm,'CosmoTech-Promotion-Argo-State',self.config['argo_instance'],
                        {'Application':[application]})
        app, project=argo['application'],argo['project']
        revision=app['status']['sync']['revision']
        if not re.fullmatch('[a-f0-9]{40}',revision):
            raise PermissionError('Argo revision is not immutable')
        resources={name:json.loads(self.gitops.file('gitops/apps/'+application+'/dev/'+name,revision))
                   for name in ['deployment.json','configmap.json','service.json','hpa.json','ingress.json']}
        deployment=self.kube.deployment(application)
        container=next(c for c in deployment['spec']['template']['spec']['containers'] if c['name']==application)
        enforced=(self.config.get('enforcement_ready') is True and project.get('signatureKeys')==[
            {'keyID':self.config['gpg_fingerprint'][-16:]}] and app['spec']['project']=='orders-dev'
            and app['spec']['source']=={'repoURL':'ssh://git@ssh.github.com:443/rlcosmo/cosmotech-gitops.git',
                                       'path':'gitops/apps/'+application+'/dev','targetRevision':'main'})
        metric=self.kube.query('time() - max(timestamp(prometheus_build_info))')
        reliable=len(metric)==1 and 0 <= float(metric[0]['value'][1]) <= 90
        incidents=self.kube.query('ALERTS{alertstate="firing",severity="critical"}')
        return {'image':container['image'],'resources':resources,
                'signature_key':self.config['gpg_fingerprint'][-16:] if enforced else '',
                'argo_synced':app['status']['sync']['status']=='Synced',
                'argo_healthy':app['status']['sync']['status']=='Synced' and app['status']['health']['status']=='Healthy'
                    and deployment['status'].get('availableReplicas',0)>=1
                    and not any(c.get('type','').endswith('Error') for c in app['status'].get('conditions') or []),
                'incidents_clear':not incidents,'observability_reliable':reliable,
                'revision':revision,'observed_at':datetime.now(UTC).isoformat()}
