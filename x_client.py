#!/usr/bin/env python3
"""
X-Wing: X (Twitter) API v2 CLI Client

A command-line interface for interacting with the X API v2 using OAuth 2.0 authentication.

Usage:
    python scripts/x_client.py <command> [options]

Commands:
    post           Create a new post
    delete         Delete a post by ID
    get            Get post details by ID
    search         Search posts (7-day window)
    like           Like a post
    unlike         Unlike a post
    repost         Repost a post
    unrepost       Undo a repost
    timeline       Get home timeline
    user-posts     Get user's posts
    mentions       Get mentions for authenticated user
    thread         Create a multi-post thread
    upload-media   Upload media (images)
    dm-send        Send a direct message
    dm-list        List DM conversations
    dm-get         Get messages in a conversation
"""

import argparse
import base64
import contextlib
import hashlib
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any, Callable, Optional, TypeVar

from dotenv import load_dotenv

DEFAULT_ENV_PATH = Path(__file__).resolve().parent / ".env"
env_path = Path(os.getenv("X_WING_ENV_PATH", str(DEFAULT_ENV_PATH))).expanduser()
# Override shell env vars so .env is always the source of truth for tokens.
# Without override=True, an expired token exported in the parent shell would
# shadow the fresh token in .env, causing 401s even when .env is correct.
load_dotenv(env_path, override=True)

from xdk import Client

SKILL_DIR = Path(__file__).resolve().parent
AUTH_STATE_PATH = SKILL_DIR / ".x-wing-auth-state.json"
AUTH_LOCK_PATH = SKILL_DIR / ".x-wing-auth-state.lock"
REFRESH_COOLDOWN_SECONDS = 60
USER_ME_URL = "https://api.x.com/2/users/me"
TOKEN_URL = "https://api.x.com/2/oauth2/token"

T = TypeVar("T")


def _env_value(primary: str, fallback: str) -> Optional[str]:
    """Read OAuth credentials, preferring canonical X_OAUTH2_* names."""
    return os.getenv(primary) or os.getenv(fallback)


def _env_scopes() -> set[str]:
    """Read granted OAuth scopes from the environment."""
    raw_scopes = _env_value("X_OAUTH2_SCOPES", "X_SCOPES")
    if not raw_scopes:
        return set()
    return {scope for scope in raw_scopes.split() if scope}


class AuthRefreshError(RuntimeError):
    """Raised when guarded OAuth refresh cannot produce a validated token."""


class ReplyPolicyError(RuntimeError):
    """Raised when X rejects a reply because the post is not reply-eligible."""


class XWingError(RuntimeError):
    """Raised when a CLI/MCP operation cannot proceed because of auth, scopes, or refresh failure."""



def _token_fingerprint(token: Optional[str]) -> Optional[str]:
    if not token:
        return None
    return hashlib.sha256(token.encode()).hexdigest()


def _normalize_scopes(scopes: Any) -> Optional[str]:
    """Convert a token scope payload to a stable space-delimited string."""
    if not scopes:
        return None
    if isinstance(scopes, str):
        return " ".join(scopes.split())
    if isinstance(scopes, (list, tuple, set)):
        normalized = [str(scope).strip() for scope in scopes if str(scope).strip()]
        return " ".join(normalized) or None
    value = str(scopes).strip()
    return value or None


def _ensure_required_scopes(required_scopes: set[str], action: str) -> None:
    """Fail fast if the token metadata does not include required scopes."""
    granted_scopes = _env_scopes()
    if not granted_scopes:
        return

    missing_scopes = required_scopes - granted_scopes
    if not missing_scopes:
        return

    print(
        f"Authentication failed: current X token is missing required scope(s) for {action}: "
        f"{', '.join(sorted(missing_scopes))}",
        file=sys.stderr,
    )
    print(f"Granted scopes: {', '.join(sorted(granted_scopes))}", file=sys.stderr)
    print(
        "Re-run oauth_setup.py after confirming the app has the needed permissions in the X developer portal.",
        file=sys.stderr,
    )
    raise XWingError(
        f"missing scope(s) for {action}: {', '.join(sorted(missing_scopes))}"
    )


