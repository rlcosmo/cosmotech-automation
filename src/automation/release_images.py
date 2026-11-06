"""Canonical image aliases derived from trusted source and CI build numbers."""

import re


def version_tag(service: str, version: str, build: str) -> str:
    short = {"orders-worker": "worker", "orders-api": "api"}[service]
    tag = short + "-" + version.replace("+", "-") + "-" + build
    if not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}", tag) or not re.fullmatch(
        r"[0-9]{1,20}", build
    ):
        raise ValueError("Invalid image version label")
    return tag
