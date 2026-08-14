"""Hardened read-only client for the local x-analytics service."""

from __future__ import annotations

import json
import os
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener

import yaml


REPO_ROOT = Path(__file__).resolve().parent
DEFAULT_CONFIG_PATH = REPO_ROOT / "config" / "analytics.yaml"
MAX_RESPONSE_BYTES = 1_000_000


class AnalyticsServiceError(RuntimeError):
    """The local analytics service is unavailable or returned invalid data."""


class AnalyticsServiceConfigurationError(AnalyticsServiceError):
    """The local analytics service is disabled, missing, or unsafe."""


class AnalyticsServiceUnavailable(AnalyticsServiceError):
    """The configured local service could not be reached safely."""


class AnalyticsServiceProtocolError(AnalyticsServiceError):
    """The configured service returned an invalid response."""


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        return None


def _configuration(config_path: Path | None = None) -> tuple[str, float]:
    path = config_path or Path(os.getenv("X_ANALYTICS_CONFIG_FILE", DEFAULT_CONFIG_PATH))
    if not path.is_file():
        raise AnalyticsServiceConfigurationError(
            f"x-analytics is not configured: missing {path}. Configure analytics_service.base_url."
        )
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        config = raw.get("analytics_service", {})
    except (OSError, yaml.YAMLError) as exc:
        raise AnalyticsServiceConfigurationError("x-analytics configuration is unreadable") from exc
    if not isinstance(config, dict) or not config.get("enabled", False):
        raise AnalyticsServiceConfigurationError("x-analytics is not configured or is disabled in analytics.yaml")
    base_url = str(config.get("base_url") or "").rstrip("/")
    parsed = urlparse(base_url)
    if (
        parsed.scheme != "http"
        or parsed.hostname not in {"127.0.0.1", "::1"}
        or not parsed.port
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
    ):
        raise AnalyticsServiceConfigurationError(
            "x-analytics base_url must be an http loopback origin such as http://127.0.0.1:9684"
        )
    try:
        timeout = float(config.get("timeout_seconds", 3))
    except (TypeError, ValueError) as exc:
        raise AnalyticsServiceConfigurationError("x-analytics timeout_seconds must be numeric") from exc
    return base_url, max(0.1, min(timeout, 10.0))


def read_analytics(
    *,
    view: str = "overview",
    window_days: int = 28,
    post_id: str | None = None,
    sort: str | None = None,
    limit: int | None = None,
    days: int | None = None,
    config_path: Path | None = None,
) -> dict:
    """Read one safe, compact analytics projection from the local service.

    ``days`` is a compatibility alias for older MCP callers; new callers use
    ``window_days``. This client deliberately has no write or collection path.
    """
    try:
        window_days = max(1, min(int(days if days is not None else window_days), 30))
    except (TypeError, ValueError):
        window_days = 28
    views = {"status", "overview", "posts", "post_history", "followers"}
    if view not in views:
        raise AnalyticsServiceConfigurationError("analytics view must be status, overview, posts, post_history, or followers")
    if view == "post_history":
        if not post_id or not post_id.isdigit():
            raise AnalyticsServiceConfigurationError("post_history requires a numeric post_id")
        path = f"/v1/analytics/posts/{post_id}/history"
    else:
        path = {
            "status": "/v1/analytics/status",
            "overview": "/v1/analytics/overview",
            "posts": "/v1/analytics/posts",
            "followers": "/v1/analytics/followers",
        }[view]
    params: dict[str, int | str] = {}
    if view != "status":
        params["window"] = f"{window_days}d"
    if view == "posts":
        if sort:
            params["sort"] = sort
        if limit is not None:
            params["limit"] = max(1, min(int(limit), 100))
    base_url, timeout = _configuration(config_path)
    token = os.getenv("X_ANALYTICS_API_TOKEN", "").strip()
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = Request(
        f"{base_url}{path}" + (f"?{urlencode(params)}" if params else ""),
        headers=headers,
        method="GET",
    )
    try:
        with build_opener(_NoRedirect()).open(request, timeout=timeout) as response:
            content_type = response.headers.get("Content-Type", "")
            if not content_type.startswith("application/json"):
                raise AnalyticsServiceProtocolError("x-analytics returned a non-JSON response")
            body = response.read(MAX_RESPONSE_BYTES + 1)
            if len(body) > MAX_RESPONSE_BYTES:
                raise AnalyticsServiceProtocolError("x-analytics response exceeded the safe size limit")
            payload = json.loads(body.decode("utf-8"))
            if not isinstance(payload, dict):
                raise AnalyticsServiceProtocolError("x-analytics returned an invalid JSON object")
            return payload
    except HTTPError as exc:
        if exc.code in {401, 403}:
            raise AnalyticsServiceUnavailable("x-analytics rejected x-wing authentication; check its local service token") from exc
        raise AnalyticsServiceUnavailable(f"x-analytics returned HTTP {exc.code}") from exc
    except (URLError, TimeoutError, OSError) as exc:
        raise AnalyticsServiceUnavailable("x-analytics is unreachable; start the local service or correct analytics.yaml") from exc
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AnalyticsServiceProtocolError("x-analytics returned malformed JSON") from exc
