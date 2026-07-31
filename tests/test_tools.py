"""Tests for the x-actions MCP server."""

import asyncio
import importlib
import json
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

REPO_ROOT = Path(__file__).parent.parent


def test_env_path_is_repo_local():
    """Importing x_client without X_WING_ENV_PATH points at repo .env."""
    # Force a fresh import of x_client with the default env path.
    env_var = "X_WING_ENV_PATH"
    old_env = os.environ.get(env_var)
    try:
        os.environ.pop(env_var, None)
        # Clear cached module to exercise module-level env resolution.
        sys.modules.pop("x_client", None)
        import x_client

        assert x_client.env_path == REPO_ROOT / ".env"
    finally:
        if old_env is not None:
            os.environ[env_var] = old_env
        else:
            os.environ.pop(env_var, None)


def test_server_env_path_override_is_set():
    """server.py sets X_WING_ENV_PATH to the repo .env before importing x_client."""
    import server
    import x_client

    assert os.environ.get("X_WING_ENV_PATH") == str(REPO_ROOT / ".env")
    assert x_client.env_path == REPO_ROOT / ".env"


@pytest.fixture
def tools():
    import server

    return asyncio.run(server.mcp.list_tools())


@pytest.fixture
def tool_names(tools):
    return {t.name for t in tools}


def test_tools_count_and_names(tool_names):
    expected = {
        "post",
        "create_thread",
        "like",
        "repost",
        "follow",
        "unfollow",
        "dm_send",
    }
    assert tool_names == expected


def test_post_schema(tools):
    post = next(t for t in tools if t.name == "post")
    schema = post.inputSchema
    assert schema["required"] == ["text"]
    assert schema["properties"]["text"]["type"] == "string"
    assert schema["properties"]["reply_to"]["anyOf"] == [{"type": "string"}, {"type": "null"}]
    assert schema["properties"]["quote"]["anyOf"] == [{"type": "string"}, {"type": "null"}]
    assert schema["properties"]["media"]["anyOf"] == [{"type": "string"}, {"type": "null"}]


def test_create_thread_schema(tools):
    thread = next(t for t in tools if t.name == "create_thread")
    schema = thread.inputSchema
    assert schema["required"] == ["texts"]
    assert schema["properties"]["texts"]["type"] == "array"


def test_like_repost_schemas(tools):
    like = next(t for t in tools if t.name == "like")
    repost = next(t for t in tools if t.name == "repost")
    assert like.inputSchema["properties"]["post_id"]["type"] == "string"
    assert repost.inputSchema["properties"]["post_id"]["type"] == "string"


def test_follow_unfollow_schemas(tools):
    follow = next(t for t in tools if t.name == "follow")
    unfollow = next(t for t in tools if t.name == "unfollow")
    assert follow.inputSchema["required"] == ["target_user_id"]
    assert unfollow.inputSchema["required"] == ["source_user_id", "target_user_id"]


def test_dm_send_schema(tools):
    dm = next(t for t in tools if t.name == "dm_send")
    schema = dm.inputSchema
    assert schema["required"] == ["text"]
    assert schema["properties"]["user"]["anyOf"] == [{"type": "string"}, {"type": "null"}]
    assert schema["properties"]["conversation"]["anyOf"] == [{"type": "string"}, {"type": "null"}]


@pytest.fixture
def mock_client():
    """Provide a mock X API client patched into the x_client module."""
    with patch("x_client.get_client") as mock_get_client:
        client = MagicMock()
        mock_get_client.return_value = client
        yield client


def test_post_handler_success(mock_client):
    import server

    mock_response = MagicMock()
    mock_response.data = MagicMock()
    mock_response.data.id = "post_123"
    mock_response.data.text = "hello"
    mock_client.posts.create.return_value = mock_response

    result = asyncio.run(server.mcp.call_tool("post", {"text": "hello"}))
    # FastMCP returns a list of TextContent; unwrap for the JSON payload.
    payload = json.loads(result[0].text)
    assert payload["success"] is True
    assert payload["data"]["id"] == "post_123"


