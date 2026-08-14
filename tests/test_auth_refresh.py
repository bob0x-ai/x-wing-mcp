"""Tests for guarded OAuth refresh and auth retry behavior."""

import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import x_client


@pytest.fixture
def isolated_auth(monkeypatch, tmp_path):
    state_path = tmp_path / "auth-state.json"
    lock_path = tmp_path / "auth-state.lock"
    env_path = tmp_path / ".env"
    env_path.write_text("X_OAUTH2_ACCESS_TOKEN=old_access\nX_OAUTH2_REFRESH_TOKEN=old_refresh\n")
    monkeypatch.setattr(x_client, "AUTH_STATE_PATH", state_path)
    monkeypatch.setattr(x_client, "AUTH_LOCK_PATH", lock_path)
    monkeypatch.setattr(x_client, "env_path", env_path)
    monkeypatch.setenv("X_OAUTH2_CLIENT_ID", "canonical_client")
    monkeypatch.setenv("X_CLIENT_ID", "legacy_client")
    monkeypatch.setenv("X_OAUTH2_CLIENT_SECRET", "canonical_secret")
    monkeypatch.setenv("X_CLIENT_SECRET", "legacy_secret")
    monkeypatch.setenv("X_OAUTH2_ACCESS_TOKEN", "old_access")
    monkeypatch.setenv("X_ACCESS_TOKEN", "legacy_access")
    monkeypatch.setenv("X_OAUTH2_REFRESH_TOKEN", "old_refresh")
    monkeypatch.setenv("X_REFRESH_TOKEN", "legacy_refresh")
    monkeypatch.setenv(
        "X_OAUTH2_SCOPES",
        "offline.access tweet.read tweet.write like.write users.read dm.write follows.write",
    )
    monkeypatch.setenv(
        "X_SCOPES",
        "offline.access tweet.read tweet.write like.write users.read dm.write follows.write",
    )
    return env_path, state_path


class Response:
    def __init__(self, status_code=200, payload=None, text="", headers=None):
        self.status_code = status_code
        self._payload = payload or {}
        self.text = text
        self.headers = headers or {}

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            import requests

            raise requests.HTTPError(f"HTTP {self.status_code}")


def test_env_value_prefers_canonical_alias(monkeypatch):
    monkeypatch.setenv("X_OAUTH2_ACCESS_TOKEN", "canonical")
    monkeypatch.setenv("X_ACCESS_TOKEN", "legacy")

    assert x_client._env_value("X_OAUTH2_ACCESS_TOKEN", "X_ACCESS_TOKEN") == "canonical"


def test_valid_current_access_token_skips_token_endpoint(isolated_auth):
    with patch("requests.get", return_value=Response(200)), patch("requests.post") as post:
        token = x_client.refresh_access_token("client", "secret", "refresh")

    assert token == "old_access"
    post.assert_not_called()


def test_refresh_adopts_token_rotated_by_sibling_process(isolated_auth):
    env_path, _ = isolated_auth
    env_path.write_text(
        "X_OAUTH2_ACCESS_TOKEN=rotated_access\n"
        "X_OAUTH2_REFRESH_TOKEN=rotated_refresh\n"
    )

    with patch("requests.get", return_value=Response(200)), patch("requests.post") as post:
        token = x_client.refresh_access_token("stale_client", "stale_secret", "stale_refresh")

    assert token == "rotated_access"
    post.assert_not_called()


def test_successful_refresh_persists_and_validates_new_tokens(isolated_auth):
    env_path, _ = isolated_auth
    token_payload = {
        "access_token": "new_access",
        "refresh_token": "new_refresh",
        "scope": "tweet.read tweet.write users.read offline.access",
        "expires_in": 7200,
    }

    with patch("requests.get", side_effect=[Response(401), Response(200)]) as get, \
         patch("requests.post", return_value=Response(200, token_payload)) as post:
        token = x_client.refresh_access_token("client", "secret", "old_refresh")

    assert token == "new_access"
    assert os.environ["X_OAUTH2_ACCESS_TOKEN"] == "new_access"
    assert os.environ["X_OAUTH2_REFRESH_TOKEN"] == "new_refresh"
    assert os.environ["X_OAUTH2_SCOPES"] == "tweet.read tweet.write users.read offline.access"
    assert os.environ["X_SCOPES"] == "tweet.read tweet.write users.read offline.access"
    env_text = env_path.read_text()
    assert "X_OAUTH2_ACCESS_TOKEN=new_access" in env_text
    assert "X_OAUTH2_REFRESH_TOKEN=new_refresh" in env_text
    assert "X_OAUTH2_SCOPES=tweet.read tweet.write users.read offline.access" in env_text
    assert "X_SCOPES=tweet.read tweet.write users.read offline.access" in env_text
    assert post.call_count == 1
    assert get.call_count == 2


