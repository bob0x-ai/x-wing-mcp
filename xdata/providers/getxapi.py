"""Read-only provider for GetXAPI article reads by wrapper tweet ID."""

from __future__ import annotations

import json
import os
import random
import time
from collections.abc import Callable
from dataclasses import dataclass
from http.client import IncompleteRead
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
import requests

from xdata.contracts import Article, ArticleBlock, ArticleImage, CostEstimate, Metrics, ProviderResult, UserRef
from xdata.providers.base import CooldownMixin, RateLimiterMixin
from xdata.providers.syndication import extract_post_id

PROVIDER_NAME = "getxapi"
API_BASE_URL = "https://api.getxapi.com/twitter"
GET_ARTICLE_PATH = "/article/get"
ARTICLE_COST_USD = 0.001
DEFAULT_TIMEOUT_SECONDS = 20
DEFAULT_COOLDOWN_SECONDS = 60
DEFAULT_REQUESTS_PER_MINUTE = 20
DEFAULT_JITTER_SECONDS = 0.2
USER_AGENT = "x-mcp/0.1 (+https://github.com/local/x-mcp)"


@dataclass(frozen=True)
class HttpResponse:
    status_code: int
    text: str
    headers: dict[str, str] | None = None

    def json(self) -> Any:
        return json.loads(self.text)


HttpGet = Callable[[str, dict[str, str], int], HttpResponse]
HTTP_RETRY_ATTEMPTS = 3


def default_http_get(
    url: str,
    headers: dict[str, str],
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
) -> HttpResponse:
    try:
        response = requests.get(
            url,
            headers={"User-Agent": USER_AGENT, **headers},
            timeout=timeout,
        )
        return HttpResponse(
            status_code=response.status_code,
            text=response.text,
            headers=dict(response.headers),
        )
    except requests.RequestException:
        pass

    request = Request(url, headers={"User-Agent": USER_AGENT, **headers})
    last_incomplete: IncompleteRead | None = None
    for _ in range(HTTP_RETRY_ATTEMPTS):
        try:
            with urlopen(request, timeout=timeout) as response:
                raw_body = response.read()
                body = raw_body.decode("utf-8", errors="replace")
                return HttpResponse(
                    status_code=getattr(response, "status", 200),
                    text=body,
                    headers=dict(response.headers.items()),
                )
        except IncompleteRead as exc:
            last_incomplete = exc
            continue
        except HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            return HttpResponse(status_code=exc.code, text=body, headers=dict(exc.headers.items()))
        except URLError as exc:
            raise RuntimeError(str(exc)) from exc
    if last_incomplete is not None:
        raise RuntimeError(f"IncompleteRead({len(last_incomplete.partial)} bytes read)") from last_incomplete
    raise RuntimeError("unexpected_http_read_failure")


def _metrics_from_article(payload: dict[str, Any]) -> Metrics | None:
    metrics = Metrics(
        replies=_maybe_int(payload.get("replyCount")),
        reposts=None,
        likes=_maybe_int(payload.get("likeCount")),
        quotes=_maybe_int(payload.get("quoteCount")),
        views=_maybe_int(payload.get("viewCount")),
    )
    if all(value is None for value in metrics.__dict__.values()):
        return None
    return metrics


def _author_from_article(payload: dict[str, Any]) -> UserRef | None:
    author = payload.get("author")
    if not isinstance(author, dict):
        return None
    return UserRef(
        id=str(author.get("id") or "").strip() or None,
        username=str(author.get("userName") or "").strip() or None,
        name=str(author.get("name") or "").strip() or None,
    )


def _article_from_payload(payload: dict[str, Any], *, wrapper_tweet_id: str) -> Article | None:
    article_id = str(payload.get("id") or "").strip()
    title = str(payload.get("title") or "").strip()
    if not article_id or not title:
        return None
    blocks: list[ArticleBlock] = []
    images: list[ArticleImage] = []
    body_parts: list[str] = []
    for raw_block in payload.get("contents") or []:
        if not isinstance(raw_block, dict):
            continue
        block_type = str(raw_block.get("type") or "unstyled").strip() or "unstyled"
        text = str(raw_block.get("text") or "").strip() or None
        url = str(raw_block.get("url") or "").strip() or None
        width = _maybe_int(raw_block.get("width"))
        height = _maybe_int(raw_block.get("height"))
        style_ranges = list(raw_block.get("inlineStyleRanges") or [])
        blocks.append(
            ArticleBlock(
                type=block_type,
                text=text,
                url=url,
                width=width,
                height=height,
                inline_style_ranges=[item for item in style_ranges if isinstance(item, dict)],
            )
        )
        if block_type == "image" and url:
            images.append(ArticleImage(url=url, width=width, height=height))
            body_parts.append(f"[Image: {url}]")
        elif block_type == "divider":
            body_parts.append("---")
        elif text:
            body_parts.append(text)
    author = _author_from_article(payload)
    username = author.username if author else None
    source_url = (
        f"https://x.com/{username}/status/{wrapper_tweet_id}"
        if username
        else f"https://x.com/i/web/status/{wrapper_tweet_id}"
    )
    body_text = "\n\n".join(part for part in body_parts if part).strip()
    if not body_text:
        body_text = str(payload.get("preview_text") or "").strip()
    return Article(
        id=article_id,
        title=title,
        body_text=body_text,
        author=author,
        created_at=str(payload.get("createdAt") or "").strip() or None,
        metrics=_metrics_from_article(payload),
        preview_text=str(payload.get("preview_text") or "").strip() or None,
        cover_image_url=str(payload.get("cover_media_img_url") or "").strip() or None,
        source_url=source_url,
        images=images,
        blocks=blocks,
        raw=payload,
    )