def test_create_thread_handler_success(mock_client):
    import server

    counter = [0]

    def side_effect(body):
        mock_response = MagicMock()
        mock_response.data = MagicMock()
        mock_response.data.id = f"post_{counter[0]}"
        counter[0] += 1
        return mock_response

    mock_client.posts.create.side_effect = side_effect

    result = asyncio.run(server.mcp.call_tool("create_thread", {"texts": ["a", "b"]}))
    payload = json.loads(result[0].text)
    assert payload["success"] is True
    assert payload["post_ids"] == ["post_0", "post_1"]


def test_like_handler_success(mock_client):
    import server

    with patch("x_client.get_my_user_id", return_value="me"):
        mock_response = MagicMock()
        mock_response.data = MagicMock()
        mock_response.data.liked = True
        mock_client.users.like_post.return_value = mock_response

        result = asyncio.run(server.mcp.call_tool("like", {"post_id": "123"}))
        payload = json.loads(result[0].text)
        assert payload["success"] is True
        assert payload["data"]["liked"] is True


def test_repost_handler_success(mock_client):
    import server

    with patch("x_client.get_my_user_id", return_value="me"):
        mock_response = MagicMock()
        mock_response.data = MagicMock()
        mock_response.data.reposted = True
        mock_client.users.repost_post.return_value = mock_response

        result = asyncio.run(server.mcp.call_tool("repost", {"post_id": "123"}))
        payload = json.loads(result[0].text)
        assert payload["success"] is True
        assert payload["data"]["reposted"] is True


def test_follow_handler_success(mock_client):
    import server

    with patch("x_client.get_my_user_id", return_value="me"):
        mock_response = MagicMock()
        mock_response.data = MagicMock()
        mock_response.data.following = True
        mock_client.users.follow_user.return_value = mock_response

        result = asyncio.run(server.mcp.call_tool("follow", {"target_user_id": "456"}))
        payload = json.loads(result[0].text)
        assert payload["success"] is True
        assert payload["data"]["following"] is True


def test_unfollow_handler_success(mock_client):
    import server

    mock_response = MagicMock()
    mock_response.data = MagicMock()
    mock_response.data.following = False
    mock_client.users.unfollow_user.return_value = mock_response

    result = asyncio.run(server.mcp.call_tool(
        "unfollow", {"source_user_id": "me", "target_user_id": "456"}
    ))
    payload = json.loads(result[0].text)
    assert payload["success"] is True
    assert payload["data"]["following"] is False


def test_dm_send_handler_success(mock_client):
    import server

    with patch("x_client.resolve_user_id", return_value="987"):
        mock_response = MagicMock()
        mock_response.data = MagicMock()
        mock_response.data.id = "msg_123"
        mock_client.dm.send_message.return_value = mock_response

        result = asyncio.run(server.mcp.call_tool(
            "dm_send", {"user": "@test", "text": "hi"}
        ))
        payload = json.loads(result[0].text)
        assert payload["success"] is True
        assert payload["data"]["id"] == "msg_123"


def test_reply_policy_error_returns_tool_error(mock_client):
    import server
    from x_client import ReplyPolicyError

    mock_client.posts.create.side_effect = ReplyPolicyError("not allowed")

    with pytest.raises(Exception) as exc_info:
        asyncio.run(server.mcp.call_tool("post", {"text": "reply", "reply_to": "123"}))

    assert "REPLY_POLICY" in str(exc_info.value)


def test_missing_scope_returns_tool_error_not_exit(mock_client):
    import server

    with patch.dict(
        os.environ,
        {"X_OAUTH2_SCOPES": "tweet.read users.read", "X_SCOPES": "tweet.read users.read"},
    ):
        with pytest.raises(Exception) as exc_info:
            asyncio.run(server.mcp.call_tool("post", {"text": "hello"}))

    assert "AUTH" in str(exc_info.value)
    assert "tweet.write" in str(exc_info.value)


def test_refresh_failure_returns_tool_error_not_exit(mock_client):
    import server
    import x_client

    # Simulate an auth failure that triggers refresh, then a refresh failure.
    mock_client.posts.create.side_effect = Exception("401 Unauthorized")

    with patch("x_client._is_auth_failure", return_value=True), \
         patch("x_client._refresh_from_env", side_effect=x_client.XWingError("refresh failed")):
        with pytest.raises(Exception) as exc_info:
            asyncio.run(server.mcp.call_tool("post", {"text": "hello"}))

    assert "AUTH" in str(exc_info.value)
    assert "refresh failed" in str(exc_info.value)
