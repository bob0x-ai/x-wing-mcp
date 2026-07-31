#!/usr/bin/env python3
"""Write-only X (Twitter) MCP server exposing a subset of x-wing commands.

Transport: stdio. The server never prints to stdout outside the MCP protocol.
"""

import json
import os
import sys
from pathlib import Path
from typing import Any, Optional

REPO_ROOT = Path(__file__).resolve().parent
os.environ["X_WING_ENV_PATH"] = str(REPO_ROOT / ".env")

from mcp.server.fastmcp import FastMCP
from mcp.shared.exceptions import McpError
from mcp.types import ErrorData, INTERNAL_ERROR, INVALID_PARAMS, INVALID_REQUEST

import x_client

mcp = FastMCP("x-wing")


def _tool_error(message: str, code: int = INTERNAL_ERROR) -> McpError:
    """Build an MCP tool error with a structured message."""
    return McpError(ErrorData(code=code, message=message, data=None))


def _api_result(result: dict) -> dict:
    """Normalize an API result dict for MCP tool return."""
    if not result:
        return {"success": True}
    return {"success": True, **result}


@mcp.tool()
def post(text: str, reply_to: Optional[str] = None, quote: Optional[str] = None, media: Optional[str] = None) -> dict:
    """Create a new X post."""
    try:
        result = x_client.api_post(None, text=text, reply_to=reply_to, quote=quote, media=media)
        return _api_result(result)
    except x_client.ReplyPolicyError as exc:
        raise _tool_error(f"[REPLY_POLICY] {exc}", code=INVALID_REQUEST) from exc
    except x_client.XWingError as exc:
        raise _tool_error(f"[AUTH] {exc}", code=INVALID_PARAMS) from exc


@mcp.tool()
def create_thread(texts: list[str]) -> dict:
    """Create a multi-post thread."""
    try:
        result = x_client.api_thread(None, texts=texts)
        return _api_result(result)
    except x_client.ReplyPolicyError as exc:
        raise _tool_error(f"[REPLY_POLICY] {exc}", code=INVALID_REQUEST) from exc
    except x_client.XWingError as exc:
        raise _tool_error(f"[AUTH] {exc}", code=INVALID_PARAMS) from exc


@mcp.tool()
def like(post_id: str) -> dict:
    """Like a post."""
    try:
        result = x_client.api_like(None, post_id=post_id)
        return _api_result(result)
    except x_client.XWingError as exc:
        raise _tool_error(f"[AUTH] {exc}", code="INVALID_REQUEST") from exc


@mcp.tool()
def repost(post_id: str) -> dict:
    """Repost a post."""
    try:
        result = x_client.api_repost(None, post_id=post_id)
        return _api_result(result)
    except x_client.XWingError as exc:
        raise _tool_error(f"[AUTH] {exc}", code="INVALID_REQUEST") from exc


@mcp.tool()
def follow(target_user_id: str) -> dict:
    """Follow a user by ID."""
    try:
        result = x_client.api_follow(None, target_user_id=target_user_id)
        return _api_result(result)
    except x_client.XWingError as exc:
        raise _tool_error(f"[AUTH] {exc}", code="INVALID_REQUEST") from exc


@mcp.tool()
def unfollow(source_user_id: str, target_user_id: str) -> dict:
    """Unfollow a user by source and target IDs."""
    try:
        result = x_client.api_unfollow(None, source_user_id=source_user_id, target_user_id=target_user_id)
        return _api_result(result)
    except x_client.XWingError as exc:
        raise _tool_error(f"[AUTH] {exc}", code="INVALID_REQUEST") from exc


@mcp.tool()
def dm_send(text: str, user: Optional[str] = None, conversation: Optional[str] = None) -> dict:
    """Send a direct message to a user or conversation."""
    try:
        result = x_client.api_dm_send(None, user=user, conversation=conversation, text=text)
        return _api_result(result)
    except x_client.XWingError as exc:
        raise _tool_error(f"[AUTH] {exc}", code="INVALID_REQUEST") from exc


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
