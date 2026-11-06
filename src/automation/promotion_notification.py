"""Build a notification only; never send email or approve from a link."""

from email.message import EmailMessage
from urllib.parse import urlsplit

from automation.promotion_gate import CRITICALITY, release_id


def notification(release: dict, ai: dict | None, *, recipient: str, approval_origin: str) -> EmailMessage:
    """Origin is trusted server configuration, never a caller-controlled URL."""
    url = urlsplit(approval_origin)
    if url.scheme != "https" or not url.hostname or url.username or url.password or url.query or url.fragment:
        raise ValueError("Approval origin must be HTTPS without embedded credentials or tokens")
    link = approval_origin.rstrip("/") + "/releases/" + release_id(release)
    assessment = ("Bedrock: DISABLED; deterministic policy and human review apply.\n"
                  if ai is None else
                  f"AI advisory: {ai['recommendation']}\nReason: {ai['justification']}\n")
    message = EmailMessage()
    message["To"] = recipient
    message["Subject"] = f"CosmoTech: review {release['service']} / {release['environment']}"
    message.set_content(
        f"Application: {release['service']}\nCriticality: {CRITICALITY[release['service']]}\n"
        f"Environment: {release['environment']}\nCurrent: {release['current_digest']}\n"
        f"Candidate: {release['digest']}\nCommit: {release['commit']}\n"
        f"Tests passed: {release['checks']['tests_passed']}\n"
        f"Trivy passed: {release['checks']['scan_passed']}\n"
        f"{assessment}"
        f"Kestra execution: {release['execution_id']}\nAuthenticated review: {link}\n\n"
        "Notification only. An email reply does not approve the release. "
        "The approval page must authenticate the authorized human identity.\n"
    )
    return message
