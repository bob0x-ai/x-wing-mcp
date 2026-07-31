"""Read-only provider for SocialData.tools X endpoints."""

from __future__ import annotations

import json
import os
import random
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from xdata.contracts import CostEstimate, Metrics, Post, ProviderResult, UserProfile, UserRef
from xdata.providers.base import CooldownMixin, RateLimiterMixin
from xdata.providers.syndication import extract_post_id, normalize_handle

PROVIDER_NAME = "socialdata"
API_BASE_URL = "https://api.socialdata.tools/twitter"
DEFAULT_TIMEOUT_SECONDS = 20
DEFAULT_COOLDOWN_SECONDS = 60
DEFAULT_REQUESTS_PER_MINUTE = 20
DEFAULT_JITTER_SECONDS = 0.25
ITEM_COST_USD = 0.0002
USER_AGENT = "x-mcp/0.1 (+https://github.com/local/x-mcp)"


@dataclass(frozen=True)
class HttpResponse:
    status_code: int
    text: str
    headers: dict[str, str] | None = None

    def json(self) -> Any:
        return json.loads(self.text)


HttpGet = Callable[[str, dict[str, str], int], HttpResponse]


def default_http_get(
    url: str,
    headers: dict[str, str],
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
) -> HttpResponse:
    request = Request(url, headers={"User-Agent": USER_AGENT, **headers})
    try:
        with urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8", errors="replace")
            return HttpResponse(
                status_code=getattr(response, "status", 200),
                text=body,
                headers=dict(response.headers.items()),
            )
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        return HttpResponse(status_code=exc.code, text=body, headers=dict(exc.headers.items()))
    except URLError as exc:
        raise RuntimeError(str(exc)) from exc