def test_second_refresh_inside_cooldown_does_not_rotate(isolated_auth, monkeypatch):
    _, state_path = isolated_auth
    state_path.write_text('{"last_refresh_at": 999999.0}\n')
    monkeypatch.setattr(x_client.time, "time", lambda: 1000000.0)

    with patch("requests.get", return_value=Response(200)), patch("requests.post") as post:
        token = x_client.refresh_access_token("client", "secret", "old_refresh")

    assert token == "old_access"
    post.assert_not_called()


def test_invalid_current_token_inside_cooldown_aborts_without_rotating(isolated_auth, monkeypatch):
    _, state_path = isolated_auth
    state_path.write_text('{"last_refresh_at": 999999.0}\n')
    monkeypatch.setattr(x_client.time, "time", lambda: 1000000.0)

    with patch("requests.get", return_value=Response(401)), patch("requests.post") as post:
        token = x_client.refresh_access_token("client", "secret", "old_refresh")

    assert token is None
    post.assert_not_called()


def test_auth_wrapper_refreshes_and_retries_once(isolated_auth):
    first_client = MagicMock(name="first_client")
    retry_client = MagicMock(name="retry_client")
    operation = MagicMock(side_effect=[Exception("401 Unauthorized"), "ok"])

    with patch("x_client.get_client", return_value=first_client), \
         patch("x_client.refresh_access_token", return_value="new_access") as refresh, \
         patch("x_client.Client", return_value=retry_client) as client_cls:
        result = x_client.run_auth_operation(operation)

    assert result == "ok"
    assert operation.call_count == 2
    operation.assert_any_call(first_client)
    operation.assert_any_call(retry_client)
    refresh.assert_called_once_with("canonical_client", "canonical_secret", "old_refresh")
    client_cls.assert_called_once_with(access_token="new_access")


def test_auth_wrapper_retry_non_auth_failure_does_not_refresh_again(isolated_auth):
    operation = MagicMock(side_effect=[Exception("401 Unauthorized"), RuntimeError("rate limited")])

    with patch("x_client.get_client", return_value=MagicMock()), \
         patch("x_client.refresh_access_token", return_value="new_access") as refresh, \
         patch("x_client.Client", return_value=MagicMock()):
        with pytest.raises(RuntimeError, match="rate limited"):
            x_client.run_auth_operation(operation)

    assert operation.call_count == 2
    refresh.assert_called_once()


def test_auth_wrapper_rejects_missing_write_scope_before_retry(isolated_auth, monkeypatch):
    monkeypatch.setenv("X_OAUTH2_SCOPES", "tweet.read users.read offline.access")
    monkeypatch.setenv("X_SCOPES", "tweet.read users.read offline.access")

    operation = MagicMock()

    with pytest.raises(x_client.XWingError):
        x_client.run_auth_operation(
            operation,
            required_scopes={"tweet.write"},
            action_name="posting",
        )

    operation.assert_not_called()


def test_auth_wrapper_raises_reply_policy_error_on_forbidden_reply(isolated_auth):
    reply_error = Response(
        403,
        text=(
            '{"detail":"Reply to this conversation is not allowed because you have not been '
            'mentioned or otherwise engaged by the author of the post you are replying to.",'
            '"type":"about:blank","title":"Forbidden","status":403}'
        ),
    )
    import requests

    http_error = requests.HTTPError("403 Client Error")
    http_error.response = reply_error
    operation = MagicMock(side_effect=http_error)

    with patch("x_client.get_client", return_value=MagicMock()), \
         patch("x_client.refresh_access_token") as refresh, \
         patch("x_client.Client", return_value=MagicMock()):
        with pytest.raises(x_client.ReplyPolicyError, match="does not allow replies"):
            x_client.run_auth_operation(operation)

    refresh.assert_not_called()


def test_auth_wrapper_refreshes_on_401_http_error(isolated_auth):
    import requests

    http_error = requests.HTTPError("401 Client Error")
    http_error.response = Response(401, text='{"detail":"Expired token"}')

    operation = MagicMock(side_effect=[http_error, "ok"])

    with patch("x_client.get_client", return_value=MagicMock()), \
         patch("x_client.refresh_access_token", return_value="new_access") as refresh, \
         patch("x_client.Client", return_value=MagicMock()):
        result = x_client.run_auth_operation(operation)

    assert result == "ok"
    refresh.assert_called_once()
