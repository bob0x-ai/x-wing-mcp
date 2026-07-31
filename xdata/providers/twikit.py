"""Read-only Twikit provider with cookie-file session reuse."""

from __future__ import annotations

import asyncio
import json
import random
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from xdata.contracts import CostEstimate, Metrics, Post, ProviderResult, UserRef
from xdata.providers.base import CooldownMixin, RateLimiterMixin
from xdata.providers.syndication import normalize_handle


PROVIDER_NAME = "twikit"
DEFAULT_COOLDOWN_SECONDS = 300
DEFAULT_REQUESTS_PER_MINUTE = 6
DEFAULT_JITTER_SECONDS = 0.75
DEFAULT_LOCALE = "en-US"
DEFAULT_EXPIRY_WARNING_DAYS = 7


ClientFactory = Callable[[str], Any]


def _load_twikit_client_factory() -> ClientFactory | None:
    try:
        from twikit import Client  # type: ignore
    except Exception:
        return None
    return lambda locale: Client(locale)


def _maybe_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _value(obj: Any, key: str, default: Any = None) -> Any:
    return getattr(obj, key, default)


def _metrics_from_obj(obj: Any) -> Metrics | None:
    metrics = Metrics(
        replies=_maybe_int(_value(obj, "reply_count")),
        reposts=_maybe_int(_value(obj, "retweet_count")),
        likes=_maybe_int(_value(obj, "favorite_count") or _value(obj, "like_count")),
        quotes=_maybe_int(_value(obj, "quote_count")),
        views=_maybe_int(_value(obj, "view_count") or _value(obj, "views_count")),
    )
    if all(value is None for value in metrics.__dict__.values()):
        return None
    return metrics


def _author_from_tweet(obj: Any) -> UserRef | None:
    user = _value(obj, "user")
    if user is None:
        return None
    return UserRef(
        id=str(_value(user, "id", "") or "").strip() or None,
        username=_value(user, "screen_name"),
        name=_value(user, "name"),
    )


def post_from_obj(obj: Any) -> Post | None:
    post_id = str(_value(obj, "id", "") or "").strip()
    text = str(_value(obj, "full_text") or _value(obj, "text") or "").strip()
    if not post_id or not text:
        return None
    author = _author_from_tweet(obj)
    username = author.username if author else None
    source_url = f"https://x.com/{username}/status/{post_id}" if username else f"https://x.com/i/web/status/{post_id}"
    raw = obj if isinstance(obj, dict) else dict(getattr(obj, "__dict__", {}))
    return Post(
        id=post_id,
        text=text,
        author=author,
        created_at=_value(obj, "created_at"),
        metrics=_metrics_from_obj(obj),
        source_url=source_url,
        raw=raw,
    )


