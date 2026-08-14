#!/usr/bin/env python3
"""x-wing owned-account analytics collector.

Fetches per-post metrics (impressions, engagements, profile clicks, URL
clicks, ...) for the authenticated account's recent posts plus an account
follower snapshot, and stores them in x-wing's own SQLite database
(``data/x-wing-analytics.db`` by default).

Designed to run once per UTC day from an OPERATOR-INSTALLED SYSTEM CRON JOB.
Not from Hermes cron, not from any agent harness scheduler. See README
§Analytics collector.

Cost discipline: one run costs exactly 2 X API calls (one free
``GET /2/users/me``, one paid ``GET /2/users/:id/tweets`` page of up to 100
posts). The real call count is recorded in ``collection_runs.api_call_count``
and every request also lands in the x_usage ledger.

Auth: reuses x_client's canonical OAuth path (ensure_access_token /
_refresh_from_env) for token storage and refresh — no separate credential
handling. HTTP is plain ``requests`` against api.x.com (the same pattern as
``x_client._validate_access_token``) because the vendored xdk response
models have rotted against X's current payload shape (missing
``public_metrics.post_count`` on users/me, 2026-08-14).

Idempotency: the UTC day is claimed atomically in ``collection_runs``
BEFORE any network access; concurrent or repeated runs exit as no-ops.
A run that ended in 'error' may be reclaimed by the next run (nothing was
stored, so retrying is safe and prevents data gaps).

Never prints secrets. stdout is a short operator log (this script is not
the MCP server, so stdout printing is safe here).
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

REPO_ROOT = Path(__file__).resolve().parent
os.environ.setdefault("X_WING_ENV_PATH", str(REPO_ROOT / ".env"))

from dotenv import load_dotenv

load_dotenv(REPO_ROOT / ".env", override=True)

import analytics_store
import x_client
import x_usage_ledger

API_BASE = "https://api.x.com"
USER_ME_URL = f"{API_BASE}/2/users/me"
USER_TWEETS_URL = f"{API_BASE}/2/users/{{user_id}}/tweets"

TWEET_FIELDS = "created_at,author_id,conversation_id,public_metrics,non_public_metrics,organic_metrics"
USER_FIELDS = "public_metrics"
DEFAULT_MAX_PAGES = 1  # 1 page = up to 100 posts = 1 paid call
PAGE_SIZE = 100


class ApiCallCounter:
    """Counts real X API HTTP requests issued by the collector."""

    def __init__(self) -> None:
        self.count = 0


class XApiFetcher:
    """Minimal user-context GET fetcher on x_client's canonical auth path.

    Retries once after a forced token refresh on 401, mirroring
    x_client.run_auth_operation's discipline for plain-requests calls.
    """

    def __init__(self, counter: ApiCallCounter) -> None:
        self.counter = counter

    def _get_once(self, url: str, params: dict[str, Any], token: str):
        import requests

        response = requests.get(
            url,
            headers={"Authorization": f"Bearer {token}"},
            params=params,
            timeout=30,
        )
        self.counter.count += 1
        x_usage_ledger.record(
            "official_x_http",
            provider="official_x",
            method="GET",
            endpoint=url,
            query_parameter_names=sorted(params),
            http_status=response.status_code,
            purpose="analytics_collection",
            x_request_id=response.headers.get("x-request-id")
            or response.headers.get("x-transaction-id"),
        )
        return response

    def get_json(self, url: str, params: dict[str, Any]) -> dict[str, Any]:
        token = x_client.ensure_access_token()
        response = self._get_once(url, params, token)
        if response.status_code == 401:
            token = x_client._refresh_from_env()
            response = self._get_once(url, params, token)
        if response.status_code != 200:
            raise RuntimeError(
                f"X API {url} returned HTTP {response.status_code}: "
                f"{response.text[:200]}"
            )
        return response.json()


def _int(value: Any) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _section(raw: dict[str, Any], key: str) -> dict[str, Any]:
    value = raw.get(key)
    return value if isinstance(value, dict) else {}


def _post_metrics_row(post: dict[str, Any]) -> Optional[dict[str, Any]]:
    """Map one API post object to a post_metrics row (raw JSON preserved)."""
    post_id = str(post.get("id") or "").strip()
    if not post_id:
        return None
    public = _section(post, "public_metrics")
    non_public = _section(post, "non_public_metrics")
    organic = _section(post, "organic_metrics")
    impressions = (
        _int(organic.get("impression_count"))
        or _int(non_public.get("impression_count"))
        or _int(public.get("impression_count"))
    )
    return {
        "post_id": post_id,
        "impressions": impressions,
        "engagements": _int(non_public.get("engagements")),
        "likes": _int(public.get("like_count")),
        "replies": _int(public.get("reply_count")),
        "reposts": _int(public.get("retweet_count")),
        "quotes": _int(public.get("quote_count")),
        "bookmarks": _int(public.get("bookmark_count")),
        "profile_clicks": _int(non_public.get("user_profile_clicks")),
        "url_clicks": _int(non_public.get("url_link_clicks")),
        "raw": post,
    }


def fetch_analytics(
    fetcher: Any, *, max_pages: int = DEFAULT_MAX_PAGES
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Fetch follower snapshot + recent-post metrics via the fetcher.

    The fetcher must expose ``get_json(url, params) -> dict``; production
    uses XApiFetcher, tests inject a mock. No SDK models involved.
    """
    me_payload = fetcher.get_json(USER_ME_URL, {"user.fields": USER_FIELDS})
    me = me_payload.get("data") or {}
    user_id = str(me.get("id") or "").strip()
    if not user_id:
        raise RuntimeError("users/me returned no user id")
    me_public = _section(me, "public_metrics")
    snapshot = {
        "followers_count": _int(me_public.get("followers_count")),
        "following_count": _int(me_public.get("following_count")),
        "raw": me,
    }

    posts: list[dict[str, Any]] = []
    pagination_token: Optional[str] = None
    for _ in range(max(1, max_pages)):
        params: dict[str, Any] = {
            "max_results": PAGE_SIZE,
            "exclude": "retweets",
            "tweet.fields": TWEET_FIELDS,
        }
        if pagination_token:
            params["pagination_token"] = pagination_token
        payload = fetcher.get_json(USER_TWEETS_URL.format(user_id=user_id), params)
        for post in payload.get("data") or []:
            row = _post_metrics_row(post)
            if row:
                posts.append(row)
        pagination_token = (payload.get("meta") or {}).get("next_token")
        if not pagination_token:
            break
    return snapshot, posts


