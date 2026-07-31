"""Read-only provider for X syndication/embed endpoints."""

from __future__ import annotations

import json
import random
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from html import unescape
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from xdata.contracts import CostEstimate, Metrics, Post, ProviderResult, UserRef
from xdata.providers.base import CooldownMixin, RateLimiterMixin

PROVIDER_NAME = "syndication"
TWEET_RESULT_URL = "https://cdn.syndication.twimg.com/tweet-result?id={id}&token=a"
TIMELINE_PROFILE_URL = "https://syndication.twitter.com/srv/timeline-profile/screen-name/{handle}"
DEFAULT_TIMEOUT_SECONDS = 10
DEFAULT_COOLDOWN_SECONDS = 60
DEFAULT_REQUESTS_PER_MINUTE = 12
DEFAULT_JITTER_SECONDS = 0.35
USER_AGENT = "x-mcp/0.1 (+https://github.com/local/x-mcp)"

_STATUS_RE = re.compile(
    r"https?://(?:mobile\.)?(?:x|twitter)\.com/(?:(?:[^/\s]+/status(?:es)?)|(?:i/web/status))/(?P<id>\d+)",
    re.IGNORECASE,
)
_DIGITS_RE = re.compile(r"^\d{5,}$")


@dataclass(frozen=True)
class HttpResponse:
    status_code: int
    text: str
    headers: dict[str, str] | None = None

    def json(self) -> Any:
        return json.loads(self.text)


HttpGet = Callable[[str, int], HttpResponse]


def default_http_get(url: str, timeout: int = DEFAULT_TIMEOUT_SECONDS) -> HttpResponse:
    request = Request(url, headers={"User-Agent": USER_AGENT})
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


def extract_post_id(value: str) -> str | None:
    value = str(value or "").strip()
    if _DIGITS_RE.fullmatch(value):
        return value
    match = _STATUS_RE.search(value)
    if match:
        return match.group("id")
    return None


def normalize_handle(value: str) -> str:
    normalized = str(value or "").strip().lstrip("@")
    if normalized.startswith("http://") or normalized.startswith("https://"):
        return ""
    return normalized


