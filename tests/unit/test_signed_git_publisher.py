import hashlib
import shutil
import subprocess

import pytest
from test_promotion_gate import NOW

from automation.gitops_publisher import (
    SignedGitPublisher,
    validate_bundle,
    verify_commit,
)
from automation.promotion_gate import canonical


def bundle_for(release):
    service = release["service"]
    names = {"deployment.json": "Deployment", "configmap.json": "ConfigMap", "service.json": "Service",
             "hpa.json": "HorizontalPodAutoscaler", "ingress.json": "Ingress"}
    resources = {path: {"apiVersion": "v1", "kind": kind,
        "metadata": {"name": "orders-config" if kind == "ConfigMap" else service + "-dev" if kind == "Ingress" else service, "namespace": service}}
        for path, kind in names.items()}
    resources["deployment.json"]["spec"] = {"template": {"spec": {"containers": [{"name": service,
        "image": "000000000000.dkr.ecr.us-east-1.amazonaws.com/aws-eks-istio-lab-dev-"
        + service + "@" + release["digest"]}]}}}
    release["manifest_sha256"] = hashlib.sha256(canonical(resources)).hexdigest()
    return {"resources": resources}


def test_unsigned_bundle_or_other_digest_rejected(release):
    bundle = bundle_for(release)
    validate_bundle(release, bundle)
    bundle["resources"]["service.json"]["metadata"]["namespace"] = "orders-api"
    with pytest.raises(PermissionError):
        validate_bundle(release, bundle)


@pytest.mark.skipif(shutil.which("gpg") is None, reason="Existing GPG required; never install tools in a test")
def test_real_local_git_signature_and_untrusted_parent_rejected(tmp_path, monkeypatch, release, ai, gate):
    # All Git repositories, keys and commits below are ephemeral local test fixtures.
    home = tmp_path / "gnupg"
    home.mkdir(mode=0o700)
    monkeypatch.setenv("GNUPGHOME", str(home))
    def call(args, cwd=None):
        result = subprocess.run(args, check=False, cwd=cwd, capture_output=True, text=True, timeout=30)
        assert result.returncode == 0, "Isolated Git/GPG operation failed"
        return result.stdout.strip()
    call(["gpg", "--batch", "--pinentry-mode", "loopback", "--passphrase", "",
          "--quick-generate-key", "Isolated test <nobody@example.invalid>", "ed25519", "sign", "1d"])
    fingerprint = next(line.split(":")[9] for line in call(["gpg", "--with-colons", "--list-secret-keys"]).splitlines()
                       if line.startswith("fpr:"))
    origin, repo = tmp_path / "origin.git", tmp_path / "writer"
    call(["git", "init", "--bare", "--quiet", str(origin)])
    call(["git", "clone", "--quiet", str(origin), str(repo)])
    call(["git", "config", "user.name", "Isolated test"], repo)
    call(["git", "config", "user.email", "nobody@example.invalid"], repo)
    call(["git", "checkout", "-b", "main"], repo)
    folder = repo / "gitops/apps/orders-worker/dev"
    folder.mkdir(parents=True)
    (folder / "deployment.json").write_text("{}")
    call(["git", "add", "."], repo)
    call(["git", "commit", "--quiet", "-m", "test: reviewed baseline"], repo)
    anchor = call(["git", "rev-parse", "HEAD"], repo)
    call(["git", "push", "--quiet", "origin", "main"], repo)
    bundle = bundle_for(release)
    receipt = gate.decide(release, ai, now=NOW)
    publisher = SignedGitPublisher(repo, str(origin), fingerprint, anchor, gate.key.public_key())
    revision = publisher.publish(release, bundle, receipt, now=NOW)
    verify_commit(repo, revision, fingerprint)
    assert call(["git", "rev-parse", "refs/heads/main"], origin) == revision
    # Another writer advances main without the gate key. Do not sign over that content.
    (repo / "unapproved.txt").write_text("test-only")
    call(["git", "add", "unapproved.txt"], repo)
    call(["git", "-c", "commit.gpgsign=false", "commit", "--quiet", "-m", "test: unauthorized parent"], repo)
    call(["git", "push", "--quiet", "origin", "HEAD:main"], repo)
    with pytest.raises(PermissionError):
        publisher.publish(release, bundle, receipt, now=NOW)
    if shutil.which("gpgconf"):
        subprocess.run(["gpgconf", "--kill", "gpg-agent"], check=False, capture_output=True, timeout=5)