def _maybe_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


class GetXApiProvider(CooldownMixin, RateLimiterMixin):
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
        return os.getenv("GETXAPI_API_KEY")

    def status(self) -> dict[str, Any]:
        return {
            "auth_required": True,
            "auth_present": bool(self._api_key()),
            "read_only": True,
            "supports_tasks": ["read_article"],
            "limitations": [
                "published_articles_only_in_v1",
                "wrapper_tweet_id_only_in_v1",
                "direct_article_url_resolution_not_implemented",
            ],
            **self._cooldown_status(),
            **self._rate_limit_status(),
        }

    def estimate_cost(self, task: str, **kwargs: Any) -> CostEstimate | None:
        del kwargs
        if task == "read_article":
            return CostEstimate(amount_usd=ARTICLE_COST_USD, basis="$0.001 per GetXAPI article read")
        return None

    def read_article(self, value: str) -> ProviderResult:
        blocked = self._cooldown_unavailable(self.name)
        if blocked:
            return blocked
        value = str(value or "").strip()
        if not value:
            return ProviderResult.error(provider=self.name, reason="missing_value")
        if "/i/article/" in value:
            return ProviderResult.error(
                provider=self.name,
                reason="article_url_requires_share_link",
                warnings=["use the article share link or wrapper tweet ID for public reads"],
            )
        wrapper_tweet_id = extract_post_id(value)
        if not wrapper_tweet_id:
            return ProviderResult.error(provider=self.name, reason="invalid_article_reference")
        payload, failure = self._request_json(GET_ARTICLE_PATH, params={"id": wrapper_tweet_id})
        if failure:
            return failure
        assert isinstance(payload, dict)
        article_payload = payload.get("article")
        if not isinstance(article_payload, dict):
            return ProviderResult.error(provider=self.name, reason="unexpected_payload")
        article = _article_from_payload(article_payload, wrapper_tweet_id=wrapper_tweet_id)
        if article is None:
            return ProviderResult.error(provider=self.name, reason="unparseable_article")
        return ProviderResult.ok(
            provider=self.name,
            items=[article],
            cost=CostEstimate(amount_usd=ARTICLE_COST_USD, basis="$0.001 per GetXAPI article read"),
            metadata={"wrapper_tweet_id": wrapper_tweet_id, "lookup_mode": "wrapper_tweet_id"},
        )

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
                warnings=["missing GETXAPI_API_KEY"],
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
            return None, ProviderResult.error(provider=self.name, reason="transport_error", warnings=[str(exc)])
        payload: dict[str, Any] | None = None
        message: str | None = None
        if response.text.strip():
            try:
                parsed = response.json()
            except json.JSONDecodeError:
                if 200 <= response.status_code < 300:
                    return None, ProviderResult.error(provider=self.name, reason="invalid_json")
            else:
                if isinstance(parsed, dict):
                    payload = parsed
                    message = str(parsed.get("error") or parsed.get("msg") or "").strip() or None
                elif 200 <= response.status_code < 300:
                    return None, ProviderResult.error(provider=self.name, reason="unexpected_payload")
        if 200 <= response.status_code < 300:
            if payload is None:
                return None, ProviderResult.error(provider=self.name, reason="unexpected_payload")
            return payload, None
        warnings = [message] if message else []
        if response.status_code == 401:
            return None, ProviderResult.unavailable(provider=self.name, reason="invalid_api_key", warnings=warnings)
        if response.status_code == 402:
            return None, ProviderResult.unavailable(provider=self.name, reason="insufficient_balance", warnings=warnings)
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
        return None, ProviderResult.error(provider=self.name, reason=f"http_{response.status_code}", warnings=warnings)


def _build_url(path: str, params: dict[str, Any] | None = None) -> str:
    url = f"{API_BASE_URL}{path}"
    if not params:
        return url
    encoded = urlencode({key: value for key, value in params.items() if value is not None}, doseq=True)
    return f"{url}?{encoded}" if encoded else url