def _int_or_none(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _author_from_payload(payload: dict[str, Any]) -> UserRef | None:
    user = payload.get("user")
    if not isinstance(user, dict):
        return None
    return UserRef(
        id=str(user.get("id_str") or user.get("id") or "") or None,
        username=user.get("screen_name"),
        name=user.get("name"),
    )


def _metrics_from_payload(payload: dict[str, Any]) -> Metrics | None:
    metrics = Metrics(
        replies=_int_or_none(payload.get("reply_count")),
        reposts=_int_or_none(payload.get("retweet_count")),
        likes=_int_or_none(payload.get("favorite_count")),
        quotes=_int_or_none(payload.get("quote_count")),
        views=_int_or_none(payload.get("views_count")),
    )
    if all(value is None for value in metrics.__dict__.values()):
        return None
    return metrics


def post_from_payload(payload: dict[str, Any]) -> Post | None:
    post_id = str(payload.get("id_str") or payload.get("id") or "").strip()
    text = str(payload.get("full_text") or payload.get("text") or "").strip()
    if not post_id or not text:
        return None
    author = _author_from_payload(payload)
    username = author.username if author else None
    source_url = f"https://x.com/{username}/status/{post_id}" if username else f"https://x.com/i/web/status/{post_id}"
    return Post(
        id=post_id,
        text=text,
        author=author,
        created_at=payload.get("tweet_created_at") or payload.get("created_at"),
        metrics=_metrics_from_payload(payload),
        source_url=source_url,
        raw=payload,
    )


def user_from_payload(payload: dict[str, Any]) -> UserProfile | None:
    user_id = str(payload.get("id_str") or payload.get("id") or "").strip()
    username = str(payload.get("screen_name") or "").strip() or None
    if not user_id:
        return None
    public_metrics = {
        key: value
        for key, value in {
            "followers_count": _int_or_none(payload.get("followers_count")),
            "friends_count": _int_or_none(payload.get("friends_count")),
            "listed_count": _int_or_none(payload.get("listed_count")),
            "favourites_count": _int_or_none(payload.get("favourites_count")),
            "statuses_count": _int_or_none(payload.get("statuses_count")),
        }.items()
        if value is not None
    }
    return UserProfile(
        id=user_id,
        username=username,
        name=payload.get("name"),
        description=payload.get("description"),
        public_metrics=public_metrics or None,
        source_url=f"https://x.com/{username}" if username else None,
        raw=payload,
    )


def _socialdata_estimate_units(task: str, **kwargs: Any) -> int | None:
    if task == "fetch_urls":
        values = kwargs.get("values") or []
        return max(0, len(values))
    if task in {
        "read_user_posts_recent",
        "search_posts",
        "read_thread",
        "read_replies",
        "read_quotes",
        "read_follow_graph",
        "collect_posts",
    }:
        limit = kwargs.get("limit", 20)
        try:
            return max(1, int(limit))
        except (TypeError, ValueError):
            return 1
    return None


class SocialDataProvider(CooldownMixin, RateLimiterMixin):
    name = PROVIDER_NAME

    def __init__(
        self,
        *,
        http_get: HttpGet = default_http_get,
        timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
        cooldown_seconds: int = DEFAULT_COOLDOWN_SECONDS,
        time_fn: Callable[[], float] = time.time,
        sleep_fn: Callable[[float], None] = time.sleep,
        random_fn: Callable[[], float] = random.random,
        requests_per_minute: float = DEFAULT_REQUESTS_PER_MINUTE,
        min_interval_seconds: float | None = None,
        jitter_seconds: float = DEFAULT_JITTER_SECONDS,
    ) -> None:
        super().__init__(time_fn=time_fn, cooldown_seconds=cooldown_seconds)
        RateLimiterMixin.__init__(
            self,
            time_fn=time_fn,
            sleep_fn=sleep_fn,
            random_fn=random_fn,
            requests_per_minute=requests_per_minute,
            min_interval_seconds=min_interval_seconds,
            jitter_seconds=jitter_seconds,
        )
        self._http_get = http_get
        self._timeout_seconds = timeout_seconds

    def _api_key(self) -> str | None:
        return os.getenv("SOCIALDATA_API_KEY")

    def status(self) -> dict[str, Any]:
        return {
            "auth_required": True,
            "auth_present": bool(self._api_key()),
            "read_only": True,
            "supports_tasks": [
                "fetch_urls",
                "read_user_posts_recent",
                "search_posts",
                "read_thread",
                "read_replies",
                "read_quotes",
                "read_follow_graph",
                "collect_posts",
            ],
            "rate_limit": "120 rpm shared across endpoints",
            "limited_access_endpoints": [
                "search",
                "user_tweets",
                "tweet_comments",
                "tweet_quotes",
                "thread",
                "followers",
                "following",
            ],
            **self._cooldown_status(),
            **self._rate_limit_status(),
        }

    def estimate_cost(self, task: str, **kwargs: Any) -> CostEstimate | None:
        units = _socialdata_estimate_units(task, **kwargs)
        if units is None:
            return None
        return CostEstimate(
            amount_usd=round(units * ITEM_COST_USD, 6),
            basis="$0.20 / 1,000 tweets or user profiles (upper bound by requested limit)",
        )

    def fetch_urls(self, values: list[str]) -> ProviderResult:
        blocked = self._cooldown_unavailable(self.name)
        if blocked:
            return blocked
        posts: list[Post] = []
        warnings: list[str] = []

        for value in values:
            post_id = extract_post_id(value)
            if not post_id:
                warnings.append(f"invalid_post_reference:{value}")
                continue
            payload, failure = self._request_json(f"/tweets/{quote(post_id)}")
            if failure:
                if failure.reason == "not_found":
                    warnings.append(f"post_unavailable:{post_id}")
                    continue
                return ProviderResult(
                    status=failure.status,
                    provider=failure.provider,
                    items=failure.items,
                    reason=failure.reason,
                    warnings=[*failure.warnings, *warnings],
                    cost=failure.cost,
                    raw_ref=failure.raw_ref,
                    metadata=failure.metadata,
                )
            assert isinstance(payload, dict)
            post = post_from_payload(payload)
            if post:
                posts.append(post)
            else:
                warnings.append(f"unparseable_post:{post_id}")

        if posts:
            return ProviderResult.ok(
                provider=self.name,
                items=posts,
                warnings=warnings,
                cost=CostEstimate(
                    amount_usd=len(posts) * ITEM_COST_USD,
                    basis="$0.20 / 1,000 tweets or user profiles",
                ),
            )
        if warnings:
            return ProviderResult.unavailable(
                provider=self.name,
                reason="no_fetchable_posts",
                warnings=warnings,
            )
        return ProviderResult.empty(provider=self.name)

    def read_user_posts(self, user: str, *, limit: int = 20, cursor: str | None = None) -> ProviderResult:
        blocked = self._cooldown_unavailable(self.name)
        if blocked:
            return blocked
        user_id, failure = self._resolve_user_id(user)
        if failure:
            return failure
        assert user_id is not None
        return self._collect_posts(
            path=f"/user/{quote(user_id)}/tweets",
            limit=limit,
            cursor=cursor,
            metadata={"user_id": user_id},
        )

    def search_posts(self, query: str, *, limit: int = 20, cursor: str | None = None) -> ProviderResult:
        blocked = self._cooldown_unavailable(self.name)
        if blocked:
            return blocked
        query = str(query or "").strip()
        if not query:
            return ProviderResult.error(provider=self.name, reason="missing_query")
        return self._collect_posts(
            path="/search",
            limit=limit,
            cursor=cursor,
            params={"query": query, "type": "Latest"},
            metadata={"query": query, "search_type": "Latest"},
        )

    def read_thread(self, value: str, *, limit: int = 100, cursor: str | None = None) -> ProviderResult:
        blocked = self._cooldown_unavailable(self.name)
        if blocked:
            return blocked
        post_id = extract_post_id(value)
        if not post_id:
            return ProviderResult.error(provider=self.name, reason="invalid_post_reference")
        return self._collect_posts(
            path=f"/thread/{quote(post_id)}",
            limit=limit,
            cursor=cursor,
            metadata={"thread_id": post_id},
        )

    def read_replies(self, value: str, *, limit: int = 100, cursor: str | None = None) -> ProviderResult:
        blocked = self._cooldown_unavailable(self.name)
        if blocked:
            return blocked
        post_id = extract_post_id(value)
        if not post_id:
            return ProviderResult.error(provider=self.name, reason="invalid_post_reference")
        return self._collect_posts(
            path=f"/tweets/{quote(post_id)}/comments",
            limit=limit,
            cursor=cursor,
            warnings=["top_level_post_only"],
            metadata={"post_id": post_id},
        )

    def read_quotes(self, value: str, *, limit: int = 100, cursor: str | None = None) -> ProviderResult:
        blocked = self._cooldown_unavailable(self.name)
        if blocked:
            return blocked
        post_id = extract_post_id(value)
        if not post_id:
            return ProviderResult.error(provider=self.name, reason="invalid_post_reference")
        return self._collect_posts(
            path=f"/tweets/{quote(post_id)}/quotes",
            limit=limit,
            cursor=cursor,
            metadata={"post_id": post_id},
        )

    def read_follow_graph(
        self,
        user: str,
        *,
        graph: str = "followers",
        limit: int = 100,
        cursor: str | None = None,
    ) -> ProviderResult:
        blocked = self._cooldown_unavailable(self.name)
        if blocked:
            return blocked
        if graph not in {"followers", "following"}:
            return ProviderResult.error(provider=self.name, reason="invalid_graph")
        user_id, failure = self._resolve_user_id(user)
        if failure:
            return failure
        assert user_id is not None
        path = "/followers/list" if graph == "followers" else "/friends/list"
        return self._collect_users(
            path=path,
            limit=limit,
            cursor=cursor,
            params={"user_id": user_id},
            metadata={"graph": graph, "user_id": user_id},
        )

    def collect_posts(self, query: str, *, limit: int = 100, cursor: str | None = None) -> ProviderResult:
        blocked = self._cooldown_unavailable(self.name)
        if blocked:
            return blocked
        query = str(query or "").strip()
        if not query:
            return ProviderResult.error(provider=self.name, reason="missing_query")
        return self._collect_posts(
            path="/search",
            limit=limit,
            cursor=cursor,
            params={"query": query, "type": "Latest"},
            metadata={"query": query, "search_type": "Latest", "mode": "bulk"},
        )

    def _resolve_user_id(self, user: str) -> tuple[str | None, ProviderResult | None]:
        handle_or_id = normalize_handle(user)
        if not handle_or_id:
            return None, ProviderResult.error(provider=self.name, reason="missing_user")
        if handle_or_id.isdigit():
            return handle_or_id, None
        payload, failure = self._request_json(f"/user/{quote(handle_or_id)}")
        if failure:
            return None, failure
        assert isinstance(payload, dict)
        user_id = str(payload.get("id_str") or payload.get("id") or "").strip()
        if not user_id:
            return None, ProviderResult.error(provider=self.name, reason="user_id_missing")
        return user_id, None

    def _collect_posts(
        self,
        *,
        path: str,
        limit: int,
        cursor: str | None = None,
        params: dict[str, Any] | None = None,
        warnings: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> ProviderResult:
        capped_limit = max(1, int(limit))
        items: list[Post] = []
        notes = list(warnings or [])
        next_cursor: str | None = None

        while len(items) < capped_limit:
            page_params = dict(params or {})
            if cursor:
                page_params["cursor"] = cursor
            payload, failure = self._request_json(path, params=page_params)
            if failure:
                if items:
                    return ProviderResult.ok(
                        provider=self.name,
                        items=items,
                        warnings=[*notes, f"pagination_stopped:{failure.reason}"],
                        cost=CostEstimate(
                            amount_usd=len(items) * ITEM_COST_USD,
                            basis="$0.20 / 1,000 tweets or user profiles",
                        ),
                        metadata={
                            **(metadata or {}),
                            "next_cursor": cursor,
                            "partial": True,
                        },
                    )
                return failure
            assert isinstance(payload, dict)
            raw_items = payload.get("tweets")
            if not isinstance(raw_items, list):
                return ProviderResult.error(provider=self.name, reason="unexpected_payload")
            for raw_item in raw_items:
                if not isinstance(raw_item, dict):
                    continue
                post = post_from_payload(raw_item)
                if post:
                    items.append(post)
                    if len(items) >= capped_limit:
                        break
            next_cursor = _cursor_str(payload.get("next_cursor"))
            if len(items) >= capped_limit or not next_cursor:
                break
            cursor = next_cursor

        if items:
            return ProviderResult.ok(
                provider=self.name,
                items=items,
                warnings=notes,
                cost=CostEstimate(
                    amount_usd=len(items) * ITEM_COST_USD,
                    basis="$0.20 / 1,000 tweets or user profiles",
                ),
                metadata={**(metadata or {}), "next_cursor": next_cursor},
            )
        return ProviderResult.empty(provider=self.name)

    def _collect_users(
        self,
        *,
        path: str,
        limit: int,
        cursor: str | None = None,
        params: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> ProviderResult:
        capped_limit = max(1, int(limit))
        items: list[UserProfile] = []
        next_cursor: str | None = None

        while len(items) < capped_limit:
            page_params = dict(params or {})
            if cursor:
                page_params["cursor"] = cursor
            payload, failure = self._request_json(path, params=page_params)
            if failure:
                if items:
                    return ProviderResult.ok(
                        provider=self.name,
                        items=items,
                        warnings=[f"pagination_stopped:{failure.reason}"],
                        cost=CostEstimate(
                            amount_usd=len(items) * ITEM_COST_USD,
                            basis="$0.20 / 1,000 tweets or user profiles",
                        ),
                        metadata={
                            **(metadata or {}),
                            "next_cursor": cursor,
                            "partial": True,
                        },
                    )
                return failure
            assert isinstance(payload, dict)
            raw_items = payload.get("users")
            if not isinstance(raw_items, list):
                return ProviderResult.error(provider=self.name, reason="unexpected_payload")
            for raw_item in raw_items:
                if not isinstance(raw_item, dict):
                    continue
                user = user_from_payload(raw_item)
                if user:
                    items.append(user)
                    if len(items) >= capped_limit:
                        break
            next_cursor = _cursor_str(payload.get("next_cursor"))
            if len(items) >= capped_limit or not next_cursor:
                break
            cursor = next_cursor

        if items:
            return ProviderResult.ok(
                provider=self.name,
                items=items,
                cost=CostEstimate(
                    amount_usd=len(items) * ITEM_COST_USD,
                    basis="$0.20 / 1,000 tweets or user profiles",
                ),
                metadata={**(metadata or {}), "next_cursor": next_cursor},
            )
        return ProviderResult.empty(provider=self.name)

    def _request_json(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Any] | None, ProviderResult | None]:
        api_key = self._api_key()
        if not api_key:
            return None, ProviderResult.unavailable(
                provider=self.name,
                reason="auth_required",
                warnings=["missing SOCIALDATA_API_KEY"],
            )

        url = _build_url(path, params)
        self._wait_for_rate_limit()
        try:
            response = self._http_get(
                url,
                {
                    "Authorization": f"Bearer {api_key}",
                    "Accept": "application/json",
                },
                self._timeout_seconds,
            )
        except Exception as exc:
            return None, ProviderResult.error(
                provider=self.name,
                reason="transport_error",
                warnings=[str(exc)],
            )

        payload: dict[str, Any] | None = None
        message: str | None = None
        if response.text.strip():
            try:
                parsed = response.json()
            except json.JSONDecodeError:
                if 200 <= response.status_code < 300:
                    return None, ProviderResult.error(provider=self.name, reason="invalid_json")
                # For non-2xx, a non-JSON body is fine; fall through to status mapping.
            else:
                if isinstance(parsed, dict):
                    payload = parsed
                    if parsed.get("status") == "error":
                        message = str(parsed.get("message") or "").strip() or None
                elif 200 <= response.status_code < 300:
                    # A 2xx response must be a dict payload.
                    return None, ProviderResult.error(provider=self.name, reason="unexpected_payload")
                # A non-2xx non-dict body carries no usable payload; fall through
                # to status-code mapping below.

        if 200 <= response.status_code < 300:
            if payload is None:
                return None, ProviderResult.error(provider=self.name, reason="unexpected_payload")
            return payload, None

        warnings = [message] if message else []
        if response.status_code == 401:
            return None, ProviderResult.unavailable(provider=self.name, reason="invalid_api_key", warnings=warnings)
        if response.status_code == 402:
            return None, ProviderResult.unavailable(
                provider=self.name,
                reason="insufficient_balance",
                warnings=warnings,
            )
        if response.status_code == 403:
            return None, ProviderResult.unavailable(provider=self.name, reason="forbidden", warnings=warnings)
        if response.status_code == 404:
            return None, ProviderResult.unavailable(provider=self.name, reason="not_found", warnings=warnings)
        if response.status_code == 422:
            return None, ProviderResult.error(provider=self.name, reason="validation_failed", warnings=warnings)
        if response.status_code == 429:
            self._activate_cooldown("rate_limited")
            return None, ProviderResult.unavailable(provider=self.name, reason="rate_limited", warnings=warnings)
        if response.status_code >= 500:
            return None, ProviderResult.error(provider=self.name, reason="upstream_error", warnings=warnings)
        return None, ProviderResult.error(
            provider=self.name,
            reason=f"http_{response.status_code}",
            warnings=warnings,
        )


def _build_url(path: str, params: dict[str, Any] | None = None) -> str:
    url = f"{API_BASE_URL}{path}"
    if not params:
        return url
    encoded = urlencode(
        {key: value for key, value in params.items() if value is not None},
        doseq=True,
    )
    return f"{url}?{encoded}" if encoded else url


def _cursor_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
