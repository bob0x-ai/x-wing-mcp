"""Common provider interface and small provider utilities."""

from __future__ import annotations

from collections.abc import Callable
import random
import time
from typing import Any, Protocol

from xdata.contracts import CostEstimate, ProviderResult


class XDataProvider(Protocol):
    name: str

    def fetch_urls(self, values: list[str]) -> ProviderResult:
        """Fetch exact post URLs or raw post IDs."""
        ...

    def read_user_posts(self, user: str, *, limit: int = 20) -> ProviderResult:
        """Read recent posts for one public user."""
        ...

    def search_posts(self, query: str, *, limit: int = 20) -> ProviderResult:
        """Search public posts."""
        ...

    def read_owned_timeline(self, *, limit: int = 20) -> ProviderResult:
        """Read the authenticated account timeline."""
        ...

    def read_mentions(self, *, limit: int = 20) -> ProviderResult:
        """Read mentions for the authenticated account."""
        ...

    def read_thread(self, value: str, *, limit: int = 100) -> ProviderResult:
        """Read a thread/conversation by URL or post ID."""
        ...

    def read_replies(self, value: str, *, limit: int = 100) -> ProviderResult:
        """Read replies for a post URL or post ID."""
        ...

    def read_quotes(self, value: str, *, limit: int = 100) -> ProviderResult:
        """Read quotes for a post URL or post ID."""
        ...

    def read_follow_graph(self, user: str, *, graph: str = "followers", limit: int = 100) -> ProviderResult:
        """Read followers or following for one user."""
        ...

    def read_article(self, value: str) -> ProviderResult:
        """Read a published X Article by wrapper tweet URL or wrapper tweet ID."""
        ...

    def collect_posts(self, query: str, *, limit: int = 100) -> ProviderResult:
        """Collect posts for monitoring/bulk workflows."""
        ...

    def estimate_cost(self, task: str, **kwargs: Any) -> CostEstimate | None:
        """Return a conservative preflight cost estimate for one task."""
        ...


class CooldownMixin:
    """Lightweight in-process cooldown state for fragile/read-limited providers."""

    def __init__(self, *, time_fn: Callable[[], float], cooldown_seconds: int) -> None:
        self._time_fn = time_fn
        self._cooldown_seconds = cooldown_seconds
        self._cooldown_until: float = 0.0
        self._cooldown_reason: str | None = None

    def _activate_cooldown(self, reason: str, *, seconds: int | None = None) -> None:
        duration = max(1, int(seconds if seconds is not None else self._cooldown_seconds))
        self._cooldown_until = self._time_fn() + duration
        self._cooldown_reason = reason

    def _cooldown_status(self) -> dict[str, Any]:
        remaining = max(0, int(self._cooldown_until - self._time_fn()))
        return {
            "cooldown_active": remaining > 0,
            "cooldown_reason": self._cooldown_reason if remaining > 0 else None,
            "cooldown_seconds_remaining": remaining,
        }

    def _cooldown_unavailable(self, provider: str) -> ProviderResult | None:
        status = self._cooldown_status()
        if not status["cooldown_active"]:
            return None
        return ProviderResult.unavailable(
            provider=provider,
            reason="cooldown_active",
            warnings=[str(status["cooldown_reason"])],
            metadata={
                "cooldown_reason": status["cooldown_reason"],
                "cooldown_seconds_remaining": status["cooldown_seconds_remaining"],
            },
        )


class RateLimiterMixin:
    """Transparent in-process pacing for polite provider usage."""

    def __init__(
        self,
        *,
        time_fn: Callable[[], float] = time.time,
        sleep_fn: Callable[[float], None] = time.sleep,
        random_fn: Callable[[], float] = random.random,
        requests_per_minute: float | None = None,
        min_interval_seconds: float | None = None,
        jitter_seconds: float = 0.0,
    ) -> None:
        derived_interval = 0.0
        if requests_per_minute and requests_per_minute > 0:
            derived_interval = 60.0 / float(requests_per_minute)
        self._time_fn = time_fn
        self._sleep_fn = sleep_fn
        self._random_fn = random_fn
        self._min_interval_seconds = max(float(min_interval_seconds or derived_interval), 0.0)
        self._jitter_seconds = max(float(jitter_seconds), 0.0)
        self._next_request_at: float = 0.0

    def _wait_for_rate_limit(self) -> None:
        if self._min_interval_seconds <= 0:
            return
        now = self._time_fn()
        if now < self._next_request_at:
            self._sleep_fn(self._next_request_at - now)
            now = self._next_request_at
        jitter = self._random_fn() * self._jitter_seconds if self._jitter_seconds > 0 else 0.0
        if jitter > 0:
            self._sleep_fn(jitter)
        self._next_request_at = now + jitter + self._min_interval_seconds

    def _rate_limit_status(self) -> dict[str, Any]:
        return {
            "local_rate_limit": {
                "enabled": self._min_interval_seconds > 0,
                "min_interval_seconds": self._min_interval_seconds,
                "jitter_seconds": self._jitter_seconds,
            }
        }
