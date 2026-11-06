from copy import deepcopy

import pytest

from automation.promotion_evidence import classify_manifests


def manifest():
    return [{"apiVersion": "apps/v1", "kind": "Deployment",
             "metadata": {"name": "worker", "namespace": "orders-worker"},
             "spec": {"template": {"spec": {"containers": [
                 {"name": "worker", "image": "old@sha256:old", "env": [{"name": "RELEASE_VERSION", "value": "old"}]}]}}}}]


def test_image_only_change_is_classified_without_sending_raw_manifests():
    old = manifest()
    new = deepcopy(old)
    new[0]["spec"]["template"]["spec"]["containers"][0]["image"] = "new@sha256:new"
    result = classify_manifests(old, new)
    assert result["sensitive_changes"] is False
    assert "spec" not in result and len(result["manifest_sha256"]) == 64


def test_secret_reference_or_new_resource_requires_review():
    old, new = manifest(), manifest()
    new[0]["spec"]["template"]["spec"]["containers"][0]["env"].append(
        {"name": "TOKEN", "valueFrom": {"secretKeyRef": {"name": "not-a-secret-value", "key": "token"}}})
    assert classify_manifests(old, new)["sensitive_changes"] is True
    assert classify_manifests(old, [])["sensitive_changes"] is True


def test_duplicate_manifest_identity_rejected():
    old = manifest()
    with pytest.raises(ValueError):
        classify_manifests(old + old, old)
