"""Real service adapters, injected for tests. No network writes happen at import."""

from __future__ import annotations

import base64
import json
import sqlite3
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from typing import Any

from automation.promotion_notification import notification


class KestraClient:
    """Use existing authenticated Kestra; never accept an API origin from release data."""

    def __init__(self, origin: str, username: str, password: str):
        parsed = urllib.parse.urlsplit(origin)
        if parsed.scheme != "https" or parsed.username or parsed.query or parsed.fragment:
            raise ValueError("Trusted HTTPS Kestra origin required")
        self.origin = origin.rstrip("/")
        self.authorization = "Basic " + base64.b64encode((username + ":" + password).encode()).decode()

    def request(self, path: str, *, method: str = "GET", body: bytes | None = None,
                content_type: str = "application/json") -> Any:
        if not path.startswith("/api/v1/") or ".." in path or "//" in path:
            raise ValueError("Only fixed Kestra API paths are allowed")
        request = urllib.request.Request(self.origin + path, data=body, method=method,
            headers={"Authorization": self.authorization, "Content-Type": content_type})
        with urllib.request.urlopen(request, timeout=15) as response:
            raw = response.read(2_000_000)
            return json.loads(raw) if raw else {}

    @staticmethod
    def authenticate_user(origin: str, username: str, password: str) -> bool:
        """Check the human's actual credentials, not the service account's session."""
        client = KestraClient(origin, username, password)
        try:
            result = client.request("/api/v1/configs")
            return isinstance(result, dict) and isinstance(result.get("version"), str)
        except urllib.error.HTTPError as error:
            if error.code in {401, 403}:
                return False
            raise

    def require_authorizing_execution(self, release: dict, *, flow_revision: int,
                                      ai_enabled: bool = False) -> dict:
        identifier = urllib.parse.quote(release["execution_id"], safe="")
        execution = self.request("/api/v1/main/executions/" + identifier)
        if (execution.get("id") != release["execution_id"]
                or execution.get("namespace") != "cosmotech.promotion"
                or execution.get("flowId") != "promotion_gate"
                or execution.get("flowRevision") != flow_revision
                or execution.get("state", {}).get("current") not in {"RUNNING", "SUCCESS"}):
            raise PermissionError("Kestra is not authorizing this promotion")
        inputs = execution.get("inputs", {})
        if any(inputs.get(key) != release[key] for key in ("service", "environment", "commit", "digest", "run_id")):
            raise PermissionError("Kestra execution targets another release")
        tasks = execution.get("taskRunList", [])
        successful = {task.get("taskId") for task in tasks if task.get("state", {}).get("current") == "SUCCESS"}
        required = {"verified_evidence", "deterministic_policy"}
        if type(ai_enabled) is not bool:
            raise PermissionError("Invalid trusted AI mode")
        if ai_enabled:
            required.add("bedrock_assessment")
        if not required <= successful:
            raise PermissionError("Required Kestra tasks did not complete")
        return execution


class SnsApprovalNotifier:
    """Reuse the existing email topic only when its full confirmed recipient set matches."""

    def __init__(self, client: Any, topic_arn: str, recipient: str, approval_origin: str):
        self.client, self.topic = client, topic_arn
        self.recipient, self.origin = recipient, approval_origin

    def send(self, release: dict, advice: dict | None, *, test_only: bool = False, change_id: int | None = None) -> str:
        pages = self.client.get_paginator("list_subscriptions_by_topic").paginate(TopicArn=self.topic)
        subscriptions = [item for page in pages for item in page.get("Subscriptions", [])]
        if (not subscriptions or any(
                item.get("Protocol") != "email"
                or item.get("Endpoint", "").casefold() != self.recipient.casefold()
                or item.get("SubscriptionArn") in {None, "PendingConfirmation", "Deleted"}
                for item in subscriptions)):
            raise PermissionError("SNS topic recipients do not match the authorized notification")
        email = notification(release, advice, recipient=self.recipient, approval_origin=self.origin, change_id=change_id)
        if test_only:
            email.replace_header("Subject", "[TESTE SEM DEPLOY] CosmoTech approval portal")
            email.set_content("TESTE OPERACIONAL. Nenhuma imagem sera promovida por esta solicitacao.\n\n" + email.get_content())
        result = self.client.publish(TopicArn=self.topic, Subject=str(email["Subject"]),
                                     Message=email.get_content())
        return result["MessageId"]  # Accepted by SNS is not proof of delivery to the mailbox.