class TwikitProvider(CooldownMixin, RateLimiterMixin):
    name = PROVIDER_NAME

    def __init__(
        self,
        *,
        cookies_file: str,
        locale: str = DEFAULT_LOCALE,
        client_factory: ClientFactory | None = None,
        cooldown_seconds: int = DEFAULT_COOLDOWN_SECONDS,
        time_fn: Callable[[], float] = time.time,
        sleep_fn: Callable[[float], None] = time.sleep,
        random_fn: Callable[[], float] = random.random,
        requests_per_minute: float = DEFAULT_REQUESTS_PER_MINUTE,
        min_interval_seconds: float | None = None,
        jitter_seconds: float = DEFAULT_JITTER_SECONDS,
    ) -> None:
        CooldownMixin.__init__(self, time_fn=time_fn, cooldown_seconds=cooldown_seconds)
        RateLimiterMixin.__init__(
            self,
            time_fn=time_fn,
            sleep_fn=sleep_fn,
            random_fn=random_fn,
            requests_per_minute=requests_per_minute,
            min_interval_seconds=min_interval_seconds,
            jitter_seconds=jitter_seconds,
        )
        self._cookies_file = str(cookies_file)
        self._locale = locale
        self._client_factory = client_factory
        self._last_session_error: str | None = None

    def status(self) -> dict[str, Any]:
        cookies_path = Path(self._cookies_file)
        expiry_status = _load_cookie_expiry_status(cookies_path, warning_days=DEFAULT_EXPIRY_WARNING_DAYS)
        return {
            "auth_required": True,
            "auth_mode": "cookies_file",
            "sdk_available": self._client_factory is not None or _load_twikit_client_factory() is not None,
            "session_file": self._cookies_file,
            "session_file_exists": cookies_path.exists(),
            "session_meta_file": expiry_status.get("meta_file"),
            "cookie_expiry": expiry_status.get("cookie_expiry"),
            "expiry_warnings": expiry_status.get("warnings"),
            "read_only": True,
            "supports_tasks": ["read_user_posts_recent", "search_posts"],
            "limitations": [
                "cookie_file_only_in_v1",
                "no_login_flow_in_provider",
                "fallback_only",
                "single_account_cautious_mode",
            ],
            "last_session_error": self._last_session_error,
            **self._cooldown_status(),
            **self._rate_limit_status(),
        }

    def estimate_cost(self, task: str, **kwargs: Any) -> CostEstimate | None:
        del kwargs
        if task in {"read_user_posts_recent", "search_posts"}:
            return CostEstimate(amount_usd=0.0, basis="local Twikit session reuse")
        return None

    def read_user_posts(self, user: str, *, limit: int = 20) -> ProviderResult:
        blocked = self._cooldown_unavailable(self.name)
        if blocked:
            return blocked
        handle = normalize_handle(user)
        if not handle:
            return ProviderResult.error(provider=self.name, reason="missing_user")
        try:
            return self._run_async(self._read_user_posts_async(handle, limit=limit))
        except Exception as exc:
            return self._map_exception(exc)

    def search_posts(self, query: str, *, limit: int = 20) -> ProviderResult:
        blocked = self._cooldown_unavailable(self.name)
        if blocked:
            return blocked
        query = str(query or "").strip()
        if not query:
            return ProviderResult.error(provider=self.name, reason="missing_query")
        try:
            return self._run_async(self._search_posts_async(query, limit=limit))
        except Exception as exc:
            return self._map_exception(exc)

    async def _read_user_posts_async(self, handle: str, *, limit: int) -> ProviderResult:
        client = await self._client()
        self._wait_for_rate_limit()
        user = await client.get_user_by_screen_name(handle)
        user_id = str(_value(user, "id", "") or "").strip()
        if not user_id:
            return ProviderResult.error(provider=self.name, reason="user_id_missing")
        self._wait_for_rate_limit()
        first_page = await client.get_user_tweets(user_id, "Tweets", count=min(max(int(limit), 1), 40))
        posts, next_cursor = await self._collect_result_pages(first_page, limit=limit)
        if posts:
            return ProviderResult.ok(
                provider=self.name,
                items=posts,
                metadata={"user_id": user_id, "next_cursor": next_cursor},
            )
        return ProviderResult.empty(provider=self.name)

    async def _search_posts_async(self, query: str, *, limit: int) -> ProviderResult:
        client = await self._client()
        self._wait_for_rate_limit()
        first_page = await client.search_tweet(query, "Latest", count=min(max(int(limit), 1), 20))
        posts, next_cursor = await self._collect_result_pages(first_page, limit=limit)
        if posts:
            return ProviderResult.ok(
                provider=self.name,
                items=posts,
                metadata={"query": query, "search_type": "Latest", "next_cursor": next_cursor},
            )
        return ProviderResult.empty(provider=self.name)

    async def _client(self) -> Any:
        cookies_path = Path(self._cookies_file)
        if not cookies_path.exists():
            self._last_session_error = "session_file_missing"
            raise _ProviderFailure("session_file_missing")
        factory = self._client_factory or _load_twikit_client_factory()
        if factory is None:
            raise _ProviderFailure("sdk_missing")
        client = factory(self._locale)
        try:
            await client.load_cookies(str(cookies_path))
        except TypeError:
            maybe_result = client.load_cookies(str(cookies_path))
            if asyncio.iscoroutine(maybe_result):
                await maybe_result
        except Exception as exc:
            self._last_session_error = f"load_cookies_failed:{exc.__class__.__name__}"
            raise
        return client

    async def _collect_result_pages(self, first_page: Any, *, limit: int) -> tuple[list[Post], str | None]:
        capped_limit = max(1, int(limit))
        posts: list[Post] = []
        page = first_page
        next_cursor = _value(page, "next_cursor")
        while True:
            for item in list(page):
                post = post_from_obj(item)
                if post:
                    posts.append(post)
                    if len(posts) >= capped_limit:
                        return posts, str(next_cursor) if next_cursor else None
            next_cursor = _value(page, "next_cursor")
            if not next_cursor or len(posts) >= capped_limit:
                return posts, str(next_cursor) if next_cursor else None
            self._wait_for_rate_limit()
            page = await page.next()

    def _run_async(self, awaitable: Awaitable[ProviderResult]) -> ProviderResult:
        try:
            return asyncio.run(awaitable)
        except RuntimeError as exc:
            if "asyncio.run()" in str(exc):
                loop = asyncio.new_event_loop()
                try:
                    return loop.run_until_complete(awaitable)
                finally:
                    loop.close()
            raise

    def _map_exception(self, exc: Exception) -> ProviderResult:
        if isinstance(exc, _ProviderFailure):
            return ProviderResult.unavailable(
                provider=self.name,
                reason=exc.reason,
                warnings=[exc.reason],
            )
        class_name = exc.__class__.__name__
        if class_name == "TooManyRequests":
            seconds = _cooldown_seconds_from_exception(exc)
            self._activate_cooldown("rate_limited", seconds=seconds)
            return ProviderResult.unavailable(provider=self.name, reason="rate_limited", warnings=[class_name])
        if class_name in {"Unauthorized", "Forbidden"}:
            self._last_session_error = "session_invalid"
            self._activate_cooldown("session_invalid", seconds=60)
            return ProviderResult.unavailable(provider=self.name, reason="session_invalid", warnings=[class_name])
        if class_name == "NotFound":
            return ProviderResult.empty(provider=self.name)
        self._last_session_error = class_name
        return ProviderResult.error(provider=self.name, reason="api_error", warnings=[f"{class_name}: {exc}"])


