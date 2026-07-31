"""Read-only official X API provider using the Python XDK directly."""

from __future__ import annotations

import os
from pathlib import Path
from collections.abc import Callable
from typing import Any

from xdata.contracts import CostEstimate, Metrics, Post, ProviderResult, UserProfile, UserRef
from xdata.providers.syndication import extract_post_id, normalize_handle

PROVIDER_NAME = "official_x"
POST_READ_COST_USD = 0.005
OWNED_READ_COST_USD = 0.001
USER_READ_COST_USD = 0.010

# X API v2 enforces per-endpoint max_results bounds. The number of items we
# return to the caller is still capped by the requested ``limit``; these floors
# only raise the page size we request from the API so small limits do not get
# rejected for being below the endpoint minimum.
SEARCH_MIN_RESULTS = 10
USER_POSTS_MIN_RESULTS = 5
TIMELINE_MIN_RESULTS = 5
MENTIONS_MIN_RESULTS = 5
POSTS_MAX_RESULTS = 100
USER_GRAPH_MAX_RESULTS = 1000


ClientFactory = Callable[[str], Any]


def _strip_quotes(value: str | None) -> str | None:
    if value and len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        return value[1:-1]
    return value


def _dotenv_path() -> Path:
    """Return the .env file used by the merged x-wing MCP server.

    Mirrors the resolution in x_client.py: X_WING_ENV_PATH override,
    otherwise the repo-root .env next to this package.
    """
    env_override = os.getenv("X_WING_ENV_PATH")
    if env_override:
        return Path(env_override)
    return Path(__file__).resolve().parent.parent.parent / ".env"


def _env_value(primary: str, fallback: str | None = None) -> str | None:
    value = _strip_quotes(os.getenv(primary))
    if value:
        return value
    if fallback:
        fallback_val = _strip_quotes(os.getenv(fallback))
        if fallback_val:
            return fallback_val

    # Fallback: try to load from .env file directly
    env_path = _dotenv_path()
    if env_path.exists():
        content = env_path.read_text()
        for line in content.splitlines():
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                key, val = line.split('=', 1)
                if key == primary:
                    return val.strip('"\'').strip()
                if key == fallback:
                    return val.strip('"\'').strip()

    return None


def _load_xdk_client_factory() -> ClientFactory | None:
    try:
        from xdk import Client  # type: ignore
    except Exception:
        return None
    return lambda access_token: Client(access_token=access_token)


def _data_items(response: Any) -> list[Any]:
    data = getattr(response, "data", None)
    if data is None and isinstance(response, dict):
        data = response.get("data")
    if data is None:
        return []
    if isinstance(data, list):
        return data
    return [data]


