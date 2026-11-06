import pytest

from automation.promotion_notification import notification


def test_email_is_notification_with_release_reference_and_no_bearer_token(release, ai):
    message = notification(release, ai, recipient="owner@example.test",
                           approval_origin="https://approval.example.test")
    content = message.get_content()
    assert release["commit"][:12] in content and "Portal autenticado: https://" in content
    assert "Portal autenticado:" in content and "?token=" not in content
    assert "Responder ao e-mail não aprova" in content


def test_insecure_approval_url_rejected(release, ai):
    with pytest.raises(ValueError):
        notification(release, ai, recipient="owner@example.test", approval_origin="http://example.test")