class _ProviderFailure(RuntimeError):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _cooldown_seconds_from_exception(exc: Exception) -> int | None:
    reset = getattr(exc, "rate_limit_reset", None)
    if reset is None:
        return None
    try:
        remaining = int(reset) - int(time.time())
    except (TypeError, ValueError):
        return None
    return max(1, remaining)


def _load_cookie_expiry_status(cookies_path: Path, *, warning_days: int) -> dict[str, Any]:
    meta_path = cookies_path.with_suffix(".meta.json")
    status = {
        "meta_file": str(meta_path),
        "cookie_expiry": None,
        "warnings": [],
    }
    if not meta_path.exists():
        return status
    try:
        payload = json.loads(meta_path.read_text(encoding="utf-8"))
    except Exception:
        status["warnings"] = ["cookie_meta_unreadable"]
        return status

    cookies = payload.get("cookies")
    if not isinstance(cookies, dict):
        status["warnings"] = ["cookie_meta_invalid"]
        return status

    expiry: dict[str, Any] = {}
    warnings = list(payload.get("warnings") or [])
    for name in ("auth_token", "ct0"):
        cookie = cookies.get(name)
        if not isinstance(cookie, dict):
            continue
        expiry[name] = {
            "present": cookie.get("present"),
            "expires_at": cookie.get("expires_at"),
            "days_remaining": cookie.get("days_remaining"),
        }
        days = cookie.get("days_remaining")
        if isinstance(days, (int, float)) and days <= warning_days:
            warnings.append(f"{name}_expiring_soon")

    derived_at = payload.get("derived_at")
    if derived_at:
        expiry["derived_at"] = derived_at
    status["cookie_expiry"] = expiry or None
    status["warnings"] = sorted(set(str(item) for item in warnings))
    return status
