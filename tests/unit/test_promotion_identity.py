import base64
from datetime import timedelta

import pytest
from test_promotion_gate import NOW

from automation.promotion_identity import authenticated_human, challenge

KEY = b"isolated-test-key-not-a-real-secret" * 2


def test_authenticated_identity_comes_from_header_not_form(release):
    auth = "Basic " + base64.b64encode(b"owner:test-only").decode()
    record = authenticated_human(release, authorization=auth, csrf=challenge(release, KEY, NOW),
                                 key=KEY, result="APPROVE", now=NOW, allowed_principals={"owner"},
                                 authenticate=lambda user, password: (user, password) == ("owner", "test-only"))
    assert record["principal"] == "owner" and record["execution_id"] == release["execution_id"]


@pytest.mark.parametrize("scenario", ["unauthorized", "bad_password", "expired", "other_digest", "forged"])
def test_email_link_or_forged_claim_cannot_authorize(release, scenario):
    token = challenge(release, KEY, NOW)
    user = "attacker" if scenario == "unauthorized" else "owner"
    auth = "Basic " + base64.b64encode((user + ":test-only").encode()).decode()
    now = NOW + timedelta(minutes=11) if scenario == "expired" else NOW
    if scenario == "other_digest":
        release["digest"] = "sha256:" + "f" * 64
    if scenario == "forged":
        token = token[:-5] + "wrong"
    with pytest.raises(PermissionError):
        authenticated_human(release, authorization=auth, csrf=token, key=KEY,
                            result="APPROVE", now=now, allowed_principals={"owner"},
                            authenticate=lambda *args: scenario != "bad_password")
