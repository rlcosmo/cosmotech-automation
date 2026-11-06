import copy
from unittest.mock import Mock

import pytest

from automation.image_references import (
    pinned_reference,
    publication_resources,
    verified_digest,
)

IMAGE = '123456789012.dkr.ecr.us-east-1.amazonaws.com/aws-eks-istio-lab-dev-orders-worker:worker-1.0.5-16'
DIGEST = 'sha256:' + 'a' * 64


def ecr_client(mode='IMMUTABLE', digest=DIGEST):
    ecr = Mock()
    ecr.describe_repositories.return_value = {'repositories': [{'imageTagMutability': mode}]}
    ecr.describe_images.return_value = {'imageDetails': [{'imageDigest': digest}]}
    return ecr


def resource(image):
    return {'deployment.json': {'kind': 'Deployment', 'spec': {'template': {'spec': {'containers': [{'name': 'orders-worker', 'image': image}]}}}}}


def test_publish_tag_only_and_resolve_for_digest_checks_without_mutating_bundle():
    original = resource(IMAGE + '@' + DIGEST)
    saved = copy.deepcopy(original)
    published = publication_resources(original, ecr_client())
    assert published['deployment.json']['spec']['template']['spec']['containers'][0]['image'] == IMAGE
    assert original == saved
    assert pinned_reference(IMAGE, ecr_client()) == IMAGE + '@' + DIGEST


@pytest.mark.parametrize('mode', ['MUTABLE', 'IMMUTABLE_WITH_EXCLUSION', 'MUTABLE_WITH_EXCLUSION'])
def test_mutable_or_excluded_tags_fail_closed(mode):
    ecr = ecr_client(mode)
    with pytest.raises(PermissionError):
        publication_resources(resource(IMAGE + '@' + DIGEST), ecr)
    ecr.describe_images.assert_not_called()


def test_alias_mismatch_or_unavailable_ecr_blocks_publication():
    with pytest.raises(PermissionError):
        publication_resources(resource(IMAGE + '@' + DIGEST), ecr_client(digest='sha256:' + 'b' * 64))
    ecr = ecr_client()
    ecr.describe_repositories.side_effect = TimeoutError('unavailable')
    with pytest.raises(TimeoutError):
        publication_resources(resource(IMAGE + '@' + DIGEST), ecr)


def test_legacy_pinned_reference_does_not_depend_on_ecr_and_external_registry_rejected():
    ecr = Mock()
    assert verified_digest(IMAGE.split(':worker')[0] + '@' + DIGEST, ecr) == DIGEST
    ecr.describe_repositories.assert_not_called()
    for value in ['orders-worker:worker-1.0.5-16', 'docker.io/orders-worker:latest', IMAGE.split(':worker')[0]]:
        with pytest.raises(PermissionError):
            verified_digest(value, ecr)


def test_observation_formatting_is_same_release_only_and_cannot_change_pod_settings(tmp_path, journal, release, monkeypatch):
    import json

    from test_controlled_publisher import publisher_fixture
    from test_promotion_gate import NOW

    publisher, bundle, identity, git = publisher_fixture(tmp_path, journal, release, monkeypatch)
    journal.transition(identity, 'OBSERVING', now=NOW, details={'technical_validation': True})
    current = bundle['resources']['deployment.json']
    current['metadata'].setdefault('annotations', {})['cosmotech.io/gmud-id'] = 'RCL000001'
    current['spec']['template'].setdefault('metadata', {}).setdefault('labels', {})['cosmotech.io/gmud-id'] = 'RCL000001'
    image = IMAGE + '@' + release['digest']
    current['spec']['template']['spec']['containers'][0]['image'] = image
    (git.repo / 'gitops/apps/orders-worker/dev/deployment.json').write_text(json.dumps(current))
    monkeypatch.setattr('automation.image_references._tag_digest', lambda *a: release['digest'])
    proposed = {'deployment.json': copy.deepcopy(current)}
    tampered = copy.deepcopy(proposed)
    tampered['deployment.json']['spec']['template']['spec']['containers'][0]['env'] = [{'name': 'BYPASS', 'value': 'true'}]
    with pytest.raises(PermissionError, match='only remove'):
        publisher.publish(identity, 'image-reference:tag-only', tampered, expected_digest=release['digest'])
    with pytest.raises(PermissionError):
        publisher.publish(identity, 'image-reference:tag-only', proposed, expected_digest=release['current_digest'])
    revision = publisher.publish(identity, 'image-reference:tag-only', proposed, expected_digest=release['digest'])
    assert revision == 'b' * 40
    written = json.loads((git.repo / 'gitops/apps/orders-worker/dev/deployment.json').read_text())
    assert written['spec']['template']['spec']['containers'][0]['image'] == IMAGE
    assert journal.read(identity)['phase'] == 'OBSERVING'
