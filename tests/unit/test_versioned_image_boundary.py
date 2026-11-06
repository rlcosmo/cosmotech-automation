import pytest
from test_promotion_gate import NOW
from test_trusted_evidence import setup_sources

from automation.gitops_publisher import validate_bundle
from automation.trusted_evidence import collect


def sources_with_alias(release, monkeypatch, digest=None):
    request, sources, _ = setup_sources(release)
    original = sources["github"].api.side_effect

    def api(path):
        value = original(path)
        if path.startswith("actions/runs/") and "/jobs?" not in path:
            value["run_number"] = 17
        return value

    sources["github"].api.side_effect = api
    monkeypatch.setattr(
        "automation.trusted_evidence.source_version", lambda *a: "1.0.5"
    )
    sources["ecr"].describe_images.side_effect = [
        {"imageDetails": [{"imageDigest": release["digest"]}]},
        {"imageDetails": [{"imageTags": ["sha-" + "c" * 40]}]},
        {"imageDetails": [{"imageDigest": digest or release["digest"]}]},
    ]
    return request, sources


def test_tag_and_digest_and_pod_version_are_independently_verified(
    release, monkeypatch
):
    request, sources = sources_with_alias(release, monkeypatch)
    verified, bundle = collect(request, now=NOW, **sources)
    image = bundle["resources"]["deployment.json"]["spec"]["template"]["spec"][
        "containers"
    ][0]["image"]
    assert ":worker-1.0.5-17@" + release["digest"] in image
    assert (
        bundle["resources"]["deployment.json"]["spec"]["template"]["metadata"][
            "labels"
        ]["app.kubernetes.io/version"]
        == "1.0.5"
    )
    validate_bundle(verified, bundle)
    bundle["image_tag"] = "worker-wrong-17"
    with pytest.raises(PermissionError):
        validate_bundle(verified, bundle)


def test_wrong_alias_digest_blocks_even_when_ci_passed(release, monkeypatch):
    request, sources = sources_with_alias(release, monkeypatch, "sha256:" + "f" * 64)
    with pytest.raises(PermissionError, match="label"):
        collect(request, now=NOW, **sources)