def _int_or_none(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _metrics_from_payload(payload: dict[str, Any]) -> Metrics | None:
    public_metrics = payload.get("public_metrics")
    if isinstance(public_metrics, dict):
        metrics = Metrics(
            replies=_int_or_none(public_metrics.get("reply_count")),
            reposts=_int_or_none(public_metrics.get("retweet_count")),
            likes=_int_or_none(public_metrics.get("like_count")),
            quotes=_int_or_none(public_metrics.get("quote_count")),
            views=_int_or_none(public_metrics.get("impression_count")),
        )
    else:
        metrics = Metrics(
            replies=_int_or_none(payload.get("reply_count") or payload.get("conversation_count")),
            reposts=_int_or_none(payload.get("retweet_count")),
            likes=_int_or_none(payload.get("favorite_count") or payload.get("like_count")),
            quotes=_int_or_none(payload.get("quote_count")),
            views=_int_or_none(payload.get("view_count")),
        )
    if all(value is None for value in metrics.__dict__.values()):
        return None
    return metrics


def _author_from_payload(payload: dict[str, Any]) -> UserRef | None:
    user = payload.get("user")
    if isinstance(user, dict):
        return UserRef(
            id=str(user.get("id_str") or user.get("id") or "") or None,
            username=user.get("screen_name") or user.get("username"),
            name=user.get("name"),
        )
    author_id = payload.get("author_id")
    if author_id:
        return UserRef(id=str(author_id))
    return None


def post_from_payload(payload: dict[str, Any]) -> Post | None:
    post_id = str(payload.get("id_str") or payload.get("id") or "").strip()
    text = str(payload.get("text") or payload.get("full_text") or "").strip()
    if not post_id or not text:
        return None
    author = _author_from_payload(payload)
    username = author.username if author else None
    source_url = f"https://x.com/{username}/status/{post_id}" if username else f"https://x.com/i/web/status/{post_id}"
    return Post(
        id=post_id,
        text=text,
        author=author,
        created_at=payload.get("created_at"),
        metrics=_metrics_from_payload(payload),
        source_url=source_url,
        raw=payload,
    )


def _iter_json_objects_from_html(html: str) -> list[dict[str, Any]]:
    """Extract plausible tweet objects from syndication timeline HTML.

    The endpoint embeds JSON in HTML/JS and has changed shape over time. This
    parser intentionally accepts a few common shapes instead of relying on one
    brittle DOM selector.
    """
    candidates: list[dict[str, Any]] = []
    decoded = unescape(html)

    for script_json in re.findall(
        r'<script[^>]+type=["\']application/json["\'][^>]*>(.*?)</script>',
        decoded,
        flags=re.IGNORECASE | re.DOTALL,
    ):
        try:
            parsed = json.loads(script_json.strip())
        except json.JSONDecodeError:
            continue
        candidates.extend(_walk_for_tweet_dicts(parsed))

    for match in re.finditer(r'\{[^{}]*"(?:id_str|text|full_text)"[^{}]*\}', decoded):
        try:
            parsed = json.loads(match.group(0))
        except json.JSONDecodeError:
            continue
        candidates.extend(_walk_for_tweet_dicts(parsed))

    return candidates


def _walk_for_tweet_dicts(value: Any) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    if isinstance(value, dict):
        if ("id_str" in value or "id" in value) and ("text" in value or "full_text" in value):
            found.append(value)
        for child in value.values():
            found.extend(_walk_for_tweet_dicts(child))
    elif isinstance(value, list):
        for child in value:
            found.extend(_walk_for_tweet_dicts(child))
    return found


class SyndicationProvider(CooldownMixin, RateLimiterMixin):
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

    def status(self) -> dict[str, Any]:
        return {
            "auth_required": False,
            "configured": True,
            "endpoints": ["tweet_result", "timeline_profile"],
            "supports_tasks": ["fetch_urls", "read_user_posts_recent"],
            "limitations": [
                "no_search",
                "no_thread_enumeration",
                "no_quotes_or_graph",
                "timeline_parse_is_best_effort",
            ],
            **self._cooldown_status(),
            **self._rate_limit_status(),
        }

    def estimate_cost(self, task: str, **kwargs: Any) -> CostEstimate | None:
        del kwargs
        if task in {"fetch_urls", "read_user_posts_recent"}:
            return CostEstimate(amount_usd=0.0, basis="free syndication endpoint")
        return None

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
            url = TWEET_RESULT_URL.format(id=quote(post_id))
            self._wait_for_rate_limit()
            try:
                response = self._http_get(url, self._timeout_seconds)
            except Exception as exc:
                return ProviderResult.error(
                    provider=self.name,
                    reason="transport_error",
                    warnings=[str(exc), *warnings],
                )
            if response.status_code in {403, 404, 410, 451}:
                warnings.append(f"post_unavailable:{post_id}")
                continue
            if response.status_code == 429:
                self._activate_cooldown("rate_limited")
                return ProviderResult.unavailable(
                    provider=self.name,
                    reason="rate_limited",
                    warnings=warnings,
                )
            if response.status_code >= 400:
                return ProviderResult.error(
                    provider=self.name,
                    reason=f"http_{response.status_code}",
                    warnings=warnings,
                )
            try:
                payload = response.json()
            except json.JSONDecodeError:
                return ProviderResult.error(
                    provider=self.name,
                    reason="invalid_json",
                    warnings=warnings,
                )
            if not isinstance(payload, dict):
                warnings.append(f"unexpected_payload:{post_id}")
                continue
            post = post_from_payload(payload)
            if post:
                posts.append(post)
            else:
                warnings.append(f"unparseable_post:{post_id}")

        if posts:
            return ProviderResult.ok(provider=self.name, items=posts, warnings=warnings)
        if warnings:
            return ProviderResult.unavailable(
                provider=self.name,
                reason="no_fetchable_posts",
                warnings=warnings,
            )
        return ProviderResult.empty(provider=self.name)

    def read_user_posts(self, user: str, *, limit: int = 20) -> ProviderResult:
        blocked = self._cooldown_unavailable(self.name)
        if blocked:
            return blocked
        handle = normalize_handle(user)
        if not handle:
            return ProviderResult.error(provider=self.name, reason="missing_handle")
        capped_limit = max(1, min(int(limit), 20))
        url = TIMELINE_PROFILE_URL.format(handle=quote(handle))
        self._wait_for_rate_limit()
        try:
            response = self._http_get(url, self._timeout_seconds)
        except Exception as exc:
            return ProviderResult.error(
                provider=self.name,
                reason="transport_error",
                warnings=[str(exc)],
            )
        if response.status_code in {403, 404, 410, 451}:
            return ProviderResult.unavailable(
                provider=self.name,
                reason="timeline_unavailable",
                warnings=[f"http_{response.status_code}"],
            )
        if response.status_code == 429:
            self._activate_cooldown("rate_limited")
            return ProviderResult.unavailable(
                provider=self.name,
                reason="rate_limited",
                warnings=["http_429"],
            )
        if response.status_code >= 400:
            return ProviderResult.error(provider=self.name, reason=f"http_{response.status_code}")

        tweet_dicts = _iter_json_objects_from_html(response.text)
        posts: list[Post] = []
        seen: set[str] = set()
        for payload in tweet_dicts:
            post = post_from_payload(payload)
            if not post or post.id in seen:
                continue
            seen.add(post.id)
            posts.append(post)
            if len(posts) >= capped_limit:
                break
        if posts:
            return ProviderResult.ok(provider=self.name, items=posts)
        return ProviderResult.empty(provider=self.name, reason="timeline_no_parseable_posts")
