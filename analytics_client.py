"""Read-only client for the local x-analytics service.

This module intentionally has no X OAuth or database access. The analytics
service owns collection and persistence; x-wing only exposes its stored data
to MCP clients.
"""

from __future__ import annotations

import json
import os
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


DEFAULT_BASE_URL = "http://127.0.0.1:9684"


class AnalyticsServiceError(RuntimeError):
    """The local analytics service is unavailable or returned invalid data."""


def read_analytics(*, days: int = 28) -> dict:
    try:
        days = max(1, min(int(days), 30))
    except (TypeError, ValueError):
        days = 28
    base_url = os.getenv("X_ANALYTICS_URL", DEFAULT_BASE_URL).rstrip("/")
    token = os.getenv("X_ANALYTICS_API_TOKEN", "").strip()
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = Request(
        f"{base_url}/v1/analytics/posts?{urlencode({'days': days})}",
        headers=headers,
        method="GET",
    )
    try:
        with urlopen(request, timeout=5) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        raise AnalyticsServiceError(f"x-analytics returned HTTP {exc.code}") from exc
    except (URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise AnalyticsServiceError(f"x-analytics unavailable: {exc}") from exc
