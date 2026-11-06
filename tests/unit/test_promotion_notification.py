import pytest

from automation.promotion_notification import notification


def test_email_is_notification_with_release_reference_and_no_bearer_token(release, ai):
    message = notification(release, ai, recipient="owner@example.test",
                           approval_origin="https://approval.example.test")
    content = message.get_content()
    assert release["digest"] in content and release["commit"] in content
    assert "Authenticated review:" in content and "?token=" not in content
    assert "email reply does not approve" in content


def test_insecure_approval_url_rejected(release, ai):
    with pytest.raises(ValueError):
        notification(release, ai, recipient="owner@example.test", approval_origin="http://example.test")
