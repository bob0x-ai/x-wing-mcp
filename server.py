#!/usr/bin/env python3
"""X (Twitter) MCP server exposing x-wing write tools and x_data read tools.

Transport: stdio. The server never prints to stdout outside the MCP protocol.
"""

import os
from pathlib import Path
from typing import Any, Optional

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parent
os.environ["X_WING_ENV_PATH"] = str(REPO_ROOT / ".env")
load_dotenv(REPO_ROOT / ".env", override=True)

from mcp.server import MCPServer
from mcp.shared.exceptions import MCPError
from mcp.types import ErrorData, INTERNAL_ERROR, INVALID_PARAMS

import analytics_client
import x_client
import xdata.server

mcp = MCPServer("x-wing")

# Application-specific MCP error codes (JSON-RPC server error range -32000..-32099)
REPLY_POLICY_ERROR = -32001
AUTH_ERROR = -32002
THREAD_PARTIAL_ERROR = -32003
ANALYTICS_SERVICE_ERROR = -32004


def _tool_error(message: str, code: int = INTERNAL_ERROR, data: Any = None) -> MCPError:
    """Build an MCP tool error with a structured message."""
    return MCPError(code, message, data)


def _api_result(result: dict) -> dict:
    """Normalize an API result dict for MCP tool return."""
    if not result:
        return {"success": True}
    return {"success": True, **result}


def _partial_data(exc: BaseException) -> Optional[dict]:
    """Extract any partial thread post IDs carried by an exception."""
    post_ids = getattr(exc, "post_ids", None)
    if post_ids:
        return {"partial_post_ids": list(post_ids)}
    return None


def _run_api(func):
    """Execute an api_* core and map x-wing exceptions to MCP tool errors."""
    try:
        return func()
    except analytics_client.AnalyticsServiceError as exc:
        raise _tool_error(
            f"[ANALYTICS_SERVICE] {exc}",
            code=ANALYTICS_SERVICE_ERROR,
            data={"remediation": "Configure and start the local x-analytics service; this MCP tool never fetches X analytics directly."},
        ) from exc
    except x_client.ReplyPolicyError as exc:
        raise _tool_error(
            f"[REPLY_POLICY] {exc}",
            code=REPLY_POLICY_ERROR,
            data=_partial_data(exc),
        ) from exc
    except x_client.XWingThreadPartialError as exc:
        raise _tool_error(
            f"[THREAD_PARTIAL] {exc}",
            code=THREAD_PARTIAL_ERROR,
            data=_partial_data(exc),
        ) from exc
    except x_client.XWingValidationError as exc:
        raise _tool_error(str(exc), code=INVALID_PARAMS) from exc
    except x_client.XWingError as exc:
        raise _tool_error(f"[AUTH] {exc}", code=AUTH_ERROR) from exc
    except Exception as exc:
        msg = str(exc)
        if not msg:
            msg = type(exc).__name__
        partial = getattr(exc, "post_ids", None)
        if partial:
            msg = f"{msg} (partial post IDs created: {list(partial)})"
        raise _tool_error(msg, code=INTERNAL_ERROR, data=_partial_data(exc)) from exc


@mcp.tool()
def post(text: str, reply_to: Optional[str] = None, quote: Optional[str] = None, media: Optional[str] = None) -> dict:
    """Create a new X post."""
    return _run_api(
        lambda: _api_result(
            x_client.api_post(text=text, reply_to=reply_to, quote=quote, media=media)
        )
    )


@mcp.tool()
def create_thread(texts: list[str]) -> dict:
    """Create a multi-post thread."""
    return _run_api(lambda: _api_result(x_client.api_thread(texts=texts)))


@mcp.tool()
def like(post_id: str) -> dict:
    """Like a post."""
    return _run_api(lambda: _api_result(x_client.api_like(post_id=post_id)))


@mcp.tool()
def repost(post_id: str) -> dict:
    """Repost a post."""
    return _run_api(lambda: _api_result(x_client.api_repost(post_id=post_id)))


@mcp.tool()
def follow(target_user_id: str) -> dict:
    """Follow a user by ID."""
    return _run_api(lambda: _api_result(x_client.api_follow(target_user_id=target_user_id)))


@mcp.tool()
def unfollow(source_user_id: str, target_user_id: str) -> dict:
    """Unfollow a user by source and target IDs."""
    return _run_api(
        lambda: _api_result(
            x_client.api_unfollow(
                source_user_id=source_user_id, target_user_id=target_user_id
            )
        )
    )


@mcp.tool()
def dm_send(text: str, user: Optional[str] = None, conversation: Optional[str] = None) -> dict:
    """Send a direct message to a user or conversation."""
    return _run_api(
        lambda: _api_result(
            x_client.api_dm_send(user=user, conversation=conversation, text=text)
        )
    )


@mcp.tool()
def x_read_own_analytics(
    view: str = "overview",
    window_days: int = 28,
    post_id: Optional[str] = None,
    sort: Optional[str] = None,
    limit: Optional[int] = None,
    days: Optional[int] = None,
) -> dict:
    """Read a compact stored owned-account analytics view from x-analytics.

    READ-ONLY: this tool never opens the analytics database or calls X. The
    default ``overview`` includes freshness, account movement, cumulative post
    metrics, and top posts. Use ``posts``, ``post_history`` (with post_id),
    ``followers``, or ``status`` for a narrower view. Changes are always
    explicitly bounded by their observed timestamps.

    ``days`` remains a backwards-compatible alias for window_days.
    """
    return _run_api(
        lambda: _api_result(
            analytics_client.read_analytics(
                view=view,
                window_days=window_days,
                post_id=post_id,
                sort=sort,
                limit=limit,
                days=days,
            )
        )
    )


xdata.server.create_mcp_server(mcp=mcp)


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