def _value(obj: Any, key: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _metrics_from_obj(obj: Any) -> Metrics | None:
    public_metrics = _value(obj, "public_metrics")
    if not isinstance(public_metrics, dict):
        public_metrics = {}
    metrics = Metrics(
        replies=_maybe_int(public_metrics.get("reply_count")),
        reposts=_maybe_int(public_metrics.get("retweet_count")),
        likes=_maybe_int(public_metrics.get("like_count")),
        quotes=_maybe_int(public_metrics.get("quote_count")),
        views=_maybe_int(public_metrics.get("impression_count")),
    )
    if all(value is None for value in metrics.__dict__.values()):
        return None
    return metrics


def _user_public_metrics_from_obj(obj: Any) -> dict[str, int] | None:
    public_metrics = _value(obj, "public_metrics")
    if not isinstance(public_metrics, dict):
        return None
    metrics = {
        key: int(value)
        for key, value in public_metrics.items()
        if isinstance(value, int) or (isinstance(value, str) and value.isdigit())
    }
    if not metrics:
        return None
    return metrics


def _maybe_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _post_from_obj(obj: Any) -> Post | None:
    post_id = str(_value(obj, "id", "") or "").strip()
    text = str(_value(obj, "text", "") or "").strip()
    if not post_id or not text:
        return None
    author_id = _value(obj, "author_id")
    return Post(
        id=post_id,
        text=text,
        author=UserRef(id=str(author_id)) if author_id else None,
        created_at=_value(obj, "created_at"),
        metrics=_metrics_from_obj(obj),
        source_url=f"https://x.com/i/web/status/{post_id}",
        raw=obj if isinstance(obj, dict) else getattr(obj, "__dict__", {}),
    )


def _user_from_obj(obj: Any) -> UserProfile | None:
    user_id = str(_value(obj, "id", "") or "").strip()
    username = str(_value(obj, "username", "") or "").strip() or None
    if not user_id:
        return None
    return UserProfile(
        id=user_id,
        username=username,
        name=_value(obj, "name"),
        description=_value(obj, "description"),
        public_metrics=_user_public_metrics_from_obj(obj),
        source_url=f"https://x.com/{username}" if username else None,
        raw=obj if isinstance(obj, dict) else getattr(obj, "__dict__", {}),
    )


def _with_extra_context(
    result: ProviderResult,
    *,
    warning: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> ProviderResult:
    return ProviderResult(
        status=result.status,
        provider=result.provider,
        items=result.items,
        reason=result.reason,
        warnings=[*result.warnings, *([warning] if warning else [])],
        cost=result.cost,
        raw_ref=result.raw_ref,
        metadata={**result.metadata, **(metadata or {})},
    )


def _official_x_estimate(task: str, **kwargs: Any) -> tuple[float | None, str | None]:
    if task == "fetch_urls":
        values = kwargs.get("values") or []
        return len(values) * POST_READ_COST_USD, "$0.005/post read (upper bound by requested URLs)"
    if task in {"read_user_posts_recent", "search_posts", "read_thread", "read_replies", "read_quotes"}:
        limit = _estimate_limit(kwargs.get("limit", 20))
        return limit * POST_READ_COST_USD, "$0.005/post read (upper bound by requested limit)"
    if task in {"read_owned_timeline", "read_mentions"}:
        limit = _estimate_limit(kwargs.get("limit", 20))
        return limit * OWNED_READ_COST_USD, "$0.001/owned-account post read (upper bound by requested limit)"
    if task == "read_follow_graph":
        limit = _estimate_limit(kwargs.get("limit", 100))
        return limit * USER_READ_COST_USD, "$0.010/user read (upper bound by requested limit)"
    return None, None


def _estimate_limit(value: Any) -> int:
    try:
        return max(1, int(value))
    except (TypeError, ValueError):
        return 1


class OfficialXProvider:
    """Read-only official X provider.

    This provider intentionally does not refresh or write tokens in v1. Token
    refresh can be added later as a contained credential component.
    """

    name = PROVIDER_NAME

    def __init__(self, *, client_factory: ClientFactory | None = None) -> None:
        self._client_factory = client_factory

    def _access_token(self) -> str | None:
        return _env_value("X_OAUTH2_ACCESS_TOKEN", "X_ACCESS_TOKEN")

    def _client(self, token: str | None = None) -> tuple[Any | None, ProviderResult | None]:
        access_token = token or self._access_token()
        if not access_token:
            return None, ProviderResult.unavailable(
                provider=self.name,
                reason="auth_required",
                warnings=["missing X_OAUTH2_ACCESS_TOKEN/X_ACCESS_TOKEN"],
            )
        factory = self._client_factory or _load_xdk_client_factory()
        if factory is None:
            return None, ProviderResult.unavailable(
                provider=self.name,
                reason="sdk_missing",
                warnings=["install xdk or the official-x optional dependency"],
            )
        try:
            return factory(access_token), None
        except Exception as exc:
            return None, ProviderResult.error(
                provider=self.name,
                reason="client_init_failed",
                warnings=[str(exc)],
            )

    def _refresh_access_token(self) -> bool:
        """
        Refresh the access token using the stored refresh token.
        Returns True on success, False on failure.
        """
        import base64
        import requests
        from pathlib import Path

        client_id = _env_value("X_OAUTH2_CLIENT_ID")
        client_secret = _env_value("X_OAUTH2_CLIENT_SECRET")
        refresh_token = _env_value("X_OAUTH2_REFRESH_TOKEN")

        if not (client_id and client_secret and refresh_token):
            return False

        creds = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
        try:
            r = requests.post(
                "https://api.x.com/2/oauth2/token",
                headers={
                    "Authorization": f"Basic {creds}",
                    "Content-Type": "application/x-www-form-urlencoded",
                },
                data={"grant_type": "refresh_token", "refresh_token": refresh_token},
                timeout=15,
            )
        except Exception:
            return False

        if r.status_code != 200:
            return False

        data = r.json()
        new_access = data.get("access_token")
        new_refresh = data.get("refresh_token")

        if not new_access:
            return False

        # Persist new tokens to .env
        env_path = _dotenv_path()
        if env_path.exists():
            content = env_path.read_text()
            lines = content.splitlines()
            for i, line in enumerate(lines):
                if line.startswith("X_OAUTH2_ACCESS_TOKEN="):
                    lines[i] = f'X_OAUTH2_ACCESS_TOKEN="{new_access}"'
                    break
            else:
                lines.append(f'X_OAUTH2_ACCESS_TOKEN="{new_access}"')

            for i, line in enumerate(lines):
                if line.startswith("X_ACCESS_TOKEN="):
                    lines[i] = f'X_ACCESS_TOKEN="{new_access}"'
                    break
            else:
                lines.append(f'X_ACCESS_TOKEN="{new_access}"')

            if new_refresh:
                for i, line in enumerate(lines):
                    if line.startswith("X_OAUTH2_REFRESH_TOKEN="):
                        lines[i] = f'X_OAUTH2_REFRESH_TOKEN="{new_refresh}"'
                        break
                else:
                    lines.append(f'X_OAUTH2_REFRESH_TOKEN="{new_refresh}"')

                for i, line in enumerate(lines):
                    if line.startswith("X_REFRESH_TOKEN="):
                        lines[i] = f'X_REFRESH_TOKEN="{new_refresh}"'
                        break
                else:
                    lines.append(f'X_REFRESH_TOKEN="{new_refresh}"')

            env_path.write_text("\n".join(lines) + "\n")

        return True

    def _ensure_authenticated(self) -> tuple[Any | None, ProviderResult | None]:
        """
        Get authenticated client, refreshing token if needed.
        Returns (client, error_result) where error_result is None on success.
        """
        # First try to get client normally
        client, unavailable = self._client()
        if unavailable:
            return client, unavailable

        assert client is not None

        # Check if the token is still valid by making a lightweight call
        try:
            self._get_me_id(client)
            # Token is still valid
            return client, None
        except Exception as exc:
            # Token might be expired - try to refresh
            refresh_success = self._refresh_access_token()
            if not refresh_success:
                # Refresh failed, return the error from the validation attempt
                return None, ProviderResult.error(
                    provider=self.name,
                    reason="auth_token_refresh_failed",
                    warnings=["Failed to refresh X authentication token", str(exc)],
                )

            # Refresh succeeded - get fresh client with new token
            client, error = self._client()
            if error:
                return client, error

            assert client is not None

            # Verify the new token works
            try:
                self._get_me_id(client)
                return client, None
            except Exception as retry_exc:
                # New token also invalid
                return None, ProviderResult.error(
                    provider=self.name,
                    reason="auth_token_validation_failed",
                    warnings=["Refreshed token is still invalid", str(retry_exc)],
                )

        return client, None

    def _retry_with_refresh(self, api_call: Callable[[], Any]) -> Any:
        """
        Execute an API call with automatic token refresh on 401 errors.
        Returns the API result.
        """
        max_retries = 2
        for attempt in range(max_retries):
            try:
                # Ensure we have a valid token
                client, unavailable = self._ensure_authenticated()
                if unavailable:
                    raise Exception(f"X API unavailable: {unavailable.reason}")

                # Execute the API call
                return api_call()

            except Exception as exc:
                # Check if this is a 401 auth error
                error_str = str(exc)
                if "401" not in error_str and "unauthorized" not in error_str.lower():
                    # Not an auth error, re-raise
                    raise

                # Check if we should retry (first attempt failed, second attempt will work after refresh)
                if attempt == 0:
                    # This is the first failure - try to refresh token
                    refresh_success = self._refresh_access_token()
                    if not refresh_success:
                        # Refresh failed, re-raise the original error
                        raise Exception(f"Token refresh failed: {exc}")

                    # Continue to next iteration (will retry with new token)
                    continue
                else:
                    # This is the second failure after refresh - give up
                    raise Exception(f"API call failed after token refresh: {exc}")

        # Should never reach here
        raise Exception("Unexpected error in _retry_with_refresh")

    def status(self) -> dict[str, Any]:
        return {
            "auth_required": True,
            "auth_present": bool(self._access_token()),
            "sdk_available": self._client_factory is not None or _load_xdk_client_factory() is not None,
            "token_refresh": "automatic",
            "read_only": True,
            "supports_tasks": [
                "fetch_urls",
                "read_user_posts_recent",
                "search_posts",
                "read_owned_timeline",
                "read_mentions",
                "read_thread",
                "read_replies",
                "read_quotes",
                "read_follow_graph",
            ],
            "limitations": [
                "paid_usage",
                "thread_and_replies_are_recent_search_fallbacks",
                "not_default_for_bulk_collection",
            ],
        }

    def estimate_cost(self, task: str, **kwargs: Any) -> CostEstimate | None:
        units, basis = _official_x_estimate(task, **kwargs)
        if units is None or basis is None:
            return None
        return CostEstimate(amount_usd=round(units, 6), basis=basis)

    def fetch_urls(self, values: list[str]) -> ProviderResult:
        client, unavailable = self._ensure_authenticated()
        if unavailable:
            return unavailable
        assert client is not None

        posts: list[Post] = []
        warnings: list[str] = []
        tweet_fields = ["created_at", "public_metrics", "text", "author_id"]

        def fetch_single_post(value: str) -> list[Post] | None:
            post_id = extract_post_id(value)
            if not post_id:
                warnings.append(f"invalid_post_reference:{value}")
                return None
            try:
                response = client.posts.get_by_id(id=post_id, tweet_fields=tweet_fields)
                items = [_post_from_obj(item) for item in _data_items(response)]
                return [item for item in items if item is not None]
            except Exception as exc:
                return None

        # Use retry logic for each post fetch
        for value in values:
            result = self._retry_with_refresh(lambda: fetch_single_post(value))
            if result is not None:
                posts.extend(result)

        if posts:
            return ProviderResult.ok(
                provider=self.name,
                items=posts,
                warnings=warnings,
                cost=CostEstimate(
                    amount_usd=len(posts) * POST_READ_COST_USD,
                    basis="$0.005/post read",
                ),
            )
        if warnings:
            return ProviderResult.empty(provider=self.name, reason="no_fetchable_posts", warnings=warnings)
        return ProviderResult.empty(provider=self.name)

    def read_user_posts(self, user: str, *, limit: int = 20) -> ProviderResult:
        client, unavailable = self._ensure_authenticated()
        if unavailable:
            return unavailable
        assert client is not None

        handle_or_id = normalize_handle(user)
        if not handle_or_id:
            return ProviderResult.error(provider=self.name, reason="missing_user")

        def resolve_and_fetch() -> Any:
            user_id = handle_or_id if handle_or_id.isdigit() else self._resolve_user_id(client, handle_or_id)
            return self._collect_pages(
                client.users.get_posts,
                {"id": user_id},
                limit=limit,
                min_results=USER_POSTS_MIN_RESULTS,
            )

        try:
            posts = self._retry_with_refresh(resolve_and_fetch)
        except Exception as exc:
            return ProviderResult.error(provider=self.name, reason="api_error", warnings=[str(exc)])

        if posts:
            return ProviderResult.ok(
                provider=self.name,
                items=posts,
                cost=CostEstimate(
                    amount_usd=len(posts) * POST_READ_COST_USD,
                    basis="$0.005/post read",
                ),
            )
        return ProviderResult.empty(provider=self.name)

    def read_owned_timeline(self, *, limit: int = 20) -> ProviderResult:
        client, unavailable = self._ensure_authenticated()
        if unavailable:
            return unavailable
        assert client is not None

        def fetch_timeline() -> Any:
            user_id = self._get_me_id(client)
            return self._collect_pages(
                client.users.get_timeline,
                {"id": user_id},
                limit=limit,
                min_results=TIMELINE_MIN_RESULTS,
            )

        try:
            posts = self._retry_with_refresh(fetch_timeline)
        except Exception as exc:
            return ProviderResult.error(provider=self.name, reason="api_error", warnings=[str(exc)])

        if posts:
            return ProviderResult.ok(
                provider=self.name,
                items=posts,
                cost=CostEstimate(
                    amount_usd=len(posts) * OWNED_READ_COST_USD,
                    basis="$0.001/owned read",
                ),
            )
        return ProviderResult.empty(provider=self.name)

    def read_mentions(self, *, limit: int = 20) -> ProviderResult:
        client, unavailable = self._ensure_authenticated()
        if unavailable:
            return unavailable
        assert client is not None

        def fetch_mentions() -> Any:
            user_id = self._get_me_id(client)
            return self._collect_pages(
                client.users.get_mentions,
                {"id": user_id},
                limit=limit,
                min_results=MENTIONS_MIN_RESULTS,
            )

        try:
            posts = self._retry_with_refresh(fetch_mentions)
        except Exception as exc:
            return ProviderResult.error(provider=self.name, reason="api_error", warnings=[str(exc)])

        if posts:
            return ProviderResult.ok(
                provider=self.name,
                items=posts,
                cost=CostEstimate(
                    amount_usd=len(posts) * OWNED_READ_COST_USD,
                    basis="$0.001/owned read",
                ),
            )
        return ProviderResult.empty(provider=self.name)

    def search_recent(self, query: str, *, limit: int = 20) -> ProviderResult:
        client, unavailable = self._ensure_authenticated()
        if unavailable:
            return unavailable
        assert client is not None
        if not query.strip():
            return ProviderResult.error(provider=self.name, reason="missing_query")

        def perform_search() -> Any:
            return self._collect_pages(
                client.posts.search_recent,
                {"query": query},
                limit=limit,
                min_results=SEARCH_MIN_RESULTS,
            )

        try:
            posts = self._retry_with_refresh(perform_search)
        except Exception as exc:
            return ProviderResult.error(provider=self.name, reason="api_error", warnings=[str(exc)])

        if posts:
            return ProviderResult.ok(
                provider=self.name,
                items=posts,
                cost=CostEstimate(
                    amount_usd=len(posts) * POST_READ_COST_USD,
                    basis="$0.005/post read",
                ),
            )
        return ProviderResult.empty(provider=self.name)

    def read_thread(self, value: str, *, limit: int = 100) -> ProviderResult:
        post_id = extract_post_id(value)
        if not post_id:
            return ProviderResult.error(provider=self.name, reason="invalid_post_reference")
        query = f"conversation_id:{post_id}"
        result = self._search_recent_query(query, limit=limit)
        if result.status == "ok":
            return _with_extra_context(
                result,
                warning="official_recent_search_only",
                metadata={"query": query},
            )
        return result

    def read_replies(self, value: str, *, limit: int = 100) -> ProviderResult:
        post_id = extract_post_id(value)
        if not post_id:
            return ProviderResult.error(provider=self.name, reason="invalid_post_reference")
        query = f"conversation_id:{post_id} is:reply"
        result = self._search_recent_query(query, limit=limit)
        if result.status == "ok":
            return _with_extra_context(
                result,
                warning="official_recent_search_only",
                metadata={"query": query},
            )
        return result

    def read_quotes(self, value: str, *, limit: int = 100) -> ProviderResult:
        post_id = extract_post_id(value)
        if not post_id:
            return ProviderResult.error(provider=self.name, reason="invalid_post_reference")
        client, unavailable = self._ensure_authenticated()
        if unavailable:
            return unavailable
        assert client is not None
        try:
            posts = self._collect_pages(
                client.posts.get_quoted,
                {"id": post_id},
                limit=limit,
            )
        except AttributeError:
            return ProviderResult.unavailable(provider=self.name, reason="sdk_method_missing")
        except Exception as exc:
            return ProviderResult.error(provider=self.name, reason="api_error", warnings=[str(exc)])
        if posts:
            return ProviderResult.ok(
                provider=self.name,
                items=posts,
                cost=CostEstimate(
                    amount_usd=len(posts) * POST_READ_COST_USD,
                    basis="$0.005/post read",
                ),
            )
        return ProviderResult.empty(provider=self.name)

    def read_follow_graph(self, user: str, *, graph: str = "followers", limit: int = 100) -> ProviderResult:
        if graph not in {"followers", "following"}:
            return ProviderResult.error(provider=self.name, reason="invalid_graph")
        client, unavailable = self._ensure_authenticated()
        if unavailable:
            return unavailable
        assert client is not None

        handle_or_id = normalize_handle(user)
        if not handle_or_id:
            return ProviderResult.error(provider=self.name, reason="missing_user")
        try:
            user_id = handle_or_id if handle_or_id.isdigit() else self._resolve_user_id(client, handle_or_id)
            method = client.users.get_followers if graph == "followers" else client.users.get_following
            users = self._collect_user_pages(method, {"id": user_id}, limit=limit)
        except AttributeError:
            return ProviderResult.unavailable(provider=self.name, reason="sdk_method_missing")
        except Exception as exc:
            return ProviderResult.error(provider=self.name, reason="api_error", warnings=[str(exc)])
        if users:
            return ProviderResult.ok(
                provider=self.name,
                items=users,
                cost=CostEstimate(
                    amount_usd=len(users) * USER_READ_COST_USD,
                    basis="$0.010/user graph read",
                ),
                metadata={"graph": graph, "user_id": user_id},
            )
        return ProviderResult.empty(provider=self.name)

    def _search_recent_query(self, query: str, *, limit: int) -> ProviderResult:
        client, unavailable = self._ensure_authenticated()
        if unavailable:
            return unavailable
        assert client is not None
        try:
            posts = self._collect_pages(
                client.posts.search_recent,
                {"query": query},
                limit=limit,
                min_results=SEARCH_MIN_RESULTS,
            )
        except Exception as exc:
            return ProviderResult.error(provider=self.name, reason="api_error", warnings=[str(exc)])
        if posts:
            return ProviderResult.ok(
                provider=self.name,
                items=posts,
                cost=CostEstimate(
                    amount_usd=len(posts) * POST_READ_COST_USD,
                    basis="$0.005/post read",
                ),
            )
        return ProviderResult.empty(provider=self.name)

    def _collect_pages(
        self,
        method: Callable[..., Any],
        base_kwargs: dict[str, Any],
        *,
        limit: int,
        min_results: int = 1,
        max_results_cap: int = POSTS_MAX_RESULTS,
    ) -> list[Post]:
        capped_limit = max(1, min(int(limit), POSTS_MAX_RESULTS))
        tweet_fields = ["created_at", "public_metrics", "text", "author_id"]
        request_size = max(min_results, min(capped_limit, max_results_cap))
        kwargs = {**base_kwargs, "max_results": request_size, "tweet_fields": tweet_fields}
        results: list[Post] = []
        for page in method(**kwargs):
            for item in _data_items(page):
                post = _post_from_obj(item)
                if post:
                    results.append(post)
                    if len(results) >= capped_limit:
                        return results
        return results

    def _collect_user_pages(
        self,
        method: Callable[..., Any],
        base_kwargs: dict[str, Any],
        *,
        limit: int,
        min_results: int = 1,
        max_results_cap: int = USER_GRAPH_MAX_RESULTS,
    ) -> list[UserProfile]:
        capped_limit = max(1, min(int(limit), POSTS_MAX_RESULTS))
        user_fields = ["created_at", "description", "public_metrics", "username", "name"]
        request_size = max(min_results, min(capped_limit, max_results_cap))
        kwargs = {**base_kwargs, "max_results": request_size, "user_fields": user_fields}
        results: list[UserProfile] = []
        for page in method(**kwargs):
            for item in _data_items(page):
                user = _user_from_obj(item)
                if user:
                    results.append(user)
                    if len(results) >= capped_limit:
                        return results
        return results

    def _resolve_user_id(self, client: Any, username: str) -> str:
        response = client.users.get_by_username(username=username)
        items = _data_items(response)
        if not items:
            raise ValueError(f"user_not_found:{username}")
        user_id = _value(items[0], "id")
        if not user_id:
            raise ValueError(f"user_id_missing:{username}")
        return str(user_id)

    def _get_me_id(self, client: Any) -> str:
        response = client.users.get_me()
        items = _data_items(response)
        if not items:
            raise ValueError("authenticated_user_not_found")
        user_id = _value(items[0], "id")
        if not user_id:
            raise ValueError("authenticated_user_id_missing")
        return str(user_id)