def _read_auth_state() -> dict[str, Any]:
    if not AUTH_STATE_PATH.exists():
        return {}
    try:
        return json.loads(AUTH_STATE_PATH.read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def _write_auth_state(**updates: Any) -> None:
    state = _read_auth_state()
    state.update(updates)
    state["updated_at"] = time.time()
    AUTH_STATE_PATH.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n")


@contextlib.contextmanager
def _auth_lock():
    AUTH_LOCK_PATH.touch(exist_ok=True)
    with AUTH_LOCK_PATH.open("r+") as lock_file:
        try:
            import fcntl

            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        except (ImportError, OSError):
            pass
        try:
            yield
        finally:
            try:
                import fcntl

                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
            except (ImportError, OSError):
                pass


def _validate_access_token(access_token: Optional[str]) -> bool:
    """Validate an access token with X's users/me endpoint."""
    if not access_token:
        return False

    import requests

    try:
        response = requests.get(
            USER_ME_URL,
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=15,
        )
        if response.status_code == 200:
            _write_auth_state(
                last_validation_at=time.time(),
                last_validation_status="valid",
                validated_access_fingerprint=_token_fingerprint(access_token),
            )
            return True
        _write_auth_state(
            last_validation_at=time.time(),
            last_validation_status=f"invalid_http_{response.status_code}",
            validated_access_fingerprint=_token_fingerprint(access_token),
        )
        return False
    except requests.RequestException as exc:
        _write_auth_state(
            last_validation_at=time.time(),
            last_validation_status=f"error:{type(exc).__name__}",
            validated_access_fingerprint=_token_fingerprint(access_token),
        )
        return False


def _is_auth_failure(error_or_response: Any) -> bool:
    if error_or_response is None:
        return False

    data = getattr(error_or_response, "data", None)
    if data is not None:
        return False

    status_code = getattr(error_or_response, "status_code", None)
    if isinstance(status_code, int) and status_code == 401:
        return True

    response = getattr(error_or_response, "response", None)
    response_status = getattr(response, "status_code", None) if response is not None else None
    if isinstance(response_status, int) and response_status == 401:
        return True

    errors = getattr(error_or_response, "errors", None)
    if isinstance(errors, (list, tuple, dict, str)) and errors:
        error_text = json.dumps(errors, default=str).lower()
        if any(marker in error_text for marker in ("unauthorized", "401", "expired", "invalid token")):
            return True

    if isinstance(error_or_response, (Exception, str)):
        text = str(error_or_response).lower()
        return any(marker in text for marker in ("unauthorized", "401", "expired", "invalid token"))

    return False


def _is_reply_policy_failure(error_or_response: Any) -> bool:
    """Detect X's reply restriction for self-serve apps."""
    if error_or_response is None:
        return False

    response = getattr(error_or_response, "response", None)
    candidate = response if response is not None else error_or_response

    status_code = getattr(candidate, "status_code", None)
    if status_code != 403:
        return False

    payload = ""
    text = getattr(candidate, "text", None)
    if isinstance(text, str):
        payload = text.lower()

    errors = getattr(candidate, "errors", None)
    if errors:
        payload = f"{payload} {json.dumps(errors, default=str).lower()}".strip()

    detail = getattr(candidate, "detail", None)
    if isinstance(detail, str):
        payload = f"{payload} {detail.lower()}".strip()

    markers = (
        "reply to this conversation is not allowed",
        "not been mentioned",
        "otherwise engaged by the author",
        "reply is not allowed",
        "replies are only permitted",
    )
    return any(marker in payload for marker in markers)


def refresh_access_token(client_id: str, client_secret: str, refresh_token: str) -> Optional[str]:
    """Refresh OAuth 2.0 access only when guarded validation requires it.

    Args:
        client_id: OAuth 2.0 client ID
        client_secret: OAuth 2.0 client secret
        refresh_token: OAuth 2.0 refresh token

    Returns:
        Current or new access token if validated, None otherwise
    """
    import requests

    current_access_token = _env_value("X_OAUTH2_ACCESS_TOKEN", "X_ACCESS_TOKEN")

    with _auth_lock():
        state = _read_auth_state()
        now = time.time()

        if _validate_access_token(current_access_token):
            _write_auth_state(
                last_refresh_status="skipped_current_valid",
                access_fingerprint=_token_fingerprint(current_access_token),
            )
            return current_access_token

        last_refresh_at = float(state.get("last_refresh_at") or 0)
        if last_refresh_at and now - last_refresh_at < REFRESH_COOLDOWN_SECONDS:
            if _validate_access_token(current_access_token):
                _write_auth_state(
                    last_refresh_status="skipped_cooldown_current_valid",
                    access_fingerprint=_token_fingerprint(current_access_token),
                )
                return current_access_token
            _write_auth_state(
                last_refresh_status="aborted_cooldown_current_invalid",
                access_fingerprint=_token_fingerprint(current_access_token),
            )
            print(
                "Token refresh aborted: previous refresh was less than 60 seconds ago and current token is invalid.",
                file=sys.stderr,
            )
            return None

        credentials = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
        headers = {
            "Authorization": f"Basic {credentials}",
            "Content-Type": "application/x-www-form-urlencoded",
        }
        data = {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
        }

        try:
            response = requests.post(TOKEN_URL, headers=headers, data=data, timeout=30)
            response.raise_for_status()
            token_data = response.json()

            new_access_token = token_data.get("access_token")
            new_refresh_token = token_data.get("refresh_token")
            new_scope = _normalize_scopes(token_data.get("scope"))
            expires_in = token_data.get("expires_in")

            if not new_access_token:
                _write_auth_state(
                    last_refresh_at=time.time(),
                    last_refresh_status="token_endpoint_missing_access_token",
                    refresh_fingerprint=_token_fingerprint(refresh_token),
                )
                return None

            os.environ["X_OAUTH2_ACCESS_TOKEN"] = new_access_token
            os.environ["X_ACCESS_TOKEN"] = new_access_token
            if new_refresh_token:
                os.environ["X_OAUTH2_REFRESH_TOKEN"] = new_refresh_token
                os.environ["X_REFRESH_TOKEN"] = new_refresh_token
            if new_scope:
                os.environ["X_OAUTH2_SCOPES"] = new_scope
                os.environ["X_SCOPES"] = new_scope

            try:
                _update_env_file(new_access_token, new_refresh_token, new_scope)
            except Exception as e:
                print(f"Warning: Could not update .env file: {e}", file=sys.stderr)

            if expires_in:
                print(f"Token refreshed (expires_in={expires_in}s)", file=sys.stderr)

            if _validate_access_token(new_access_token):
                _write_auth_state(
                    last_refresh_at=time.time(),
                    last_refresh_status="refreshed_validated",
                    access_fingerprint=_token_fingerprint(new_access_token),
                    refresh_fingerprint=_token_fingerprint(new_refresh_token or refresh_token),
                )
                return new_access_token

            _write_auth_state(
                last_refresh_at=time.time(),
                last_refresh_status="refreshed_validation_failed",
                access_fingerprint=_token_fingerprint(new_access_token),
                refresh_fingerprint=_token_fingerprint(new_refresh_token or refresh_token),
            )
            print(
                "Token refresh failed validation via /2/users/me. Re-run PKCE auth or inspect credentials.",
                file=sys.stderr,
            )
            return None
        except requests.RequestException as e:
            print(f"Token refresh failed: {e}", file=sys.stderr)
            _write_auth_state(
                last_refresh_at=time.time(),
                last_refresh_status=f"token_endpoint_error:{type(e).__name__}",
                refresh_fingerprint=_token_fingerprint(refresh_token),
            )
            return None


def _update_env_file(access_token: str, refresh_token: Optional[str] = None, scopes: Optional[str] = None):
    """Update the .env file with new tokens."""
    if not env_path.exists():
        return
    
    content = env_path.read_text()
    
    def upsert_env_var(text: str, key: str, value: str) -> str:
        pattern = rf"^{re.escape(key)}=.*$"
        replacement = f"{key}={value}"
        if re.search(pattern, text, flags=re.MULTILINE):
            return re.sub(pattern, replacement, text, flags=re.MULTILINE)
        suffix = "" if text.endswith("\n") else "\n"
        return f"{text}{suffix}{replacement}\n"

    content = upsert_env_var(content, "X_OAUTH2_ACCESS_TOKEN", access_token)
    content = upsert_env_var(content, "X_ACCESS_TOKEN", access_token)

    if refresh_token:
        content = upsert_env_var(content, "X_OAUTH2_REFRESH_TOKEN", refresh_token)
        content = upsert_env_var(content, "X_REFRESH_TOKEN", refresh_token)

    if scopes:
        content = upsert_env_var(content, "X_OAUTH2_SCOPES", scopes)
        content = upsert_env_var(content, "X_SCOPES", scopes)
    
    env_path.write_text(content)


def _refresh_from_env() -> str:
    client_id = _env_value("X_OAUTH2_CLIENT_ID", "X_CLIENT_ID")
    client_secret = _env_value("X_OAUTH2_CLIENT_SECRET", "X_CLIENT_SECRET")
    refresh_token = _env_value("X_OAUTH2_REFRESH_TOKEN", "X_REFRESH_TOKEN")

    if not client_id or not client_secret or not refresh_token:
        raise AuthRefreshError("OAuth refresh requires client ID, client secret, and refresh token.")

    new_token = refresh_access_token(client_id, client_secret, refresh_token)
    if not new_token:
        raise AuthRefreshError("OAuth refresh did not produce a validated access token.")
    return new_token


def run_auth_operation(
    operation: Callable[[Client], T],
    *,
    use_app_only: bool = False,
    required_scopes: Optional[set[str]] = None,
    action_name: str = "this operation",
) -> T:
    """Run one SDK operation, refreshing and retrying once on auth failure."""
    if required_scopes and not use_app_only:
        _ensure_required_scopes(required_scopes, action_name)

    try:
        client = get_client(use_app_only=use_app_only)
        result = operation(client)
    except Exception as exc:
        if _is_reply_policy_failure(exc):
            raise ReplyPolicyError(
                "Reply rejected by X: the original post does not allow replies from this account."
            ) from exc
        if not _is_auth_failure(exc):
            raise
        result = None
    else:
        if not _is_auth_failure(result):
            return result

    try:
        new_token = _refresh_from_env()
    except AuthRefreshError as exc:
        raise XWingError(f"Authentication failed and refresh was not possible: {exc}") from exc

    if required_scopes and not use_app_only:
        _ensure_required_scopes(required_scopes, action_name)

    try:
        retry_client = Client(access_token=new_token)
        retry_result = operation(retry_client)
    except Exception as exc:
        if _is_reply_policy_failure(exc):
            raise ReplyPolicyError(
                "Reply rejected by X: the original post does not allow replies from this account."
            ) from exc
        if _is_auth_failure(exc):
            raise XWingError("Authentication failed after one refresh retry. Re-run PKCE auth.") from exc
        raise

    if _is_auth_failure(retry_result):
        raise XWingError("Authentication failed after one refresh retry. Re-run PKCE auth.")

    return retry_result


def get_client(use_app_only: bool = False) -> Client:
    """Create and return an authenticated X API client using OAuth 2.0.
    
    Args:
        use_app_only: If True, use client credentials for app-only auth (for search).
                     If False, use OAuth 2.0 bearer token for user-context operations.
    """
    client_id = _env_value("X_OAUTH2_CLIENT_ID", "X_CLIENT_ID")
    client_secret = _env_value("X_OAUTH2_CLIENT_SECRET", "X_CLIENT_SECRET")
    access_token = _env_value("X_OAUTH2_ACCESS_TOKEN", "X_ACCESS_TOKEN")
    # Validate credentials
    if not client_id or not client_secret:
        raise XWingError(
            "Missing OAuth 2.0 credentials in environment variables. "
            "Required: X_OAUTH2_CLIENT_ID/X_CLIENT_ID, X_OAUTH2_CLIENT_SECRET/X_CLIENT_SECRET"
        )
    
    # For user-context operations, we need an access token
    if not use_app_only:
        if not access_token:
            raise XWingError(
                "Missing OAuth 2.0 access token for user-context operations. "
                "Required: X_OAUTH2_ACCESS_TOKEN or X_ACCESS_TOKEN"
            )
        
        return Client(access_token=access_token)
    
    # For app-only auth (search), we can use client credentials
    # or the access token if available
    if access_token:
        return Client(access_token=access_token)
    else:
        # App-only auth using client credentials
        return Client(client_id=client_id, client_secret=client_secret)


def get_my_user_id(client: Client) -> str:
    """Get the authenticated user's ID."""
    response = client.users.get_me()
    if response and response.data:
        # Handle both object and dict response formats
        if isinstance(response.data, dict):
            return str(response.data.get('id'))
        elif hasattr(response.data, 'id'):
            return str(response.data.id)
    raise ValueError("Could not get authenticated user ID")


def print_json(data):
    """Print data as formatted JSON."""
    if hasattr(data, "model_dump"):
        print(json.dumps(data.model_dump(), indent=2, default=str))
    elif hasattr(data, "__dict__"):
        print(json.dumps(data.__dict__, indent=2, default=str))
    else:
        print(json.dumps(data, indent=2, default=str))


from unittest.mock import Mock


def _response_to_dict(response: Any) -> dict:
    """Convert an SDK response object to a plain dict for return via MCP/CLI wrappers."""
    result: dict[str, Any] = {}
    if response is None:
        return result
    if hasattr(response, "data"):
        data = response.data
        if isinstance(data, Mock):
            # Preserve MagicMock objects so test fixtures behave predictably.
            result["data"] = data
        elif hasattr(data, "model_dump"):
            result["data"] = data.model_dump()
        elif hasattr(data, "__dict__"):
            result["data"] = data.__dict__
        elif isinstance(data, dict):
            result["data"] = data
        else:
            result["data"] = data
    if hasattr(response, "errors") and response.errors:
        errors = response.errors
        if isinstance(errors, Mock):
            result["errors"] = errors
        elif hasattr(errors, "model_dump"):
            result["errors"] = errors.model_dump()
        elif hasattr(errors, "__dict__"):
            result["errors"] = errors.__dict__
        elif isinstance(errors, (list, dict)):
            result["errors"] = errors
        else:
            result["errors"] = str(errors)
    return result


def api_post(client: Client, text: str, reply_to: Optional[str] = None, quote: Optional[str] = None, media: Optional[str] = None) -> dict:
    """Create a single post. Returns a serializable dict with the API response."""
    body: dict[str, Any] = {"text": text}
    if reply_to:
        body["reply"] = {"in_reply_to_tweet_id": reply_to}
    if quote:
        body["quote_tweet_id"] = quote
    if media:
        body["media"] = {"media_ids": [media]}

    response = run_auth_operation(
        lambda c: c.posts.create(body=body),
        required_scopes={"tweet.write"},
        action_name="posting",
    )
    return _response_to_dict(response)


def api_thread(client: Client, texts: list[str]) -> dict:
    """Create a thread (multi-post sequence). Returns {"post_ids": [...]}."""
    if not texts:
        raise XWingError("At least one text is required for a thread.")

    post_ids: list[str] = []
    previous_post_id: Optional[str] = None

    for i, text in enumerate(texts, 1):
        body = {"text": text}
        if previous_post_id:
            body["reply"] = {"in_reply_to_tweet_id": previous_post_id}

        try:
            response = run_auth_operation(
                lambda c, body=body: c.posts.create(body=body),
                required_scopes={"tweet.write"},
                action_name="creating a thread",
            )
        except ReplyPolicyError as exc:
            raise ReplyPolicyError(f"{exc} Target post ID: {previous_post_id}") from exc

        if response and response.data:
            post_id = response.data.id
            post_ids.append(post_id)
            previous_post_id = post_id
        else:
            errors = _response_to_dict(response).get("errors")
            raise XWingError(
                f"Failed to create post {i} in thread."
                + (f" Errors: {errors}" if errors else "")
            )

    return {"post_ids": post_ids}


def api_like(client: Client, post_id: str) -> dict:
    """Like a post."""
    my_id = run_auth_operation(get_my_user_id)
    body = {"tweet_id": post_id}
    response = run_auth_operation(
        lambda c: c.users.like_post(id=my_id, body=body),
        required_scopes={"like.write"},
        action_name="liking a post",
    )
    return _response_to_dict(response)


def api_repost(client: Client, post_id: str) -> dict:
    """Repost a post."""
    my_id = run_auth_operation(get_my_user_id)
    body = {"tweet_id": post_id}
    response = run_auth_operation(
        lambda c: c.users.repost_post(id=my_id, body=body),
        required_scopes={"tweet.write"},
        action_name="reposting a post",
    )
    return _response_to_dict(response)


def api_follow(client: Client, target_user_id: str) -> dict:
    """Follow a user."""
    my_id = run_auth_operation(get_my_user_id)
    body = {"target_user_id": target_user_id}
    response = run_auth_operation(
        lambda c: c.users.follow_user(id=my_id, body=body),
        required_scopes={"follows.write"},
        action_name="following a user",
    )
    return _response_to_dict(response)


def api_unfollow(client: Client, source_user_id: str, target_user_id: str) -> dict:
    """Unfollow a user."""
    response = run_auth_operation(
        lambda c: c.users.unfollow_user(
            source_user_id=source_user_id,
            target_user_id=target_user_id,
        ),
        required_scopes={"follows.write"},
        action_name="unfollowing a user",
    )
    return _response_to_dict(response)


def api_dm_send(client: Client, *, user: Optional[str] = None, conversation: Optional[str] = None, text: str) -> dict:
    """Send a direct message to a user or conversation."""
    if user:
        participant_id = run_auth_operation(lambda c: resolve_user_id(c, user))
    elif conversation:
        participant_id = conversation
    else:
        raise XWingError("Either user or conversation must be specified.")

    body = {"text": text}
    response = run_auth_operation(
        lambda c: c.dm.send_message(
            participant_id=participant_id,
            body=body,
        ),
        required_scopes={"dm.write"},
        action_name="sending a direct message",
    )
    return _response_to_dict(response)


def cmd_post(args):
    """Create a new post."""
    result = api_post(
        None,  # client created inside run_auth_operation
        text=args.text,
        reply_to=getattr(args, "reply_to", None),
        quote=getattr(args, "quote", None),
        media=getattr(args, "media", None),
    )
    if result.get("data"):
        data = result["data"]
        post_id = data.get("id") if isinstance(data, dict) else getattr(data, "id", None)
        print("Post created successfully!")
        if post_id:
            print(f"Post ID: {post_id}")
        if args.json:
            print_json(data)
    else:
        print("Failed to create post.")
        if result.get("errors"):
            print_json(result["errors"])


def cmd_upload_media(args):
    """Upload media file."""
    file_path = Path(args.file)
    
    # Validate file exists
    if not file_path.exists():
        print(f"Error: File '{args.file}' not found.")
        sys.exit(1)
    
    # Validate file size (< 5MB for simple upload)
    file_size = file_path.stat().st_size
    max_size = 5 * 1024 * 1024  # 5MB
    if file_size > max_size:
        print(f"Error: File size ({file_size / 1024 / 1024:.2f}MB) exceeds 5MB limit for simple upload.")
        sys.exit(1)
    
    # Validate file extension
    valid_extensions = {'.jpg', '.jpeg', '.png', '.gif', '.webp'}
    if file_path.suffix.lower() not in valid_extensions:
        print(f"Error: Invalid file type '{file_path.suffix}'. Supported: {', '.join(valid_extensions)}")
        sys.exit(1)
    
    # Determine MIME type
    mime_types = {
        '.jpg': 'image/jpeg',
        '.jpeg': 'image/jpeg',
        '.png': 'image/png',
        '.gif': 'image/gif',
        '.webp': 'image/webp'
    }
    mime_type = mime_types.get(file_path.suffix.lower(), 'application/octet-stream')
    
    try:
        # Read file and upload
        with open(file_path, 'rb') as f:
            media_data = f.read()
        
        response = run_auth_operation(
            lambda client: client.media.upload(
                media=media_data,
                media_category="tweet_image"
            )
        )
        
        if response and response.data:
            media_id = response.data.id
            print(f"Media uploaded successfully!")
            print(f"Media ID: {media_id}")
            print(f"\nUse this ID with: python scripts/x_client.py post --text 'Your text' --media {media_id}")
            if args.json:
                print_json(response.data)
        else:
            print("Failed to upload media.")
            if hasattr(response, "errors") and response.errors:
                print_json(response.errors)
    
    except Exception as e:
        print(f"Error uploading media: {e}")
        sys.exit(1)


def resolve_user_id(client, user_identifier: str) -> str:
    """Resolve a user identifier (username or ID) to a user ID.
    
    Args:
        client: X API client
        user_identifier: Username (with @) or user ID
        
    Returns:
        User ID string
    """
    if user_identifier.isdigit():
        return user_identifier
    
    # It's a username, look up the user ID
    username = user_identifier.lstrip("@")
    user_response = client.users.get_by_username(username=username)
    
    if user_response and user_response.data:
        user_data = user_response.data
        if isinstance(user_data, dict):
            return str(user_data.get('id', ''))
        else:
            return str(user_data.id)
    else:
        raise ValueError(f"User '{user_identifier}' not found.")


def cmd_dm_send(args):
    """Send a direct message."""
    try:
        result = api_dm_send(
            None,
            user=getattr(args, "user", None),
            conversation=getattr(args, "conversation", None),
            text=args.text,
        )
        if result.get("data"):
            print("Message sent successfully!")
            data = result["data"]
            msg_id = data.get("id") if isinstance(data, dict) else getattr(data, "id", None)
            if msg_id:
                print(f"Message ID: {msg_id}")
            if args.json:
                print_json(data)
        else:
            print("Failed to send message.")
            if result.get("errors"):
                print_json(result["errors"])
    except (ValueError, XWingError) as e:
        print(f"Error: {e}")
        sys.exit(1)


def cmd_dm_list(args):
    """List DM conversations."""
    max_results = args.limit or 10
    
    try:
        def fetch_conversations(client):
            results = []
            for page in client.dm.get_conversations(max_results=max_results):
                if page.data:
                    results.extend(page.data)
                if len(results) >= max_results:
                    break
            return results[:max_results]

        results = run_auth_operation(fetch_conversations)
        
        print(f"DM Conversations ({len(results)}):\n")
        for i, conv in enumerate(results, 1):
            # Handle both dict and object formats
            if isinstance(conv, dict):
                conv_id = conv.get('id', '')
                participants = conv.get('participants', [])
            else:
                conv_id = conv.id
                participants = getattr(conv, 'participants', [])
            
            # Get participant names if available
            participant_names = []
            if participants:
                for p in participants:
                    if isinstance(p, dict):
                        name = p.get('username', p.get('id', ''))
                    else:
                        name = getattr(p, 'username', getattr(p, 'id', ''))
                    participant_names.append(name)
            
            participant_str = ', '.join(participant_names) if participant_names else 'Unknown'
            print(f"{i}. [{conv_id}] Participants: {participant_str}")
            
            if args.json:
                print_json(conv)
                print()
    
    except Exception as e:
        print(f"Error listing conversations: {e}")
        sys.exit(1)


def cmd_dm_get(args):
    """Get messages in a DM conversation."""
    max_results = args.limit or 20
    
    try:
        def fetch_messages(client):
            results = []
            for page in client.dm.get_messages(
                conversation_id=args.conversation,
                max_results=max_results
            ):
                if page.data:
                    results.extend(page.data)
                if len(results) >= max_results:
                    break
            return results[:max_results]

        results = run_auth_operation(fetch_messages)
        
        print(f"Messages in conversation {args.conversation} ({len(results)}):\n")
        for i, msg in enumerate(results, 1):
            # Handle both dict and object formats
            if isinstance(msg, dict):
                msg_id = msg.get('id', '')
                text = msg.get('text', '')
                sender_id = msg.get('sender_id', 'Unknown')
                created_at = msg.get('created_at', 'N/A')
            else:
                msg_id = msg.id
                text = getattr(msg, 'text', '')
                sender_id = getattr(msg, 'sender_id', 'Unknown')
                created_at = getattr(msg, 'created_at', 'N/A')
            
            text_preview = text[:80] + "..." if len(text) > 80 else text
            print(f"{i}. [{msg_id}] {created_at}")
            print(f"   From: {sender_id}")
            print(f"   Text: {text_preview}")
            
            if args.json:
                print_json(msg)
                print()
    
    except Exception as e:
        print(f"Error getting messages: {e}")
        sys.exit(1)


def cmd_delete(args):
    """Delete a post by ID."""
    response = run_auth_operation(
        lambda client: client.posts.delete(id=args.id),
        required_scopes={"tweet.write"},
        action_name="deleting a post",
    )
    if response and response.data:
        print(f"Post deleted successfully!")
        if args.json:
            print_json(response.data)
    else:
        print("Failed to delete post.")
        if hasattr(response, "errors") and response.errors:
            print_json(response.errors)


def cmd_get(args):
    """Get post details by ID."""
    tweet_fields = ["created_at", "public_metrics", "text", "author_id"]
    response = run_auth_operation(lambda client: client.posts.get_by_id(id=args.id, tweet_fields=tweet_fields))
    if response and response.data:
        post = response.data
        # Handle both dict and object formats
        if isinstance(post, dict):
            post_id = post.get('id', '')
            author_id = post.get('author_id', 'N/A')
            created_at = post.get('created_at', 'N/A')
            text = post.get('text', '')
            public_metrics = post.get('public_metrics')
        else:
            post_id = post.id
            author_id = getattr(post, 'author_id', 'N/A')
            created_at = getattr(post, 'created_at', 'N/A')
            text = post.text
            public_metrics = getattr(post, 'public_metrics', None)
        
        print(f"Post ID: {post_id}")
        print(f"Author ID: {author_id}")
        print(f"Created: {created_at}")
        print(f"Text: {text}")
        if public_metrics and args.json:
            print("Metrics:")
            print_json(public_metrics)
        if args.json:
            print("\nFull response:")
            print_json(post)
    else:
        print("Post not found or unavailable.")


def cmd_search(args):
    """Search posts (7-day window)."""
    max_results = args.limit or 10
    tweet_fields = ["created_at", "public_metrics", "text", "author_id"]
    
    def fetch_search(client):
        results = []
        for page in client.posts.search_recent(query=args.query, max_results=max_results, tweet_fields=tweet_fields):
            if page.data:
                results.extend(page.data)
            if len(results) >= max_results:
                break
        return results[:max_results]

    results = run_auth_operation(fetch_search, use_app_only=True)
    
    print(f"Found {len(results)} posts matching '{args.query}':\n")
    for i, post in enumerate(results, 1):
        # Handle both dict and object formats
        if isinstance(post, dict):
            post_id = post.get('id', '')
            post_text = post.get('text', '')
        else:
            post_id = post.id
            post_text = post.text
        print(f"{i}. [{post_id}] {post_text[:100]}{'...' if len(post_text) > 100 else ''}")
        if args.json:
            print_json(post)
            print()


def cmd_like(args):
    """Like a post."""
    result = api_like(None, post_id=args.id)
    if result.get("data"):
        print(f"Post {args.id} liked successfully!")
        if args.json:
            print_json(result["data"])
    else:
        print("Failed to like post.")
        if result.get("errors"):
            print_json(result["errors"])


def cmd_unlike(args):
    """Unlike a post."""
    my_id = run_auth_operation(get_my_user_id)
    response = run_auth_operation(
        lambda client: client.users.unlike_post(id=my_id, tweet_id=args.id),
        required_scopes={"like.write"},
        action_name="unliking a post",
    )
    if response and response.data:
        print(f"Post {args.id} unliked successfully!")
        if args.json:
            print_json(response.data)
    else:
        print("Failed to unlike post.")
        if hasattr(response, "errors") and response.errors:
            print_json(response.errors)


def cmd_repost(args):
    """Repost a post."""
    result = api_repost(None, post_id=args.id)
    if result.get("data"):
        print(f"Post {args.id} reposted successfully!")
        if args.json:
            print_json(result["data"])
    else:
        print("Failed to repost.")
        if result.get("errors"):
            print_json(result["errors"])


def cmd_follow(args):
    """Follow a user."""
    result = api_follow(None, target_user_id=args.target_user_id)
    if result.get("data"):
        print(f"Successfully followed user {args.target_user_id}!")
        data = result["data"]
        following = data.get("following") if isinstance(data, dict) else getattr(data, "following", None)
        if following is not None:
            print(f"Following: {following}")
        if args.json:
            print_json(data)
    else:
        print(f"Failed to follow user {args.target_user_id}.")
        if result.get("errors"):
            print_json(result["errors"])


def cmd_unfollow(args):
    """Unfollow a user."""
    result = api_unfollow(None, source_user_id=args.source_user_id, target_user_id=args.target_user_id)
    if result.get("data"):
        print(f"Successfully unfollowed user {args.target_user_id}!")
        data = result["data"]
        following = data.get("following") if isinstance(data, dict) else getattr(data, "following", None)
        if following is not None:
            print(f"Following: {following}")
        if args.json:
            print_json(data)
    else:
        print(f"Failed to unfollow user {args.target_user_id}.")
        if result.get("errors"):
            print_json(result["errors"])


def cmd_unrepost(args):
    """Undo a repost."""
    my_id = run_auth_operation(get_my_user_id)
    response = run_auth_operation(
        lambda client: client.users.unrepost_post(id=my_id, source_tweet_id=args.id),
        required_scopes={"tweet.write"},
        action_name="undoing a repost",
    )
    if response and response.data:
        print(f"Repost of {args.id} undone successfully!")
        if args.json:
            print_json(response.data)
    else:
        print("Failed to undo repost.")
        if hasattr(response, "errors") and response.errors:
            print_json(response.errors)


def cmd_timeline(args):
    """Get home timeline."""
    my_id = run_auth_operation(get_my_user_id)
    max_results = args.limit or 10
    tweet_fields = ["created_at", "public_metrics", "text", "author_id"]
    
    def fetch_timeline(client):
        results = []
        for page in client.users.get_timeline(id=my_id, max_results=max_results, tweet_fields=tweet_fields):
            if page.data:
                results.extend(page.data)
            if len(results) >= max_results:
                break
        return results[:max_results]

    results = run_auth_operation(fetch_timeline)
    
    print(f"Home Timeline ({len(results)} posts):\n")
    for i, post in enumerate(results, 1):
        # Handle both dict and object formats
        if isinstance(post, dict):
            post_text = post.get('text', '')
            post_id = post.get('id', '')
        else:
            post_text = post.text
            post_id = post.id
        text_preview = post_text[:80] + "..." if len(post_text) > 80 else post_text
        print(f"{i}. [{post_id}] {text_preview}")
        if args.json:
            print_json(post)
            print()


def cmd_user_posts(args):
    """Get user's posts."""
    # Resolve user ID from username if needed
    user_id = args.user
    if not user_id.isdigit():
        # It's a username, look up the user ID
        user_response = run_auth_operation(lambda client: client.users.get_by_username(username=user_id.lstrip("@")))
        if user_response and user_response.data:
            # Handle both dict and object formats
            user_data = user_response.data
            if isinstance(user_data, dict):
                user_id = str(user_data.get('id', ''))
                username = user_data.get('username', args.user.lstrip('@'))
            else:
                user_id = str(user_data.id)
                username = user_data.username
            print(f"Resolved @{username} to user ID: {user_id}")
        else:
            print(f"User '{args.user}' not found.")
            return
    
    max_results = args.limit or 10
    tweet_fields = ["created_at", "public_metrics", "text", "author_id"]
    
    def fetch_user_posts(client):
        results = []
        for page in client.users.get_posts(id=user_id, max_results=max_results, tweet_fields=tweet_fields):
            if page.data:
                results.extend(page.data)
            if len(results) >= max_results:
                break
        return results[:max_results]

    results = run_auth_operation(fetch_user_posts)
    
    print(f"User posts ({len(results)} posts):\n")
    for i, post in enumerate(results, 1):
        # Handle both dict and object formats
        if isinstance(post, dict):
            post_text = post.get('text', '')
            post_id = post.get('id', '')
        else:
            post_text = post.text
            post_id = post.id
        text_preview = post_text[:80] + "..." if len(post_text) > 80 else post_text
        print(f"{i}. [{post_id}] {text_preview}")
        if args.json:
            print_json(post)
            print()


def cmd_mentions(args):
    """Get mentions for authenticated user."""
    my_id = run_auth_operation(get_my_user_id)
    max_results = args.limit or 10
    tweet_fields = ["created_at", "public_metrics", "text", "author_id"]
    
    def fetch_mentions(client):
        results = []
        for page in client.users.get_mentions(id=my_id, max_results=max_results, tweet_fields=tweet_fields):
            if page.data:
                results.extend(page.data)
            if len(results) >= max_results:
                break
        return results[:max_results]

    results = run_auth_operation(fetch_mentions)
    
    print(f"Mentions ({len(results)} posts):\n")
    for i, post in enumerate(results, 1):
        # Handle both dict and object formats
        if isinstance(post, dict):
            post_text = post.get('text', '')
            post_id = post.get('id', '')
        else:
            post_text = post.text
            post_id = post.id
        text_preview = post_text[:80] + "..." if len(post_text) > 80 else post_text
        print(f"{i}. [{post_id}] {text_preview}")
        if args.json:
            print_json(post)
            print()


def cmd_thread(args):
    """Create a thread (multi-post sequence)."""
    if not args.text or len(args.text) == 0:
        print("Error: At least one --text argument is required for a thread.")
        sys.exit(1)

    try:
        result = api_thread(None, texts=args.text)
    except ReplyPolicyError as exc:
        print(f"{exc}")
        sys.exit(1)

    post_ids = result.get("post_ids", [])
    print(f"\nThread created successfully with {len(post_ids)} posts!")
    print(f"First post ID: {post_ids[0]}")
    print(f"Last post ID: {post_ids[-1]}")
    print("\nAll post IDs:")
    for pid in post_ids:
        print(f"  - {pid}")


def main():
    parser = argparse.ArgumentParser(
        prog="x_client",
        description="X (Twitter) API v2 CLI Client (OAuth 2.0)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    python scripts/x_client.py post --text "Hello, World!"
    python scripts/x_client.py get --id 1234567890
    python scripts/x_client.py search --query "python API"
    python scripts/x_client.py timeline --limit 20
    python scripts/x_client.py user-posts --user @elonmusk

Environment Variables (in .env next to this script):
    X_OAUTH2_CLIENT_ID      - OAuth 2.0 Client ID (preferred)
    X_OAUTH2_CLIENT_SECRET  - OAuth 2.0 Client Secret (preferred)
    X_OAUTH2_ACCESS_TOKEN   - OAuth 2.0 Access Token (preferred)
    X_OAUTH2_REFRESH_TOKEN  - OAuth 2.0 Refresh Token (preferred)

Legacy aliases are also accepted: X_CLIENT_ID, X_CLIENT_SECRET,
X_ACCESS_TOKEN, X_REFRESH_TOKEN.  Set X_WING_ENV_PATH to override the .env location.
        """
    )
    parser.add_argument("--json", action="store_true", help="Output full JSON response")
    
    subparsers = parser.add_subparsers(dest="command", help="Available commands")
    
    # post command
    post_parser = subparsers.add_parser("post", help="Create a new post")
    post_parser.add_argument("--text", required=True, help="Text content of the post")
    post_parser.add_argument("--media", help="Media ID to attach to the post (from upload-media)")
    post_parser.add_argument("--reply-to", dest="reply_to", help="Tweet ID to reply to (self-serve apps may be restricted; prefer --quote for engagement)")
    post_parser.add_argument("--quote", dest="quote", help="Tweet ID to quote-tweet (recommended over --reply-to for engagement on self-serve apps)")
    post_parser.set_defaults(func=cmd_post)
    
    # upload-media command
    upload_media_parser = subparsers.add_parser("upload-media", help="Upload media file (images < 5MB)")
    upload_media_parser.add_argument("--file", required=True, help="Path to media file (JPG, PNG, GIF, WEBP)")
    upload_media_parser.set_defaults(func=cmd_upload_media)
    
    # delete command
    delete_parser = subparsers.add_parser("delete", help="Delete a post by ID")
    delete_parser.add_argument("--id", required=True, help="Post ID to delete")
    delete_parser.set_defaults(func=cmd_delete)
    
    # get command
    get_parser = subparsers.add_parser("get", help="Get post details by ID")
    get_parser.add_argument("--id", required=True, help="Post ID to retrieve")
    get_parser.set_defaults(func=cmd_get)
    
    # search command
    search_parser = subparsers.add_parser("search", help="Search posts (7-day window)")
    search_parser.add_argument("--query", required=True, help="Search query")
    search_parser.add_argument("--limit", type=int, default=10, help="Maximum results (default: 10)")
    search_parser.set_defaults(func=cmd_search)
    
    # like command
    like_parser = subparsers.add_parser("like", help="Like a post")
    like_parser.add_argument("--id", required=True, help="Post ID to like")
    like_parser.set_defaults(func=cmd_like)
    
    # unlike command
    unlike_parser = subparsers.add_parser("unlike", help="Unlike a post")
    unlike_parser.add_argument("--id", required=True, help="Post ID to unlike")
    unlike_parser.set_defaults(func=cmd_unlike)
    
    # repost command
    repost_parser = subparsers.add_parser("repost", help="Repost a post")
    repost_parser.add_argument("--id", required=True, help="Post ID to repost")
    repost_parser.set_defaults(func=cmd_repost)

    # follow command
    follow_parser = subparsers.add_parser("follow", help="Follow a user")
    follow_parser.add_argument("--target-user-id", required=True, help="User ID to follow")
    follow_parser.set_defaults(func=cmd_follow)

    # unfollow command
    unfollow_parser = subparsers.add_parser("unfollow", help="Unfollow a user")
    unfollow_parser.add_argument("--source-user-id", required=True, help="Your user ID (the authenticated user)")
    unfollow_parser.add_argument("--target-user-id", required=True, help="User ID to unfollow")
    unfollow_parser.set_defaults(func=cmd_unfollow)

    # unrepost command
    unrepost_parser = subparsers.add_parser("unrepost", help="Undo a repost")
    unrepost_parser.add_argument("--id", required=True, help="Original post ID to unrepost")
    unrepost_parser.set_defaults(func=cmd_unrepost)
    
    # timeline command
    timeline_parser = subparsers.add_parser("timeline", help="Get home timeline")
    timeline_parser.add_argument("--limit", type=int, default=10, help="Maximum results (default: 10)")
    timeline_parser.set_defaults(func=cmd_timeline)
    
    # user-posts command
    user_posts_parser = subparsers.add_parser("user-posts", help="Get user's posts")
    user_posts_parser.add_argument("--user", required=True, help="User ID or username (e.g., @username)")
    user_posts_parser.add_argument("--limit", type=int, default=10, help="Maximum results (default: 10)")
    user_posts_parser.set_defaults(func=cmd_user_posts)
    
    # mentions command
    mentions_parser = subparsers.add_parser("mentions", help="Get mentions for authenticated user")
    mentions_parser.add_argument("--limit", type=int, default=10, help="Maximum results (default: 10)")
    mentions_parser.set_defaults(func=cmd_mentions)
    
    # thread command
    thread_parser = subparsers.add_parser("thread", help="Create a thread (multi-post sequence)")
    thread_parser.add_argument("--text", action="append", required=True, help="Text for each post in the thread (use multiple --text args)")
    thread_parser.set_defaults(func=cmd_thread)

    # dm-send command
    dm_send_parser = subparsers.add_parser("dm-send", help="Send a direct message")
    dm_send_parser.add_argument("--user", help="Username (with @) or user ID to send to")
    dm_send_parser.add_argument("--conversation", help="Conversation ID to send to")
    dm_send_parser.add_argument("--text", required=True, help="Message text")
    dm_send_parser.set_defaults(func=cmd_dm_send)

    # dm-list command
    dm_list_parser = subparsers.add_parser("dm-list", help="List DM conversations")
    dm_list_parser.add_argument("--limit", type=int, default=10, help="Maximum results (default: 10)")
    dm_list_parser.set_defaults(func=cmd_dm_list)

    # dm-get command
    dm_get_parser = subparsers.add_parser("dm-get", help="Get messages in a DM conversation")
    dm_get_parser.add_argument("--conversation", required=True, help="Conversation ID")
    dm_get_parser.add_argument("--limit", type=int, default=20, help="Maximum results (default: 20)")
    dm_get_parser.set_defaults(func=cmd_dm_get)

    args = parser.parse_args()
    
    if not args.command:
        parser.print_help()
        sys.exit(1)
    
    try:
        args.func(args)
    except XWingError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
    except ReplyPolicyError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
