"""Wire Kestra authority, human decision, policy, replay journal and exclusive dispatch."""

from __future__ import annotations

import hashlib
import sqlite3
from datetime import datetime

from automation.gitops_publisher import validate_bundle
from automation.promotion_gate import canonical, evaluate, release_id


class PromotionCoordinator:
    """Only trusted server evidence reaches this boundary, never CI-supplied checks."""

    def __init__(self, gate, kestra, portal, notifier, publisher, start_worker, *, flow_revision: int, changes=None, owner: str | None = None):
        self.gate, self.kestra, self.portal = gate, kestra, portal
        self.notifier, self.publisher, self.start_worker = notifier, publisher, start_worker
        self.flow_revision = flow_revision
        self.changes, self.owner = changes, owner
        with sqlite3.connect(gate.database) as db:
            db.execute("CREATE TABLE IF NOT EXISTS notifications (id TEXT PRIMARY KEY, state TEXT, message_id TEXT)")

    def complete(self, release: dict, bundle: dict, advice: dict | None, *, now: datetime) -> dict:
        """Human approval records no deploy. Only a verified Kestra execution dispatches."""
        self.kestra.require_authorizing_execution(
            release, flow_revision=self.flow_revision, ai_enabled=self.gate.ai_enabled)
        validate_bundle(release, bundle)
        decision, reason = evaluate(release, advice, now=now, ai_enabled=self.gate.ai_enabled)
        change = None
        if self.changes is not None:
            from automation.gmud import change_identity
            change = self.changes.ensure(release, owner=self.owner,
                ci_links=["https://github.com/rlcosmo/cosmotech-" + release["service"] + "/actions/runs/" + release["run_id"]],
                implementation_plan="Signed GitOps publisher; Argo exact revision and health; Worker Canary 1/2 with metrics.",
                rollback_plan="Authenticated rollback within 12h, exact current digest check; Worker drain/inflight proof before restore.",
                now=now)
            decision, reason = self.changes.policy.decide(release, self.changes.history(), now=now)
            if change["phase"] not in {"CREATED", "HUMAN_REVIEW"}:
                raise PermissionError("GMUD already dispatched, denied or requires reconciliation")
            if decision in {"BLOCK", "REJECT"}:
                self.changes.transition(change_identity(release), "REJECTED" if decision == "REJECT" else "FAILED", now=now, details={"reason": reason})
                raise PermissionError(reason)
        identity = release_id(release)
        human = self.portal.decision(identity)
        if decision == "HUMAN_REVIEW" and human is None:
            self.portal.register(release, advice)
            if change is not None and change["phase"] == "CREATED":
                self.changes.transition(change["id"], "HUMAN_REVIEW", now=now, details={"reason": reason})
            with sqlite3.connect(self.gate.database) as db:
                old = db.execute("SELECT state FROM notifications WHERE id=?", (identity,)).fetchone()
                if old is None:
                    db.execute("INSERT INTO notifications VALUES (?,'dispatching',NULL)", (identity,))
            if old is None:
                message_id = (self.notifier.send(release, advice, change_id=change["change_id"])
                              if change is not None else self.notifier.send(release, advice))
                with sqlite3.connect(self.gate.database) as db:
                    db.execute("UPDATE notifications SET state='accepted',message_id=? WHERE id=?", (message_id, identity))
            elif old[0] != "accepted":
                raise PermissionError("Ambiguous notification; reconcile instead of duplicate email")
            return {"decision": "HUMAN_REVIEW", "release_id": identity, "reason": reason}
        try:
            receipt = self.gate.decide(release, advice, human=human, now=now,
                                       require_human=decision == "HUMAN_REVIEW")
        except PermissionError as error:
            if change is not None and str(error) == "human_decision":
                self.changes.transition(change["id"], "REJECTED", now=now,
                    details={"reason": "authenticated_human_rejected", "principal": human["principal"]})
                self.portal.set_context(identity,{"phase":"REJECTED","change_id":change["change_id"],"criticality":change["criticality"]})
            raise
        if change is not None:
            self.changes.transition(change["id"], "APPROVED", now=now,
                details={"principal": receipt["payload"]["approver"], "reason": receipt["payload"]["reason"],
                         "receipt_release_id": receipt["payload"]["release_id"],
                         "receipt_sha256": hashlib.sha256(canonical(receipt)).hexdigest(),
                         "evidence_observed_at": release["observed_at"]})
            self.portal.set_context(identity,{"phase":"APPROVED","change_id":change["change_id"],"criticality":change["criticality"]})
        def dispatch(authorized):
            if change is not None:
                self.changes.transition(change["id"], "DEPLOYING", now=now,
                                        details={"execution_id": authorized["execution_id"]})
            if authorized["service"] == "orders-worker":
                self.start_worker(authorized, bundle, receipt)
            else:
                self.publisher.publish(authorized, bundle, receipt, now=now)
        self.gate.promote(receipt, release, dispatch, now=now)
        return {"decision": "APPROVE", "release_id": identity, "dispatch": "accepted",
                "delivery_validated": False}
