"""Wire Kestra authority, human decision, policy, replay journal and exclusive dispatch."""

from __future__ import annotations

import sqlite3
from datetime import datetime

from automation.gitops_publisher import validate_bundle
from automation.promotion_gate import evaluate, release_id


class PromotionCoordinator:
    """Only trusted server evidence reaches this boundary, never CI-supplied checks."""

    def __init__(self, gate, kestra, portal, notifier, publisher, start_worker, *, flow_revision: int):
        self.gate, self.kestra, self.portal = gate, kestra, portal
        self.notifier, self.publisher, self.start_worker = notifier, publisher, start_worker
        self.flow_revision = flow_revision
        with sqlite3.connect(gate.database) as db:
            db.execute("CREATE TABLE IF NOT EXISTS notifications (id TEXT PRIMARY KEY, state TEXT, message_id TEXT)")

    def complete(self, release: dict, bundle: dict, advice: dict | None, *, now: datetime) -> dict:
        """Human approval records no deploy. Only a verified Kestra execution dispatches."""
        self.kestra.require_authorizing_execution(
            release, flow_revision=self.flow_revision, ai_enabled=self.gate.ai_enabled)
        validate_bundle(release, bundle)
        decision, reason = evaluate(release, advice, now=now, ai_enabled=self.gate.ai_enabled)
        identity = release_id(release)
        human = self.portal.decision(identity)
        if decision == "HUMAN_REVIEW" and human is None:
            self.portal.register(release, advice)
            with sqlite3.connect(self.gate.database) as db:
                old = db.execute("SELECT state FROM notifications WHERE id=?", (identity,)).fetchone()
                if old is None:
                    db.execute("INSERT INTO notifications VALUES (?,'dispatching',NULL)", (identity,))
            if old is None:
                message_id = self.notifier.send(release, advice)
                with sqlite3.connect(self.gate.database) as db:
                    db.execute("UPDATE notifications SET state='accepted',message_id=? WHERE id=?", (message_id, identity))
            elif old[0] != "accepted":
                raise PermissionError("Ambiguous notification; reconcile instead of duplicate email")
            return {"decision": "HUMAN_REVIEW", "release_id": identity, "reason": reason}
        receipt = self.gate.decide(release, advice, human=human, now=now)
        def dispatch(authorized):
            if authorized["service"] == "orders-worker":
                self.start_worker(authorized, bundle, receipt)
            else:
                self.publisher.publish(authorized, bundle, receipt, now=now)
        self.gate.promote(receipt, release, dispatch, now=now)
        return {"decision": "APPROVE", "release_id": identity, "dispatch": "accepted",
                "delivery_validated": False}