class CanaryExecutor:
    """Apply persisted controller effects only to fixed Worker resources.

    run is a trusted command runner. The runtime binding must use the approved
    EKS context. No shell interpolation, arbitrary namespaces or image setters.
    """

    def __init__(self, database: str, context: str, run, promote_stable, notify):
        self.database, self.context, self.run = database, context, run
        self.promote_stable, self.notify = promote_stable, notify
        with sqlite3.connect(database) as db:
            db.execute("CREATE TABLE IF NOT EXISTS applied_effects (id TEXT, generation INTEGER, state TEXT, PRIMARY KEY(id,generation))")

    def command(self, *args: str) -> None:
        self.run(["kubectl", "--context", self.context, "-n", "orders-worker", *args])

    def apply(self, identity: str, *, now: datetime) -> dict:
        with sqlite3.connect(self.database) as db:
            row = db.execute("SELECT state FROM rollouts WHERE id=?", (identity,)).fetchone()
            if row is None:
                raise PermissionError("No authorized Worker rollout")
            state = json.loads(row[0])
            if release_id_from_state(state) != identity:
                raise PermissionError("Rollout identity mismatch")
            effect = state["effect"]
            generation = state["generation"]
            existing = db.execute("SELECT state FROM applied_effects WHERE id=? AND generation=?",
                                  (identity, generation)).fetchone()
            if existing:
                if existing[0] != "success":
                    raise PermissionError("Ambiguous effect: reconcile before retry")
                return state
            db.execute("INSERT INTO applied_effects VALUES (?,?,'dispatching')", (identity, generation))
        mode = effect.get("mode")
        if mode not in {"run", "drain"}:
            raise ValueError("Unknown consumer mode")
        expires = effect.get("expires_at", 0)
        if mode == "run" and not now.timestamp() < expires <= now.timestamp() + 180:
            raise PermissionError("Stale consumption lease")
        lease = json.dumps({"data": {"lease.json": json.dumps({"mode": mode, "expires_at": expires})}})
        # Drain is published before any scaling or stable restoration.
        self.command("patch", "configmap", "orders-worker-canary-control", "--type=merge", "-p", lease)
        if "restore_stable" in effect:
            if effect["restore_stable"] != state["stable"]:
                raise PermissionError("Rollback digest mismatch")
            self.promote_stable(identity, state["stable"], "rollback")
        if "promote_stable" in effect:
            if effect["promote_stable"] != state["candidate"]:
                raise PermissionError("Candidate digest mismatch")
            self.promote_stable(identity, state["candidate"], "promotion")
        if "replicas" in effect:
            replicas = effect["replicas"]
            if type(replicas) is not int or replicas not in {0, 1, 2}:
                raise PermissionError("Invalid canary capacity")
            if replicas == 0 and state["stage"] not in {"COMPLETE", "ROLLED_BACK"}:
                raise PermissionError("Cannot remove a canary before drain is proven")
            self.command("scale", "deployment", "orders-worker-canary", "--replicas=" + str(replicas))
        if effect.get("notify"):
            self.notify({"release_id": identity, "stage": state["stage"], "reason": state["reason"]})
        with sqlite3.connect(self.database) as db:
            db.execute("UPDATE applied_effects SET state='success' WHERE id=? AND generation=?",
                       (identity, generation))
        return state


def release_id_from_state(state: dict) -> str:
    """Only controller-owned SQLite state may be passed here, never request JSON."""
    return state["id"]
