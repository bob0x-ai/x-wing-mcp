#!/usr/bin/env python3
"""x-wing owned-account analytics collector.

Fetches per-post metrics (impressions, engagements, profile clicks, URL
clicks, ...) for the authenticated account's recent posts plus an account
follower snapshot, and stores them in x-wing's own SQLite database
(``data/x-wing-analytics.db`` by default).

Designed to run once per UTC day from an OPERATOR-INSTALLED SYSTEM CRON JOB.
Not from Hermes cron, not from any agent harness scheduler. See README
§Analytics collector.

Cost discipline: one run costs exactly 2 X API calls (one
``GET /2/users/me``, one ``GET /2/users/:id/tweets`` page of up to 100
posts). The real call count is recorded in ``collection_runs.api_call_count``
and every HTTP request also lands in the x_usage ledger via the existing
response hook.

Auth: reuses x_client's canonical OAuth path (ensure_access_token /
run_auth_operation). No separate credential handling.

Idempotency: the UTC day is claimed atomically in ``collection_runs``
BEFORE any network access; concurrent or repeated runs exit as no-ops.

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

POST_FIELDS = [
    "created_at",
    "author_id",
    "conversation_id",
    "text",
    "public_metrics",
    "non_public_metrics",
    "organic_metrics",
]
USER_FIELDS = ["public_metrics"]
DEFAULT_MAX_PAGES = 1  # 1 page = up to 100 posts = 1 paid call


class ApiCallCounter:
    """Counts real X API HTTP responses on an xdk client session."""

    def __init__(self) -> None:
        self.count = 0

    def attach(self, client: Any) -> None:
        session = getattr(client, "session", None)
        if session is None:
            return
        marker = "_x_wing_analytics_counter"
        if getattr(session, marker, None) is self:
            return

        def count_response(response: Any, *args: Any, **kwargs: Any) -> Any:
            self.count += 1
            return response

        session.hooks.setdefault("response", []).append(count_response)
        setattr(session, marker, self)


def _value(obj: Any, key: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _to_dict(obj: Any) -> dict[str, Any]:
    if isinstance(obj, dict):
        return obj
    if hasattr(obj, "model_dump"):
        return obj.model_dump()
    if hasattr(obj, "__dict__"):
        return {k: v for k, v in obj.__dict__.items() if not k.startswith("_")}
    return {}


def _metrics_section(raw: dict[str, Any], section: str) -> dict[str, Any]:
    value = raw.get(section)
    return value if isinstance(value, dict) else {}


def _int(value: Any) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _post_metrics_row(post: Any) -> Optional[dict[str, Any]]:
    """Map one API post object to a post_metrics row (raw JSON preserved)."""
    raw = _to_dict(post)
    post_id = str(raw.get("id") or "").strip()
    if not post_id:
        return None
    public = _metrics_section(raw, "public_metrics")
    non_public = _metrics_section(raw, "non_public_metrics")
    organic = _metrics_section(raw, "organic_metrics")
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
        "raw": raw,
    }


def fetch_analytics(
    client: Any, *, max_pages: int = DEFAULT_MAX_PAGES
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Fetch follower snapshot + recent-post metrics. Caller counts calls."""
    me = client.users.get_me(user_fields=USER_FIELDS)
    me_raw = _to_dict(_value(me, "data", {}) or {})
    user_id = str(me_raw.get("id") or "").strip()
    if not user_id:
        raise RuntimeError("users/me returned no user id")
    me_public = _metrics_section(me_raw, "public_metrics")
    snapshot = {
        "followers_count": _int(me_public.get("followers_count")),
        "following_count": _int(me_public.get("following_count")),
        "raw": me_raw,
    }

    pages = client.users.get_posts(
        id=user_id,
        max_results=100,
        exclude=["retweets"],
        post_fields=POST_FIELDS,
    )
    posts: list[dict[str, Any]] = []
    for page_index, page in enumerate(pages):
        if page_index >= max_pages:
            break
        data = _value(page, "data", None)
        if not data:
            continue
        items = data if isinstance(data, list) else [data]
        for item in items:
            row = _post_metrics_row(item)
            if row:
                posts.append(row)
    return snapshot, posts


def run(
    *,
    collection_date: Optional[str] = None,
    db_path: Optional[Path] = None,
    force: bool = False,
    max_pages: Optional[int] = None,
    fetch_client_factory: Optional[Callable[[ApiCallCounter], Any]] = None,
) -> dict[str, Any]:
    """Run one collection. Returns a result dict for the CLI to report.

    ``fetch_client_factory`` is a test seam: given the shared ApiCallCounter
    it must return an authenticated xdk-compatible client. Production uses
    x_client.run_auth_operation, so tests inject a mock without any network.
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
            if fetch_client_factory is not None:
                client = fetch_client_factory(counter)
                snapshot, posts = fetch_analytics(client, max_pages=pages)
            else:
                def operation(client: Any) -> tuple[dict, list[dict]]:
                    counter.attach(client)
                    return fetch_analytics(client, max_pages=pages)

                snapshot, posts = x_client.run_auth_operation(
                    operation,
                    required_scopes={"tweet.read", "users.read"},
                    action_name="analytics collection",
                )

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
