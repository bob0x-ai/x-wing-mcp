"""Tests for the analytics collector, store, and read-only analytics tool.

All X API responses are mocked. No network, no real API calls.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

import analytics_collector
import analytics_store


ME_PAYLOAD = {
    "id": "111",
    "username": "hikari_signal",
    "public_metrics": {"followers_count": 420, "following_count": 123},
}

POST_A = {
    "id": "2001",
    "text": "first post",
    "created_at": "2026-08-13T10:00:00.000Z",
    "public_metrics": {
        "like_count": 10,
        "reply_count": 2,
        "retweet_count": 1,
        "quote_count": 3,
        "bookmark_count": 4,
        "impression_count": 900,
    },
    "non_public_metrics": {
        "impression_count": 950,
        "engagements": 55,
        "user_profile_clicks": 7,
        "url_link_clicks": 5,
    },
    "organic_metrics": {"impression_count": 940},
}

POST_B = {
    "id": "2002",
    "text": "second post",
    "created_at": "2026-08-12T09:00:00.000Z",
    "public_metrics": {
        "like_count": 1,
        "reply_count": 0,
        "retweet_count": 0,
        "quote_count": 0,
        "bookmark_count": 0,
        "impression_count": 100,
    },
    "non_public_metrics": {
        "impression_count": 110,
        "engagements": 4,
        "user_profile_clicks": 1,
        "url_link_clicks": 0,
    },
    "organic_metrics": {"impression_count": 108},
}


class MockUsersApi:
    def __init__(self, posts=None, me=None, simulated_calls=2):
        self._posts = POST_A if posts is None else posts
        self._me = ME_PAYLOAD if me is None else me
        self.simulated_calls = simulated_calls

    def get_me(self, user_fields=None):
        return SimpleNamespace(data=self._me)

    def get_posts(self, **kwargs):
        assert kwargs["max_results"] == 100
        assert "non_public_metrics" in kwargs["post_fields"]
        assert "organic_metrics" in kwargs["post_fields"]
        assert "public_metrics" in kwargs["post_fields"]
        page = SimpleNamespace(data=self._posts)
        return iter([page])


class MockClient:
    def __init__(self, posts=None, me=None):
        self.users = MockUsersApi(posts=posts, me=me)


def mock_factory(counter, posts=None, me=None):
    """Test seam: returns a mock client; simulates 2 real HTTP calls."""
    counter.count += 2
    return MockClient(posts=posts, me=me)


@pytest.fixture()
def db(tmp_path, monkeypatch):
    path = tmp_path / "analytics.db"
    monkeypatch.setenv("X_WING_ANALYTICS_DB_PATH", str(path))
    return path


def test_first_run_stores_posts_snapshot_and_run(db):
    result = analytics_collector.run(
        collection_date="2026-08-14",
        db_path=db,
        fetch_client_factory=lambda c: mock_factory(c, posts=[POST_A, POST_B]),
    )
    assert result["status"] == "ok"
    assert result["post_count"] == 2
    assert result["followers_count"] == 420
    assert result["api_call_count"] == 2

    conn = sqlite3.connect(str(db))
    posts = conn.execute(
        "SELECT * FROM post_metrics ORDER BY post_id"
    ).fetchall()
    assert len(posts) == 2
    snap = conn.execute("SELECT * FROM account_snapshots").fetchall()
    assert len(snap) == 1 and snap[0][1] == 420 and snap[0][2] == 123
    run = conn.execute("SELECT * FROM collection_runs").fetchall()
    assert len(run) == 1
    assert run[0][3] == "ok"  # status column
    assert run[0][4] == 2  # post_count
    assert run[0][5] == 2  # api_call_count logged per run
    conn.close()

    # metric mapping: organic impressions preferred, non-public engagements
    row = analytics_store.connect(db, readonly=True).execute(
        "SELECT * FROM post_metrics WHERE post_id='2001'"
    ).fetchone()
    assert row["impressions"] == 940
    assert row["engagements"] == 55
    assert row["likes"] == 10
    assert row["reposts"] == 1
    assert row["quotes"] == 3
    assert row["bookmarks"] == 4
    assert row["profile_clicks"] == 7
    assert row["url_clicks"] == 5
    raw = json.loads(row["raw_json"])
    assert raw["text"] == "first post"  # raw JSON preserved per row


def test_second_run_same_day_is_noop(db):
    first = analytics_collector.run(
        collection_date="2026-08-14",
        db_path=db,
        fetch_client_factory=lambda c: mock_factory(c, posts=[POST_A]),
    )
    assert first["status"] == "ok"

    def forbidden_factory(counter):
        counter.count += 999  # must never be reached
        return MockClient()

    second = analytics_collector.run(
        collection_date="2026-08-14",
        db_path=db,
        fetch_client_factory=forbidden_factory,
    )
    assert second["status"] == "skipped"
    assert second["reason"] == "already_claimed"
    assert second["api_call_count"] == 0

    conn = sqlite3.connect(str(db))
    assert conn.execute("SELECT COUNT(*) FROM post_metrics").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM collection_runs").fetchone()[0] == 1
    conn.close()


def test_force_reruns_claimed_day(db):
    analytics_collector.run(
        collection_date="2026-08-14",
        db_path=db,
        fetch_client_factory=lambda c: mock_factory(c, posts=[POST_A]),
    )
    again = analytics_collector.run(
        collection_date="2026-08-14",
        db_path=db,
        force=True,
        fetch_client_factory=lambda c: mock_factory(c, posts=[POST_A, POST_B]),
    )
    assert again["status"] == "ok"
    conn = sqlite3.connect(str(db))
    assert conn.execute("SELECT COUNT(*) FROM post_metrics").fetchone()[0] == 2
    assert conn.execute("SELECT COUNT(*) FROM collection_runs").fetchone()[0] == 1
    conn.close()


def test_concurrent_claims_exactly_one_winner(db):
    """N concurrent claimants -> exactly one claim; losers never fetch."""
    analytics_store.connect(db).close()  # initialize schema
    outcomes = []
    barrier = threading.Barrier(6)

    def claimant():
        conn = analytics_store.connect(db)
        try:
            barrier.wait(timeout=10)
            claimed, _ = analytics_store.claim_run(conn, "2026-08-14")
            outcomes.append(claimed)
        finally:
            conn.close()

    threads = [threading.Thread(target=claimant) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert outcomes.count(True) == 1
    assert outcomes.count(False) == 5
    conn = sqlite3.connect(str(db))
    assert conn.execute("SELECT COUNT(*) FROM collection_runs").fetchone()[0] == 1
    conn.close()


def test_stale_running_claim_is_reclaimed(db):
    conn = analytics_store.connect(db)
    old = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    with conn:
        conn.execute(
            "INSERT INTO collection_runs (collection_date, started_at, status)"
            " VALUES ('2026-08-14', ?, 'running')",
            (old,),
        )
    claimed, _ = analytics_store.claim_run(conn, "2026-08-14")
    assert claimed is True
    conn.close()


def test_failed_run_records_error_and_call_count(db):
    def broken_factory(counter):
        counter.count += 1
        client = MockClient()
        client.users.get_me = lambda user_fields=None: (_ for _ in ()).throw(
            RuntimeError("boom")
        )
        return client

    with pytest.raises(RuntimeError):
        analytics_collector.run(
            collection_date="2026-08-14",
            db_path=db,
            fetch_client_factory=broken_factory,
        )
    conn = analytics_store.connect(db, readonly=True)
    run = conn.execute("SELECT * FROM collection_runs").fetchone()
    assert run["status"] == "error"
    assert run["api_call_count"] == 1
    assert "boom" in run["error"]
    conn.close()


def test_read_analytics_missing_db_warns(tmp_path, monkeypatch):
    monkeypatch.setenv(
        "X_WING_ANALYTICS_DB_PATH", str(tmp_path / "absent.db")
    )
    result = analytics_store.read_analytics(days=28)
    assert result["status"] == "empty"
    assert "system cron" in result["warning"]
    assert result["posts"] == []


def test_read_analytics_empty_db_warns(db):
    analytics_store.connect(db).close()  # schema only, no runs
    result = analytics_store.read_analytics(days=28)
    assert result["status"] == "empty"
    assert "system cron" in result["warning"]


def test_read_analytics_fresh_data_no_warning(db):
    analytics_collector.run(
        collection_date="2026-08-14",
        db_path=db,
        fetch_client_factory=lambda c: mock_factory(c, posts=[POST_A, POST_B]),
    )
    result = analytics_store.read_analytics(days=28)
    assert result["status"] == "ok"
    assert result["warning"] is None
    assert len(result["posts"]) == 2
    top = result["posts"][0]
    assert top["post_id"] == "2001"  # ordered by impressions desc
    assert top["impressions"] == 940
    assert top["profile_clicks"] == 7
    assert top["text"] == "first post"
    assert result["follower_trend"] == [
        {"snapshot_date": "2026-08-14", "followers_count": 420, "following_count": 123}
    ]
    assert result["collection"]["latest_successful_run"]["api_call_count"] == 2


def test_read_analytics_stale_run_warns_but_returns_data(db):
    analytics_collector.run(
        collection_date="2026-08-12",
        db_path=db,
        fetch_client_factory=lambda c: mock_factory(c, posts=[POST_A]),
    )
    stale_ts = (datetime.now(timezone.utc) - timedelta(hours=48)).isoformat()
    conn = analytics_store.connect(db)
    with conn:
        conn.execute(
            "UPDATE collection_runs SET finished_at = ?", (stale_ts,)
        )
    conn.close()
    result = analytics_store.read_analytics(days=28)
    assert result["warning"] is not None
    assert "system cron" in result["warning"]
    assert len(result["posts"]) == 1  # data still returned


def test_read_analytics_never_fetches(db, monkeypatch):
    """The read path must not touch the network: sabotage x_client and xdk."""
    analytics_collector.run(
        collection_date="2026-08-14",
        db_path=db,
        fetch_client_factory=lambda c: mock_factory(c, posts=[POST_A]),
    )

    def explode(*args, **kwargs):
        raise AssertionError("read path triggered a fetch/auth call")

    monkeypatch.setattr("x_client.get_client", explode)
    monkeypatch.setattr("x_client.ensure_access_token", explode)
    monkeypatch.setattr("x_client.run_auth_operation", explode)
    result = analytics_store.read_analytics(days=28)
    assert result["status"] == "ok"