def run(
    *,
    collection_date: Optional[str] = None,
    db_path: Optional[Path] = None,
    force: bool = False,
    max_pages: Optional[int] = None,
    fetcher_factory: Optional[Callable[[ApiCallCounter], Any]] = None,
) -> dict[str, Any]:
    """Run one collection. Returns a result dict for the CLI to report.

    ``fetcher_factory`` is a test seam: given the shared ApiCallCounter it
    must return a fetcher with ``get_json(url, params)``. Production uses
    XApiFetcher on x_client's OAuth path; tests inject a mock with no
    network access.
    """
    date = collection_date or datetime.now(timezone.utc).date().isoformat()
    pages = max_pages
    if pages is None:
        try:
            pages = int(os.getenv("X_WING_ANALYTICS_MAX_PAGES", "") or DEFAULT_MAX_PAGES)
        except ValueError:
            pages = DEFAULT_MAX_PAGES
    pages = max(1, min(pages, 10))

    conn = analytics_store.connect(db_path)
    try:
        claimed, existing = analytics_store.claim_run(conn, date, force=force)
        if not claimed:
            return {
                "status": "skipped",
                "reason": "already_claimed",
                "collection_date": date,
                "existing_run": existing,
                "api_call_count": 0,
            }

        counter = ApiCallCounter()
        try:
            fetcher = (
                fetcher_factory(counter)
                if fetcher_factory is not None
                else XApiFetcher(counter)
            )
            snapshot, posts = fetch_analytics(fetcher, max_pages=pages)

            analytics_store.upsert_account_snapshot(conn, date, snapshot)
            stored = analytics_store.upsert_post_metrics(conn, date, posts)
            analytics_store.finish_run(
                conn,
                date,
                status="ok",
                post_count=stored,
                api_call_count=counter.count,
            )
            return {
                "status": "ok",
                "collection_date": date,
                "post_count": stored,
                "followers_count": snapshot.get("followers_count"),
                "api_call_count": counter.count,
            }
        except Exception as exc:
            analytics_store.finish_run(
                conn,
                date,
                status="error",
                api_call_count=counter.count,
                error=str(exc)[:500],
            )
            raise
    finally:
        conn.close()


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Collect owned-account X analytics into the x-wing SQLite store."
    )
    parser.add_argument(
        "--date",
        help="UTC collection date YYYY-MM-DD (default: today). Testing aid.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Override an existing claim for the date (operator use only).",
    )
    parser.add_argument("--db-path", help="Override analytics DB path.")
    args = parser.parse_args(argv)

    try:
        result = run(
            collection_date=args.date,
            db_path=Path(args.db_path) if args.db_path else None,
            force=args.force,
        )
    except Exception as exc:
        print(f"analytics-collector: ERROR date={args.date or 'today'}: {exc}")
        return 1

    if result["status"] == "skipped":
        existing = result.get("existing_run") or {}
        print(
            "analytics-collector: no-op - "
            f"{result['collection_date']} already claimed "
            f"(status={existing.get('status')}, started_at={existing.get('started_at')})"
        )
        return 0

    print(
        "analytics-collector: ok "
        f"date={result['collection_date']} posts={result['post_count']} "
        f"followers={result.get('followers_count')} "
        f"api_calls={result['api_call_count']}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
