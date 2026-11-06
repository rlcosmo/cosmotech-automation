"""Bind the existing persistent Worker controller/executor to trusted live adapters."""
from __future__ import annotations

import copy
import json
import math
import sqlite3
import time
from datetime import UTC, datetime

from automation.controlled_publisher import stable_control
from automation.live_evidence import client
from automation.promotion_gate import instant, release_id
from automation.promotion_runtime import CanaryExecutor
from automation.worker_canary import CanaryController


class WorkerCanaryDelivery:
    def __init__(self, delivery):
        self.delivery=delivery
        self.rt=delivery.rt
        self.release=delivery.release
        self.identity=release_id(self.release)
        self.controller=CanaryController(self.rt.gate.database)
        self.sqs=client('sqs')

    def queue(self):
        config=self.delivery.state['bundle']['resources']['configmap.json']['data']
        url=config.get('ORDERS_QUEUE_URL') or config['SQS_QUEUE_URL']
        raw=self.sqs.get_queue_attributes(QueueUrl=url,AttributeNames=['ApproximateNumberOfMessages','ApproximateNumberOfMessagesNotVisible','RedrivePolicy'])['Attributes']
        arn=json.loads(raw['RedrivePolicy'])['deadLetterTargetArn']
        region,account,name=arn.split(':')[3:]
        dlq=self.sqs.get_queue_attributes(QueueUrl=f'https://sqs.{region}.amazonaws.com/{account}/{name}',AttributeNames=['ApproximateNumberOfMessages'])['Attributes']
        visible=int(raw['ApproximateNumberOfMessages']);inflight=int(raw['ApproximateNumberOfMessagesNotVisible'])
        if visible or inflight:
            cloudwatch=client('cloudwatch')
            result=cloudwatch.get_metric_statistics(Namespace='AWS/SQS',MetricName='ApproximateAgeOfOldestMessage',Dimensions=[{'Name':'QueueName','Value':url.rsplit('/',1)[1]}],StartTime=datetime.fromtimestamp(time.time()-180,UTC),EndTime=datetime.now(UTC),Period=60,Statistics=['Maximum'])['Datapoints']
            if not result:raise PermissionError('Idade da fila sem amostras atuais')
            latest=max(result,key=lambda p:p['Timestamp']);age=float(latest['Maximum'])
            if time.time()-latest['Timestamp'].timestamp()>180:raise PermissionError('Métrica da fila desatualizada')
        else:age=0 # Empty queue independently observed via SQS; no synthetic CloudWatch datapoint.
        return {'visible':visible,'inflight':inflight,'dlq':int(dlq['ApproximateNumberOfMessages']),'oldest':age}

    def executor(self):
        return CanaryExecutor(self.rt.gate.database,self.rt.config['cluster'],self.rt.kube.worker_command,
                              self.promote,lambda event:None)

    def start(self, bundle, receipt):
        q=self.queue()
        if q['dlq'] or q['visible'] or q['inflight']:
            raise PermissionError('Canary exige baseline de fila e DLQ limpas')
        candidate=copy.deepcopy(bundle['resources'])
        stable=copy.deepcopy(self.delivery.state['before_bundle']['resources'])
        canary,_=stable_control(candidate['deployment.json'],self.release['digest'])
        canary['metadata']['name']='orders-worker-canary'
        canary['metadata']['labels']['app']='orders-worker-canary'
        canary['spec']['selector']['matchLabels']={'app':'orders-worker-canary'}
        canary['spec']['replicas']=0
        pod=canary['spec']['template'];pod['metadata']['labels'].update(app='orders-worker-canary',**{'cosmotech.io/channel':'canary'})
        container=next(c for c in pod['spec']['containers'] if c['name']=='orders-worker')
        for env in container['env']:
            if env['name']=='RELEASE_CHANNEL':env['value']='canary'
            if env['name']=='WORKER_CONTROL_FILE':env['value']='/etc/cosmotech/control/lease.json'
        pod['spec']['volumes']=[v for v in pod['spec']['volumes'] if v['name']!='stable-control']+[{'name':'canary-control','configMap':{'name':'orders-worker-canary-control'}}]
        container['volumeMounts']=[v for v in container['volumeMounts'] if v['name']!='stable-control']+[{'name':'canary-control','mountPath':'/etc/cosmotech/control','readOnly':True}]
        stable['hpa.json']['spec'].update(minReplicas=1,maxReplicas=1)
        stable.update({'canary-deployment.json':canary,'canary-control.json':{'apiVersion':'v1','kind':'ConfigMap','metadata':{'name':'orders-worker-canary-control','namespace':'orders-worker'},'data':{'lease.json':'{"mode":"drain","expires_at":0}'}}})
        revision=self.delivery.controlled().publish(self.delivery.identity,'canary:prepare',stable,expected_digest=self.release['current_digest'])
        self.delivery.state['delivery']={'phase':'CANARY','revision':revision,'bootstrap':False}
        self.delivery.save();self.delivery.sync(revision)
        change=self.rt.changes.read(self.delivery.identity)
        self.controller.start(receipt,self.release,self.rt.key.public_key(),now=datetime.now(UTC),gmud_id=change.get('rcl_id'))
        self.executor().apply(self.identity,now=datetime.now(UTC))
        self.delivery.context(change)

    def promote(self, identity, digest, operation):
        if identity!=self.identity:raise PermissionError('Canary de outra release')
        current=self.rt.kube.deployment('orders-worker')
        actual=next(c['image'] for c in current['spec']['template']['spec']['containers'] if c['name']=='orders-worker').split('@')[-1]
        if actual==digest:return
        if digest==self.release['digest']:
            resources=copy.deepcopy(self.delivery.state['bundle']['resources'])
            resources['deployment.json'],resources['stable-control.json']=stable_control(resources['deployment.json'],digest)
            resources['hpa.json']['spec'].update(minReplicas=1,maxReplicas=1)
            revision=self.delivery.controlled().publish(self.delivery.identity,'promote:canary',resources,expected_digest=self.release['current_digest'])
        else:
            self.delivery.drain_worker()
            resources=self.delivery.state['before_bundle']['resources']
            revision=self.delivery.controlled().publish(self.delivery.identity,'rollback:canary',resources,expected_digest=self.release['digest'],restored_source_commit=self.delivery.state['bundle']['baseline_commit'])
        self.delivery.state['delivery']['revision']=revision;self.delivery.save()
        if digest==self.release['current_digest']:self.delivery.resume_restored_worker()
        self.delivery.sync(revision)

    def number(self, expression, *, empty=False):
        rows=self.rt.kube.query(expression)
        if not rows and empty:return 0.0 # No matching failure series; success/heartbeat checked separately.
        if len(rows)!=1:raise PermissionError('Série de métricas ausente ou ambígua')
        value=float(rows[0]['value'][1])
        if not math.isfinite(value) or value<0:raise PermissionError('Métrica inválida')
        return value

    def observation(self, state):
        now=datetime.now(UTC);elapsed=max(1,int((now-instant(state['entered_at'])).total_seconds()));window=min(300,elapsed)
        cs='{channel="canary",version="'+self.release['digest']+'"}'
        stable=self.rt.kube.deployment('orders-worker');canary=self.rt.kube.deployment('orders-worker-canary')
        version=next(c['image'] for c in stable['spec']['template']['spec']['containers'] if c['name']=='orders-worker').split('@')[-1]
        ss='{channel="stable",version="'+version+'"}'
        pods=self.rt.kube.request('/api/v1/namespaces/orders-worker/pods?labelSelector=app%3Dorders-worker-canary')['items']
        active={p['metadata']['name'] for p in pods if p['status']['phase']=='Running' and not p['metadata'].get('deletionTimestamp')}
        heartbeats=self.rt.kube.query('cosmotech_worker_heartbeat_timestamp_seconds'+cs)
        observed={r['metric'].get('pod') for r in heartbeats if 0<=time.time()-float(r['value'][1])<=90}
        if not active or active!=observed:raise PermissionError('Métricas frescas não cobrem todos os pods canary')
        q=self.queue();runtime=self.delivery.snapshot()
        if not runtime['incidents_clear'] or not runtime['observability_reliable']:
            raise PermissionError('Incidente ou observabilidade inválida')
        if not runtime['argo_healthy'] and state['stage'] not in {'DRAIN_PROMOTED','DRAIN_ROLLBACK'}:
            raise PermissionError('Argo não está saudável')
        def count(channel_selector,result):
            selector=channel_selector[:-1]+',result="'+result+'"}'
            return self.number('sum(increase(cosmotech_worker_messages_total'+selector+'['+str(window)+'s]))')
        draining=self.rt.kube.query('cosmotech_worker_draining'+cs)
        inflight=self.rt.kube.query('cosmotech_worker_inflight'+cs)
        if len(draining)!=len(active) or len(inflight)!=len(active):raise PermissionError('Drenagem incompleta')
        drained=all(float(r['value'][1])==1 for r in draining)
        success=count(cs,'success')
        p95=self.number('histogram_quantile(0.95,sum by(le)(rate(cosmotech_worker_processing_seconds_bucket'+cs+'['+str(window)+'s])))') if success else None
        # No latency observation exists without processed events; never fabricate a value.
        return {'version':self.release['digest'],'sampled_at':now.isoformat(),'window_seconds':window,'queue_data_age_seconds':0,
                'canary_success':success,'canary_failure':count(cs,'error'),'canary_ack_errors':count(cs,'ack_error'),
                'canary_retries':self.number('sum(increase(cosmotech_worker_retries_total'+cs+'['+str(window)+'s]))'),
                'canary_ddb_errors':self.number('sum(increase(cosmotech_worker_dynamodb_errors_total'+cs+'['+str(window)+'s]))'),
                'stable_success':count(ss,'success'),'stable_failure':count(ss,'error'),'p95_seconds':p95,
                'dlq_visible':q['dlq'],'queue_visible':q['visible'],'oldest_age_seconds':q['oldest'],
                'available_canary':canary['status'].get('availableReplicas',0),'available_stable':stable['status'].get('availableReplicas',0),
                'inflight':sum(float(r['value'][1]) for r in inflight),'draining':drained,
                'stable_version':version,'stable_synced':runtime['argo_synced']}

    def tick(self):
        with sqlite3.connect(self.rt.gate.database) as db:
            row=db.execute('SELECT state FROM rollouts WHERE id=?',(self.identity,)).fetchone()
        if not row:raise PermissionError('Intent canary ausente; reconciliar sem replay')
        state=json.loads(row[0])
        elapsed=(datetime.now(UTC)-instant(state['entered_at'])).total_seconds()
        if state['stage'] in {'CANARY_1','CANARY_2'} and elapsed<90:
            return {'gmud_id':state.get('gmud_id'),'canary_stage':state['stage'],'status':'Aguardando inicialização e primeiras coletas','elapsed_seconds':elapsed}
        try:observation=self.observation(state)
        except PermissionError as error:
            self.delivery.state['delivery']['observation_error']=str(error)[:200]
            self.delivery.state['delivery'].setdefault('observation_errors',[]).append({'at':datetime.now(UTC).isoformat(),'stage':state['stage'],'error':str(error)[:200]})
            self.delivery.save()
            observation={} # Existing controller fails closed and requests drain/rollback.
        if observation:
            self.delivery.state['delivery'].pop('observation_error',None)
        state=self.controller.step(self.identity,observation,now=datetime.now(UTC))
        self.executor().apply(self.identity,now=datetime.now(UTC))
        self.delivery.state['delivery']['canary_stage']=state['stage'];self.delivery.save()
        self.delivery.context(self.rt.changes.read(self.delivery.identity))
        change=self.rt.changes.read(self.delivery.identity)
        if state['stage']=='COMPLETE':
            # Restore source HPA only after all candidate consumers have drained.
            revision=self.delivery.controlled().publish(self.delivery.identity,'drain-complete:canary',{'hpa.json':self.delivery.state['bundle']['resources']['hpa.json']},expected_digest=self.release['digest'])
            self.delivery.sync(revision);self.delivery.observing()
        elif state['stage']=='ROLLED_BACK':
            if not self.delivery.state['delivery'].get('capacity_restored'):
                revision=self.delivery.controlled().publish(self.delivery.identity,'drain-complete:rollback',{'hpa.json':self.delivery.state['before_bundle']['resources']['hpa.json']},expected_digest=self.release['current_digest'])
                self.delivery.sync(revision)
                self.delivery.state['delivery'].update(capacity_restored=True,phase='ROLLED_BACK')
                self.delivery.save()
            if change['phase']=='ROLLED_BACK':return {'gmud_id':change.get('rcl_id'),'canary_stage':'ROLLED_BACK','capacity_restored':True}
            change=self.rt.changes.transition(change['id'],'ROLLBACK_RUNNING',now=datetime.now(UTC),details={'reason':state['reason']})
            change=self.rt.changes.transition(change['id'],'ROLLED_BACK',now=datetime.now(UTC),details={'rollback_validated':True,'canary_stage':state['stage']})
            self.delivery.context(change)
        return {'gmud_id':change.get('rcl_id'),'change_id':change['change_id'],'canary_stage':state['stage'],'reason':state['reason']}
