from unittest.mock import Mock

import pytest

from automation.github_access import GitHubApp


def test_compare_separator_is_not_path_traversal():
    app=GitHubApp({},'cosmotech-orders-worker')
    app.headers=Mock(return_value={})
    app.request=Mock(return_value={'files':[]})
    assert app.api('compare/'+'a'*40+'...'+'b'*40)=={'files':[]}
    app.request.assert_called_once()


@pytest.mark.parametrize('path',['../secrets','contents/%2e%2e/secret','contents/%252e%252e/secret',
                                 '//evil.invalid','https://evil.invalid/x','contents/a#fragment','contents/a\n'])
def test_absolute_encoded_and_dot_segment_paths_remain_blocked(path):
    app=GitHubApp({},'cosmotech-orders-worker')
    app.headers=Mock(return_value={});app.request=Mock()
    with pytest.raises(ValueError):app.api(path)
    app.request.assert_not_called()
