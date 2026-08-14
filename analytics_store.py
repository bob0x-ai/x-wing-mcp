"""SQLite storage layer for x-wing owned-account analytics.

Owns the analytics schema, the claim-before-fetch idempotency protocol for
the daily collector, and the read-only query path used by the MCP tool.

Discipline (repo AGENTS.md): state lives inside the repo, files are 0600,
directories 0700. No credentials are ever stored here — metrics and raw
post JSON only.

Idempotency invariant: N concurrent collector processes -> exactly one
fetch+store per UTC calendar day. Enforced by ``collection_runs`` PRIMARY
KEY(collection_date) plus an atomic ``INSERT OR IGNORE`` claim inside a
``BEGIN IMMEDIATE`` transaction *before* any network access. A second
claimant observes the existing row and exits without fetching.
"""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

REPO_ROOT = Path(__file__).resolve().parent
DEFAULT_DB_PATH = REPO_ROOT / "data" / "x-wing-analytics.db"

# A claimed run still 'running' after this long is treated as crashed and
# may be reclaimed by a later process on the same UTC day.
STALE_CLAIM_SECONDS = 3600
# The MCP read tool warns when the newest successful run is older than this.
STALE_DATA_HOURS = 36

STALE_WARNING = (
    "analytics store empty/stale - the operator must install the system cron "
    "job (see README \u00a7Analytics collector)"
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS post_metrics (
    post_id        TEXT NOT NULL,
    collected_date TEXT NOT NULL,
    impressions    INTEGER,
    engagements    INTEGER,
    likes          INTEGER,
    replies        INTEGER,
    reposts        INTEGER,
    quotes         INTEGER,
    bookmarks      INTEGER,
    profile_clicks INTEGER,
    url_clicks     INTEGER,
    raw_json       TEXT,
    PRIMARY KEY (post_id, collected_date)
);
CREATE TABLE IF NOT EXISTS account_snapshots (
    snapshot_date   TEXT PRIMARY KEY,
    followers_count INTEGER,
    following_count INTEGER,
    raw_json        TEXT
);
CREATE TABLE IF NOT EXISTS collection_runs (
    collection_date TEXT PRIMARY KEY,
    started_at      TEXT,
    finished_at     TEXT,
    status          TEXT,
    post_count      INTEGER,
    api_call_count  INTEGER,
    error           TEXT
);
"""


def db_path() -> Path:
    value = os.getenv("X_WING_ANALYTICS_DB_PATH")
    return Path(value).expanduser() if value else DEFAULT_DB_PATH


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: Optional[datetime] = None) -> str:
    return (dt or _utcnow()).isoformat()


def connect(path: Optional[Path] = None, *, readonly: bool = False) -> sqlite3.Connection:
    """Open the analytics DB. Initializes schema unless readonly."""
    target = Path(path) if path else db_path()
    if readonly:
        # uri mode=ro: fails cleanly if the file does not exist, and can never
        # create or write — this is the only mode the MCP tool uses.
        conn = sqlite3.connect(
            f"file:{target}?mode=ro", uri=True, timeout=10
        )
        conn.row_factory = sqlite3.Row
        return conn
    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    conn = sqlite3.connect(str(target), timeout=10, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=10000")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(SCHEMA)
    try:
        os.chmod(target, 0o600)
    except OSError:
        pass
    return conn


def claim_run(
    conn: sqlite3.Connection,
    collection_date: str,
    *,
    force: bool = False,
    stale_after_seconds: int = STALE_CLAIM_SECONDS,
) -> tuple[bool, Optional[dict[str, Any]]]:
    """Atomically claim the right to collect ``collection_date``.

    Returns (True, None) when this process holds the claim and may fetch.
    Returns (False, existing_run) when the day is already claimed/done.

    The claim happens inside BEGIN IMMEDIATE so concurrent processes
    serialize on the DB write lock; exactly one INSERT wins.
    """
    now = _utcnow()
    cutoff = _iso(now - timedelta(seconds=stale_after_seconds))
    conn.execute("BEGIN IMMEDIATE")
    try:
        if force:
            conn.execute(
                "DELETE FROM collection_runs WHERE collection_date = ?",
                (collection_date,),
            )
        cursor = conn.execute(
            "INSERT OR IGNORE INTO collection_runs"
            " (collection_date, started_at, status)"
            " VALUES (?, ?, 'running')",
            (collection_date, _iso(now)),
        )
        if cursor.rowcount == 1:
            conn.execute("COMMIT")
            return True, None

        row = conn.execute(
            "SELECT * FROM collection_runs WHERE collection_date = ?",
            (collection_date,),
        ).fetchone()
        existing = dict(row) if row else None

        # Reclaim a crashed run: still 'running' but started long ago.
        if existing and existing.get("status") == "running" and str(
            existing.get("started_at") or ""
        ) < cutoff:
            reclaimed = conn.execute(
                "UPDATE collection_runs SET started_at = ?, finished_at = NULL,"
                " status = 'running', post_count = NULL, api_call_count = NULL,"
                " error = 'reclaimed stale claim'"
                " WHERE collection_date = ? AND status = 'running'"
                " AND started_at < ?",
                (_iso(now), collection_date, cutoff),
            )
            if reclaimed.rowcount == 1:
                conn.execute("COMMIT")
                return True, None

        # Retry a failed run: nothing was stored, so re-fetching the same
        # day is safe and prevents analytics gaps from transient errors.
        if existing and existing.get("status") == "error":
            reclaimed = conn.execute(
                "UPDATE collection_runs SET started_at = ?, finished_at = NULL,"
                " status = 'running', post_count = NULL, api_call_count = NULL"
                " WHERE collection_date = ? AND status = 'error'",
                (_iso(now), collection_date),
            )
            if reclaimed.rowcount == 1:
                conn.execute("COMMIT")
                return True, None

        conn.execute("COMMIT")
        return False, existing
    except Exception:
        conn.execute("ROLLBACK")
        raise


def upsert_post_metrics(
    conn: sqlite3.Connection,
    collection_date: str,
    posts: list[dict[str, Any]],
) -> int:
    """Upsert per-post metric rows keyed (post_id, collected_date)."""
    with conn:
        for post in posts:
            conn.execute(
                "INSERT INTO post_metrics (post_id, collected_date, impressions,"
                " engagements, likes, replies, reposts, quotes, bookmarks,"
                " profile_clicks, url_clicks, raw_json)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
                " ON CONFLICT(post_id, collected_date) DO UPDATE SET"
                " impressions=excluded.impressions, engagements=excluded.engagements,"
                " likes=excluded.likes, replies=excluded.replies,"
                " reposts=excluded.reposts, quotes=excluded.quotes,"
                " bookmarks=excluded.bookmarks,"
                " profile_clicks=excluded.profile_clicks,"
                " url_clicks=excluded.url_clicks, raw_json=excluded.raw_json",
                (
                    post["post_id"],
                    collection_date,
                    post.get("impressions"),
                    post.get("engagements"),
                    post.get("likes"),
                    post.get("replies"),
                    post.get("reposts"),
                    post.get("quotes"),
                    post.get("bookmarks"),
                    post.get("profile_clicks"),
                    post.get("url_clicks"),
                    json.dumps(post.get("raw") or {}, sort_keys=True, default=str),
                ),
            )
    return len(posts)


def upsert_account_snapshot(
    conn: sqlite3.Connection, snapshot_date: str, snapshot: dict[str, Any]
) -> None:
    with conn:
        conn.execute(
            "INSERT INTO account_snapshots"
            " (snapshot_date, followers_count, following_count, raw_json)"
            " VALUES (?, ?, ?, ?)"
            " ON CONFLICT(snapshot_date) DO UPDATE SET"
            " followers_count=excluded.followers_count,"
            " following_count=excluded.following_count,"
            " raw_json=excluded.raw_json",
            (
                snapshot_date,
                snapshot.get("followers_count"),
                snapshot.get("following_count"),
                json.dumps(snapshot.get("raw") or {}, sort_keys=True, default=str),
            ),
        )


def finish_run(
    conn: sqlite3.Connection,
    collection_date: str,
    *,
    status: str,
    post_count: int = 0,
    api_call_count: int = 0,
    error: Optional[str] = None,
) -> None:
    with conn:
        conn.execute(
            "UPDATE collection_runs SET finished_at = ?, status = ?,"
            " post_count = ?, api_call_count = ?, error = ?"
            " WHERE collection_date = ?",
            (_iso(), status, post_count, api_call_count, error, collection_date),
        )


def latest_run(conn: sqlite3.Connection) -> Optional[dict[str, Any]]:
    row = conn.execute(
        "SELECT * FROM collection_runs ORDER BY collection_date DESC LIMIT 1"
    ).fetchone()
    return dict(row) if row else None


def latest_successful_run(conn: sqlite3.Connection) -> Optional[dict[str, Any]]:
    row = conn.execute(
        "SELECT * FROM collection_runs WHERE status = 'ok'"
        " ORDER BY collection_date DESC LIMIT 1"
    ).fetchone()
    return dict(row) if row else None


def read_analytics(
    *, days: int = 28, path: Optional[Path] = None, now: Optional[datetime] = None
) -> dict[str, Any]:
    """Read-only analytics view for the MCP tool. NEVER triggers a fetch.

    Returns whatever data exists, plus a visible warning when the store is
    empty or the newest successful collection run is stale (>36h).
    """
    try:
        days = max(1, min(int(days), 30))  # non-public metrics only exist ~30d
    except (TypeError, ValueError):
        days = 28
    now = now or _utcnow()
    window_start = (now - timedelta(days=days)).date().isoformat()

    target = Path(path) if path else db_path()
    result: dict[str, Any] = {
        "status": "ok",
        "window_days": days,
        "db_path": str(target),
        "warning": None,
        "collection": None,
        "follower_trend": [],
        "posts": [],
    }

    if not target.exists():
        result["status"] = "empty"
        result["warning"] = STALE_WARNING
        return result

    try:
        conn = connect(target, readonly=True)
    except sqlite3.Error as exc:
        result["status"] = "empty"
        result["warning"] = f"{STALE_WARNING} (db unreadable: {exc})"
        return result

    try:
        last_run = latest_run(conn)
        last_ok = latest_successful_run(conn)
        result["collection"] = {
            "latest_run": last_run,
            "latest_successful_run": last_ok,
        }

        stale = False
        if last_ok is None or not last_ok.get("finished_at"):
            stale = True
        else:
            try:
                finished = datetime.fromisoformat(
                    str(last_ok["finished_at"]).replace("Z", "+00:00")
                )
                stale = (now - finished) > timedelta(hours=STALE_DATA_HOURS)
            except ValueError:
                stale = True
        if stale:
            result["warning"] = STALE_WARNING

        trend_rows = conn.execute(
            "SELECT snapshot_date, followers_count, following_count"
            " FROM account_snapshots WHERE snapshot_date >= ?"
            " ORDER BY snapshot_date",
            (window_start,),
        ).fetchall()
        result["follower_trend"] = [dict(row) for row in trend_rows]

        # Latest row per post within the window.
        post_rows = conn.execute(
            "SELECT p.* FROM post_metrics p"
            " JOIN (SELECT post_id, MAX(collected_date) AS latest"
            "       FROM post_metrics WHERE collected_date >= ?"
            "       GROUP BY post_id) q"
            " ON p.post_id = q.post_id AND p.collected_date = q.latest"
            " ORDER BY p.impressions DESC NULLS LAST",
            (window_start,),
        ).fetchall()
        posts: list[dict[str, Any]] = []
        for row in post_rows:
            entry = {
                key: row[key]
                for key in (
                    "post_id",
                    "collected_date",
                    "impressions",
                    "engagements",
                    "likes",
                    "replies",
                    "reposts",
                    "quotes",
                    "bookmarks",
                    "profile_clicks",
                    "url_clicks",
                )
            }
            try:
                raw = json.loads(row["raw_json"] or "{}")
            except (TypeError, json.JSONDecodeError):
                raw = {}
            text = str(raw.get("text") or "")
            if text:
                entry["text"] = text[:140]
            entry["created_at"] = raw.get("created_at")
            entry["url"] = f"https://x.com/i/web/status/{row['post_id']}"
            posts.append(entry)
        result["posts"] = posts

        if not posts and not result["follower_trend"]:
            result["status"] = "empty"
            result["warning"] = result["warning"] or STALE_WARNING
        return result
    finally:
        conn.close()
