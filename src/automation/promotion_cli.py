"""Offline validation/evaluation entry point for the prepared Kestra runner."""

from __future__ import annotations

import argparse
import json
import os
from datetime import UTC, datetime
from pathlib import Path

from automation.promotion_gate import ai_input, evaluate, validate_release


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["prepare", "evaluate", "bedrock"])
    parser.add_argument("--release", type=Path, required=True)
    parser.add_argument("--ai", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--require-provider-metadata", action="store_true")
    args = parser.parse_args()
    now = datetime.now(UTC)
    release = json.loads(args.release.read_text())
    if args.mode == "bedrock":
        if os.getenv("BEDROCK_PROMOTION_ENABLED") != "true":
            raise SystemExit("Paid inference not enabled in trusted runtime configuration")
        from automation.bedrock_adviser import assess, runtime_client
        assessment = assess(release, now=now, client=runtime_client())
        args.output.write_text(json.dumps(assessment["advice"], indent=2) + "\n")
        print(json.dumps({"model_id": assessment["model_id"], "usage": assessment["usage"]}))
        return
    if args.mode == "prepare":
        validate_release(release, now)
        if release["enforcement_verified"] is not True:
            raise SystemExit("Mandatory enforcement not verified; do not invoke AI")
        value = ai_input(release)
    else:
        try:
            ai = json.loads(args.ai.read_text()) if args.ai else None
        except (ValueError, OSError):
            ai = None
        if args.require_provider_metadata:
            actual_model = os.getenv("TRUSTED_MODEL_VERSION")
            if not actual_model or not isinstance(ai, dict) or ai.get("model_version") != actual_model:
                ai = None
        mode = os.getenv("BEDROCK_PROMOTION_ENABLED", "false")
        if mode not in {"true", "false"}:
            raise SystemExit("Invalid trusted Bedrock configuration")
        decision, reason = evaluate(release, ai, now=now, ai_enabled=mode == "true")
        value = {"decision": decision, "reason": reason}
        # Kestra script output variables; no credentials or evidence body in logs.
        print("::" + json.dumps({"outputs": value}) + "::")
    args.output.write_text(json.dumps(value, indent=2) + "\n")


if __name__ == "__main__":
    main()
