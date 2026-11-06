from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from automation.delivery_runtime import DeliveryRuntime


def instance(release,pod):
    delivery=DeliveryRuntime.__new__(DeliveryRuntime)
    delivery.release=release
    delivery.rt=SimpleNamespace(kube=Mock())
    delivery.rt.kube.request.side_effect=[{'items':[pod]},{}]
    return delivery


def pod_for(digest):
    return {'metadata':{},'status':{'containerStatuses':[{'name':'orders-worker','image':'sha256:configuration-digest',
            'imageID':'registry@'+digest,'state':{'running':{}}}]}}


def test_resume_requires_running_restored_manifest_digest(release):
    pod=pod_for(release['current_digest']);pod['status']['containerStatuses'][0]['state']['running']={'startedAt':'actual'}
    d=instance(release,pod);d.resume_restored_worker()
    assert d.rt.kube.request.call_args.kwargs=={'body':{'data':{'mode':'run'}},'method':'PATCH'}


def test_wrong_image_never_resumes_consumption(release,monkeypatch):
    d=instance(release,pod_for(release['digest']))
    clock=iter([0,1,91]);monkeypatch.setattr('automation.delivery_runtime.time.monotonic',lambda:next(clock))
    monkeypatch.setattr('automation.delivery_runtime.time.sleep',lambda _:None)
    with pytest.raises(PermissionError):d.resume_restored_worker()
    assert d.rt.kube.request.call_count==1
