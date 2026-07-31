"""Tests for the x-wing MCP server."""

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

REPO_ROOT = Path(__file__).parent.parent


def test_env_path_is_repo_local():
    """Importing x_client without X_WING_ENV_PATH points at repo .env."""
    # Use a subprocess so the module re-import does not pollute this test process.
    env = os.environ.copy()
    env.pop("X_WING_ENV_PATH", None)
    code = "import x_client; print(x_client.env_path)"
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        env=env,
    )
    assert result.returncode == 0, result.stderr
    assert Path(result.stdout.strip()) == REPO_ROOT / ".env"


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
        # x-wing write tools
        "post",
        "create_thread",
        "like",
        "repost",
        "follow",
        "unfollow",
        "dm_send",
        # x_data read tools
        "x_fetch_urls",
        "x_read_user_posts",
        "x_search_posts",
        "x_read_owned_timeline",
        "x_read_mentions",
        "x_read_thread",
        "x_read_replies",
        "x_read_quotes",
        "x_read_follow_graph",
        "x_read_article",
        "x_collect_posts",
        "x_data_status",
        "x_data_healthcheck",
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


def test_read_tools_require_max_cost_usd(tools):
    for name in [
        "x_fetch_urls",
        "x_read_user_posts",
        "x_search_posts",
        "x_read_owned_timeline",
        "x_read_mentions",
        "x_read_thread",
        "x_read_replies",
        "x_read_quotes",
        "x_read_follow_graph",
        "x_read_article",
        "x_collect_posts",
    ]:
        tool = next(t for t in tools if t.name == name)
        assert "max_cost_usd" in tool.inputSchema.get("required", []), f"{name} should require max_cost_usd"


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


@pytest.mark.parametrize("tool_name,args", [
    ("like", {"post_id": "123"}),
    ("repost", {"post_id": "123"}),
    ("follow", {"target_user_id": "456"}),
    ("unfollow", {"source_user_id": "me", "target_user_id": "456"}),
    ("dm_send", {"user": "@test", "text": "hi"}),
])
def test_write_tool_auth_error_returns_labeled_error(mock_client, tool_name, args):
    """The five tools that previously used string error codes must return [AUTH] errors."""
    import server
    import x_client

    with patch.object(
        x_client,
        f"api_{tool_name}",
        side_effect=x_client.XWingError("auth failed"),
    ):
        with pytest.raises(Exception) as exc_info:
            asyncio.run(server.mcp.call_tool(tool_name, args))

    message = str(exc_info.value)
    assert "[AUTH]" in message
    assert "auth failed" in message
    assert "validation error" not in message.lower()
    assert "Input should be a valid integer" not in message


def test_dm_send_validation_missing_recipient(mock_client):
    """dm_send with neither user nor conversation returns a validation error, not [AUTH]."""
    import server

    with pytest.raises(Exception) as exc_info:
        asyncio.run(server.mcp.call_tool("dm_send", {"text": "hi"}))

    message = str(exc_info.value)
    assert "[AUTH]" not in message
    assert "user or conversation" in message


def test_create_thread_validation_empty_text(mock_client):
    """create_thread with an empty string text returns a validation error."""
    import server

    with pytest.raises(Exception) as exc_info:
        asyncio.run(server.mcp.call_tool("create_thread", {"texts": [""]}))

    assert "non-empty" in str(exc_info.value)


def test_create_thread_partial_failure_returns_post_ids(mock_client):
    """A thread that fails after the first post carries the partial IDs in the error."""
    import server
    import x_client

    first_response = MagicMock()
    first_response.data = MagicMock()
    first_response.data.id = "post_0"

    class SecondPostFails(Exception):
        pass

    mock_client.posts.create.side_effect = [first_response, SecondPostFails("boom")]

    with pytest.raises(Exception) as exc_info:
        asyncio.run(server.mcp.call_tool("create_thread", {"texts": ["a", "b"]}))

    message = str(exc_info.value)
    assert "post_0" in message
    assert "partial" in message.lower() or "THREAD_PARTIAL" in message

