"""Read-only official X API provider using the Python XDK directly."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import x_client
from x_usage_ledger import attach_xdk_response_logging
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


def _load_xdk_client_factory() -> ClientFactory | None:
    try:
        from xdk import Client  # type: ignore
    except Exception:
        return None
    return lambda access_token: attach_xdk_response_logging(Client(access_token=access_token))


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
    if task == "read_user_posts_recent":
        limit = max(USER_POSTS_MIN_RESULTS, _estimate_limit(kwargs.get("limit", 20)))
        return limit * POST_READ_COST_USD, "$0.005/post read (includes endpoint page-size minimum)"
    if task in {"search_posts", "read_thread", "read_replies"}:
        limit = max(SEARCH_MIN_RESULTS, _estimate_limit(kwargs.get("limit", 20)))
        return limit * POST_READ_COST_USD, "$0.005/post read (includes endpoint page-size minimum)"
    if task == "read_quotes":
        limit = _estimate_limit(kwargs.get("limit", 20))
        return limit * POST_READ_COST_USD, "$0.005/post read (upper bound by requested limit)"
    if task in {"read_owned_timeline", "read_mentions"}:
        floor = TIMELINE_MIN_RESULTS if task == "read_owned_timeline" else MENTIONS_MIN_RESULTS
        limit = max(floor, _estimate_limit(kwargs.get("limit", 20)))
        return limit * OWNED_READ_COST_USD, "$0.001/owned-account post read (includes endpoint page-size minimum)"
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

    Credential loading and refresh are delegated to x_client, the merged
    server's single credential owner.
    """

    name = PROVIDER_NAME

    def __init__(self, *, client_factory: ClientFactory | None = None) -> None:
        self._client_factory = client_factory

    def _access_token(self) -> str | None:
        return x_client.get_access_token()

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

    def _ensure_authenticated(self) -> tuple[Any | None, ProviderResult | None]:
        """
        Get authenticated client, refreshing token if needed.
        Returns (client, error_result) where error_result is None on success.
        """
        if not self._access_token():
            return self._client()

        try:
            access_token = x_client.ensure_access_token()
        except x_client.AuthRefreshError as exc:
            return None, ProviderResult.error(
                provider=self.name,
                reason="auth_token_refresh_failed",
                warnings=[str(exc)],
            )

        client, unavailable = self._client(access_token)
        if unavailable:
            return client, unavailable
        assert client is not None
        return client, None

    def _retry_with_refresh(self, api_call: Callable[[], Any]) -> Any:
        """
        Execute an API call with automatic token refresh on 401 errors.
        Returns the API result.
        """
        max_retries = 2
        for attempt in range(max_retries):
            try:
                # Authentication was validated once before the operation. Do
                # not validate again here: each validation is its own X API
                # request and formerly multiplied read traffic.
                return api_call()

            except Exception as exc:
                # Check if this is a 401 auth error
                error_str = str(exc)
                if "401" not in error_str and "unauthorized" not in error_str.lower():
                    # Not an auth error, re-raise
                    raise

                # Check if we should retry (first attempt failed, second attempt will work after refresh)
                if attempt == 0:
                    try:
                        x_client.ensure_access_token()
                    except x_client.AuthRefreshError as refresh_exc:
                        raise Exception(f"Token refresh failed: {refresh_exc}") from exc

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
        post_fields = ["created_at", "public_metrics", "text", "author_id"]

        def fetch_single_post(value: str) -> list[Post] | None:
            post_id = extract_post_id(value)
            if not post_id:
                warnings.append(f"invalid_post_reference:{value}")
                return None
            try:
                response = client.posts.get_by_id(id=post_id, post_fields=post_fields)
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
            posts, billed_resources = self._retry_with_refresh(resolve_and_fetch)
        except Exception as exc:
            return ProviderResult.error(provider=self.name, reason="api_error", warnings=[str(exc)])

        if posts:
            return ProviderResult.ok(
                provider=self.name,
                items=posts,
                cost=CostEstimate(
                    amount_usd=billed_resources * POST_READ_COST_USD,
                    basis="$0.005/post read (returned API resources)",
                ),
                metadata={"billed_resource_count": billed_resources},
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
            posts, billed_resources = self._retry_with_refresh(fetch_timeline)
        except Exception as exc:
            return ProviderResult.error(provider=self.name, reason="api_error", warnings=[str(exc)])

        if posts:
            return ProviderResult.ok(
                provider=self.name,
                items=posts,
                cost=CostEstimate(
                    amount_usd=billed_resources * OWNED_READ_COST_USD,
                    basis="$0.001/owned read (returned API resources)",
                ),
                metadata={"billed_resource_count": billed_resources},
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
            posts, billed_resources = self._retry_with_refresh(fetch_mentions)
        except Exception as exc:
            return ProviderResult.error(provider=self.name, reason="api_error", warnings=[str(exc)])

        if posts:
            return ProviderResult.ok(
                provider=self.name,
                items=posts,
                cost=CostEstimate(
                    amount_usd=billed_resources * OWNED_READ_COST_USD,
                    basis="$0.001/owned read (returned API resources)",
                ),
                metadata={"billed_resource_count": billed_resources},
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
            posts, billed_resources = self._retry_with_refresh(perform_search)
        except Exception as exc:
            return ProviderResult.error(provider=self.name, reason="api_error", warnings=[str(exc)])

        if posts:
            return ProviderResult.ok(
                provider=self.name,
                items=posts,
                cost=CostEstimate(
                    amount_usd=billed_resources * POST_READ_COST_USD,
                    basis="$0.005/post read (returned API resources)",
                ),
                metadata={"billed_resource_count": billed_resources},
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
            posts, billed_resources = self._collect_pages(
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
                    amount_usd=billed_resources * POST_READ_COST_USD,
                    basis="$0.005/post read (returned API resources)",
                ),
                metadata={"billed_resource_count": billed_resources},
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
            users, billed_resources = self._collect_user_pages(method, {"id": user_id}, limit=limit)
        except AttributeError:
            return ProviderResult.unavailable(provider=self.name, reason="sdk_method_missing")
        except Exception as exc:
            return ProviderResult.error(provider=self.name, reason="api_error", warnings=[str(exc)])
        if users:
            return ProviderResult.ok(
                provider=self.name,
                items=users,
                cost=CostEstimate(
                    amount_usd=billed_resources * USER_READ_COST_USD,
                    basis="$0.010/user graph read (returned API resources)",
                ),
                metadata={"graph": graph, "user_id": user_id, "billed_resource_count": billed_resources},
            )
        return ProviderResult.empty(provider=self.name)

    def _search_recent_query(self, query: str, *, limit: int) -> ProviderResult:
        client, unavailable = self._ensure_authenticated()
        if unavailable:
            return unavailable
        assert client is not None
        try:
            posts, billed_resources = self._collect_pages(
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
                    amount_usd=billed_resources * POST_READ_COST_USD,
                    basis="$0.005/post read (returned API resources)",
                ),
                metadata={"billed_resource_count": billed_resources},
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
    ) -> tuple[list[Post], int]:
        capped_limit = max(1, min(int(limit), POSTS_MAX_RESULTS))
        post_fields = ["created_at", "public_metrics", "text", "author_id"]
        request_size = max(min_results, min(capped_limit, max_results_cap))
        kwargs = {**base_kwargs, "max_results": request_size, "post_fields": post_fields}
        results: list[Post] = []
        billed_resources = 0
        for page in method(**kwargs):
            page_items = _data_items(page)
            billed_resources += len(page_items)
            for item in page_items:
                post = _post_from_obj(item)
                if post:
                    results.append(post)
                    if len(results) >= capped_limit:
                        return results, billed_resources
        return results, billed_resources

    def _collect_user_pages(
        self,
        method: Callable[..., Any],
        base_kwargs: dict[str, Any],
        *,
        limit: int,
        min_results: int = 1,
        max_results_cap: int = USER_GRAPH_MAX_RESULTS,
    ) -> tuple[list[UserProfile], int]:
        capped_limit = max(1, min(int(limit), POSTS_MAX_RESULTS))
        user_fields = ["created_at", "description", "public_metrics", "username", "name"]
        request_size = max(min_results, min(capped_limit, max_results_cap))
        kwargs = {**base_kwargs, "max_results": request_size, "user_fields": user_fields}
        results: list[UserProfile] = []
        billed_resources = 0
        for page in method(**kwargs):
            page_items = _data_items(page)
            billed_resources += len(page_items)
            for item in page_items:
                user = _user_from_obj(item)
                if user:
                    results.append(user)
                    if len(results) >= capped_limit:
                        return results, billed_resources
        return results, billed_resources

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
