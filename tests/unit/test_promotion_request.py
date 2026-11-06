from unittest.mock import Mock

import pytest

from automation.promotion_request import enqueue


def test_ci_request_is_deduplicated_and_contains_no_approval_claim(tmp_path, release):
    client = Mock()
    client.request.return_value = {"id": "kestra-server-generated-id"}
    args = {"service": release["service"], "commit": release["commit"], "digest": release["digest"], "run_id": "123"}
    database = str(tmp_path / "requests.db")
    first = enqueue(database, client, **args)
    assert enqueue(database, client, **args) == first
    assert first["promotion_validated"] is False
    client.request.assert_called_once()
    assert b"enforcement_verified" not in client.request.call_args.kwargs["body"]


def test_ambiguous_kestra_dispatch_cannot_spawn_another_execution(tmp_path, release):
    client = Mock()
    client.request.side_effect = TimeoutError()
    args = {"service": release["service"], "commit": release["commit"], "digest": release["digest"], "run_id": "123"}
    database = str(tmp_path / "requests.db")
    with pytest.raises(TimeoutError):
        enqueue(database, client, **args)
    with pytest.raises(PermissionError):
        enqueue(database, client, **args)
    assert client.request.call_count == 1
