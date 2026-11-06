"""Conservative manifest classification before sending sanitized evidence to AI."""

from __future__ import annotations

import copy
import hashlib

from automation.promotion_gate import canonical


def classify_manifests(before: list[dict], after: list[dict]) -> dict:
    """Only image and release label changes are eligible for the low-risk path.

    Any other change, including a new resource, routes to human review. This is
    intentionally stricter than guessing that a new environment variable is safe.
    Raw manifests never go into the returned AI-facing summary.
    """
    def index(resources):
        indexed = {}
        for resource in resources:
            metadata = resource["metadata"]
            key = (resource["apiVersion"], resource["kind"], metadata.get("namespace"), metadata["name"])
            if key in indexed:
                raise ValueError("Duplicate resource identity")
            indexed[key] = resource
        return indexed

    left, right = index(before), index(after)
    sensitive = set(left) != set(right)
    changed = [key[1] for key in set(left) ^ set(right)]
    for key in sorted(set(left) & set(right), key=str):
        old, new = copy.deepcopy(left[key]), copy.deepcopy(right[key])
        if old == new:
            continue
        changed.append(key[1])
        if key[1] == "Deployment":
            for resource in (old, new):
                for container in resource["spec"]["template"]["spec"]["containers"]:
                    container.pop("image", None)
                    container["env"] = [e for e in container.get("env", [])
                                        if e["name"] != "RELEASE_VERSION"]
        if old != new:
            sensitive = True
    return {"sensitive_changes": sensitive, "changed_kinds": sorted(set(changed)),
            "manifest_sha256": hashlib.sha256(canonical(after)).hexdigest()}
