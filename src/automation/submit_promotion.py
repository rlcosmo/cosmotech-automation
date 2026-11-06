"""Fixed SSM document entrypoint on the existing ITSM host."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from automation.promotion_request import enqueue
from automation.promotion_runtime import KestraClient


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--service", choices=["orders-api", "orders-worker"], required=True)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--digest", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--run-attempt", required=True)
    args = parser.parse_args()
    source = Path("/opt/cosmotech/secrets/itsm/runtime.env")
    values = dict(line.split("=", 1) for line in source.read_text().splitlines()
                  if "=" in line and not line.startswith("#"))
    client = KestraClient("https://kestra.cosmotechsolutions.com.br",
                         values["KESTRA_ADMIN_USERNAME"], values["KESTRA_ADMIN_PASSWORD"])
    result = enqueue("/var/lib/cosmotech-promotion/requests.db", client, service=args.service,
                     commit=args.commit, digest=args.digest, run_id=args.run_id, run_attempt=args.run_attempt)
    print(json.dumps(result))


if __name__ == "__main__":
    main()
