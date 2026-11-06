"""Exclusive signed GitOps writer; no execution of code from the delivery repository."""

from __future__ import annotations

import fcntl
import hashlib
import json
import re
import subprocess
from datetime import datetime
from pathlib import Path

from automation.promotion_gate import canonical, verify_receipt

FILES = {"deployment.json": "Deployment", "configmap.json": "ConfigMap",
         "service.json": "Service", "hpa.json": "HorizontalPodAutoscaler", "ingress.json": "Ingress"}


def verify_commit(repository: Path, revision: str, fingerprint: str, *, env: dict | None = None) -> None:
    """Require a cryptographic VALIDSIG from the dedicated trusted signing key."""
    if not re.fullmatch(r"[a-f0-9]{40}", revision) or not re.fullmatch(r"(?:[A-F0-9]{40}|[A-F0-9]{64})", fingerprint):
        raise ValueError("Invalid immutable commit or GPG fingerprint")
    result = subprocess.run(["git", "-c", "core.hooksPath=/dev/null", "verify-commit", "--raw", revision],
                            check=False, cwd=repository, capture_output=True, text=True, timeout=15, env=env)
    valid = []
    for line in result.stderr.splitlines():
        if line.startswith("[GNUPG:] VALIDSIG "):
            fields = line.split()
            valid.extend([fields[2], fields[-1]])
    if result.returncode or fingerprint not in valid:
        raise PermissionError("Untrusted GitOps commit signature")


def validate_bundle(release: dict, bundle: dict) -> None:
    resources = bundle.get("resources", {})
    if set(resources) != set(FILES) or hashlib.sha256(canonical(resources)).hexdigest() != release["manifest_sha256"]:
        raise PermissionError("Bundle does not match the authorized manifest hash")
    service = release["service"]
    for filename, kind in FILES.items():
        resource = resources[filename]
        if (resource.get("kind") != kind or resource.get("metadata", {}).get("namespace") != service
                or resource["metadata"].get("name") != (
                    "orders-config" if kind == "ConfigMap" else service + "-dev" if kind == "Ingress" else service)):
            raise PermissionError("Resource escaped the authorized application boundary")
    containers = resources["deployment.json"]["spec"]["template"]["spec"]["containers"]
    container = next(c for c in containers if c["name"] == service)
    expression = (r"[0-9]{12}\.dkr\.ecr\.us-east-1\.amazonaws\.com/aws-eks-istio-lab-dev-"
                  + re.escape(service) + r"(?::sha-" + release["commit"] + r")?@"
                  + re.escape(release["digest"]))
    if not re.fullmatch(expression, container["image"]):
        raise PermissionError("Image is not the tested immutable application image")
    if any(c["name"] != service for c in containers):
        raise PermissionError("Unexpected companion container in application-source bundle")


class SignedGitPublisher:
    """Called only by the consumed Kestra decision; credentials live on the publisher host."""

    def __init__(self, repository: Path, expected_origin: str, fingerprint: str,
                 reviewed_anchor: str, receipt_public_key, *, git_env: dict | None = None):
        self.repo = repository.resolve()
        self.origin, self.fingerprint, self.anchor = expected_origin, fingerprint, reviewed_anchor
        self.public_key = receipt_public_key
        self.git_env = git_env

    def git(self, *args: str) -> str:
        result = subprocess.run(["git", "-c", "core.hooksPath=/dev/null", *args], check=False, cwd=self.repo,
                                text=True, capture_output=True, timeout=60, env=self.git_env)
        if result.returncode:
            # stderr may contain credential helper details; never return it to CI or Kestra.
            raise RuntimeError("GitOps operation failed; reconcile trusted host evidence")
        return result.stdout.strip()

    def publish(self, release: dict, bundle: dict, receipt: dict, *, now: datetime) -> str:
        """A lost push response is ambiguous; the gate journal forbids blind replay."""
        verify_receipt(receipt, self.public_key, release, now=now)
        validate_bundle(release, bundle)
        if self.git("remote", "get-url", "origin") != self.origin:
            raise PermissionError("Unexpected GitOps remote")
        # The lock belongs to trusted host state, never to a candidate manifest.
        with (self.repo / ".git" / "cosmotech-publisher.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            if self.git("status", "--porcelain"):
                raise PermissionError("Publisher worktree must be clean")
            self.git("fetch", "--quiet", "origin", "main")
            parent = self.git("rev-parse", "origin/main")
            if parent != self.anchor:
                verify_commit(self.repo, parent, self.fingerprint, env=self.git_env)
            self.git("checkout", "--quiet", "--detach", parent)
            folder = self.repo / "gitops" / "apps" / release["service"] / "dev"
            for filename, resource in bundle["resources"].items():
                path = folder / filename
                # Reject symlink writes even when a branch outside the gate adds one.
                if any(component.is_symlink() for component in [path, *path.parents] if component != self.repo.parent):
                    raise PermissionError("Symlink paths are forbidden")
                path.resolve().relative_to(self.repo)
                if not path.parent.is_dir():
                    raise PermissionError("Application directory missing")
                path.write_text(json.dumps(resource, indent=2) + "\n")
            proof = self.repo / "gitops" / "releases" / (release["service"] + "-dev.json")
            if any(component.is_symlink() for component in [proof, *proof.parents] if component != self.repo.parent):
                raise PermissionError("Symlink release proof is forbidden")
            proof.resolve().relative_to(self.repo)
            proof.parent.mkdir(parents=True, exist_ok=True)
            proof.write_text(json.dumps({"application": release["service"],
                "source_repository": "rlcosmo/cosmotech-" + release["service"],
                "source_sha": release["commit"], "resources": bundle["resources"],
                "promotion": {"release_id": receipt["payload"]["release_id"],
                              "kestra_execution_id": release["execution_id"]}}, indent=2) + "\n")
            self.git("add", "--", str(folder.relative_to(self.repo)), str(proof.relative_to(self.repo)))
            paths = self.git("diff", "--cached", "--name-only").splitlines()
            expected = {str((folder / name).relative_to(self.repo)) for name in FILES}
            expected.add(str(proof.relative_to(self.repo)))
            if not paths or not set(paths) <= expected:
                raise PermissionError("Unexpected or empty promotion diff")
            self.git("-c", "user.name=CosmoTech Promotion", "-c", "user.email=promotion@localhost",
                     "-c", f"user.signingkey={self.fingerprint}", "-c", "commit.gpgsign=true",
                     "commit", "--quiet", "-m", f"feat({release['service']}): promote approved release")
            revision = self.git("rev-parse", "HEAD")
            verify_commit(self.repo, revision, self.fingerprint, env=self.git_env)
            # A non-fast-forward update aborts rather than rebasing over unreviewed content.
            self.git("push", "--quiet", "origin", "HEAD:main")
            return revision
