import json
from unittest.mock import Mock

import pytest
from test_promotion_gate import NOW

from automation.bedrock_adviser import MODEL_ID, assess


def response(advice):
    return {"stopReason": "end_turn", "output": {"message": {"content": [{"text": json.dumps(advice)}]}},
            "usage": {"inputTokens": 1000, "outputTokens": 150}, "ResponseMetadata": {"RequestId": "mock"}}


def test_role_bedrock_adapter_bounded_sanitized_and_schema_checked(release, ai):
    ai["model_version"] = MODEL_ID
    client = Mock()
    client.converse.return_value = response(ai)
    result = assess(release, now=NOW, client=client)
    assert result["advice"] == ai and result["usage"]["outputTokens"] == 150
    params = client.converse.call_args.kwargs
    assert params["inferenceConfig"]["maxTokens"] == 512
    assert "tools" not in params and "toolConfig" not in params
    assert "execution_id" not in params["messages"][0]["content"][0]["text"]


@pytest.mark.parametrize("failure", ["timeout", "truncated", "invalid", "wrong_model", "tool"])
def test_bedrock_failure_never_becomes_approval(release, ai, failure):
    ai["model_version"] = MODEL_ID
    client = Mock()
    value = response(ai)
    if failure == "timeout":
        client.converse.side_effect = TimeoutError()
    elif failure == "truncated":
        value["stopReason"] = "max_tokens"
    elif failure == "invalid":
        value["output"]["message"]["content"] = [{"text": "{}"}]
    elif failure == "wrong_model":
        ai["model_version"] = "unverified"
        value = response(ai)
    else:
        value["output"]["message"]["content"] = [{"toolUse": {"name": "promote"}}]
    client.converse.return_value = value
    with pytest.raises((ValueError, TimeoutError)):
        assess(release, now=NOW, client=client)


def test_no_paid_call_before_enforcement_verified(release):
    release["enforcement_verified"] = False
    client = Mock()
    with pytest.raises(PermissionError):
        assess(release, now=NOW, client=client)
    client.converse.assert_not_called()
