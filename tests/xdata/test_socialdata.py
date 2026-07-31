import json
from urllib.parse import parse_qs, urlparse

from xdata.providers.socialdata import HttpResponse, SocialDataProvider


def _tweet(tweet_id: str, *, username: str = "alice", text: str | None = None) -> dict:
    return {
        "tweet_created_at": "2026-07-03T00:00:00Z",
        "id_str": tweet_id,
        "full_text": text or f"post {tweet_id}",
        "user": {
            "id_str": "42",
            "name": "Alice",
            "screen_name": username,
            "followers_count": 100,
            "friends_count": 10,
            "statuses_count": 20,
        },
        "reply_count": 1,
        "retweet_count": 2,
        "favorite_count": 3,
        "quote_count": 4,
        "views_count": 5,
    }


def _user(user_id: str, *, username: str = "alice") -> dict:
    return {
        "id_str": user_id,
        "name": "Alice",
        "screen_name": username,
        "description": "bio",
        "followers_count": 100,
        "friends_count": 10,
        "listed_count": 5,
        "favourites_count": 7,
        "statuses_count": 20,
    }


def test_missing_auth_returns_unavailable(monkeypatch):
    monkeypatch.delenv("SOCIALDATA_API_KEY", raising=False)

    provider = SocialDataProvider(min_interval_seconds=0, jitter_seconds=0)
    result = provider.fetch_urls(["1234567890"])

    assert result.status == "unavailable"
    assert result.reason == "auth_required"


def test_fetch_urls_reads_single_tweets(monkeypatch):
    monkeypatch.setenv("SOCIALDATA_API_KEY", "token")

    def http_get(url, headers, timeout):
        del headers, timeout
        assert url.endswith("/tweets/1234567890")
        return HttpResponse(status_code=200, text=json.dumps(_tweet("1234567890")))

    provider = SocialDataProvider(http_get=http_get, min_interval_seconds=0, jitter_seconds=0)
    result = provider.fetch_urls(["https://x.com/alice/status/1234567890"])

    assert result.status == "ok"
    assert result.items[0].id == "1234567890"
    assert result.items[0].author.username == "alice"
    assert result.items[0].metrics.likes == 3


def test_fetch_urls_skips_not_found_posts(monkeypatch):
    monkeypatch.setenv("SOCIALDATA_API_KEY", "token")

    def http_get(url, headers, timeout):
        del headers, timeout
        if url.endswith("/tweets/11111"):
            return HttpResponse(status_code=404, text=json.dumps({"status": "error", "message": "Tweet not found"}))
        return HttpResponse(status_code=200, text=json.dumps(_tweet("22222")))

    provider = SocialDataProvider(http_get=http_get, min_interval_seconds=0, jitter_seconds=0)
    result = provider.fetch_urls(["11111", "22222"])

    assert result.status == "ok"
    assert [item.id for item in result.items] == ["22222"]
    assert "post_unavailable:11111" in result.warnings


def test_read_user_posts_resolves_username_and_reads_timeline(monkeypatch):
    monkeypatch.setenv("SOCIALDATA_API_KEY", "token")

    def http_get(url, headers, timeout):
        del headers, timeout
        parsed = urlparse(url)
        if parsed.path.endswith("/user/alice"):
            return HttpResponse(status_code=200, text=json.dumps(_user("42")))
        if parsed.path.endswith("/user/42/tweets"):
            return HttpResponse(
                status_code=200,
                text=json.dumps({"tweets": [_tweet("1"), _tweet("2")], "next_cursor": "cursor-1"}),
            )
        raise AssertionError(url)

    provider = SocialDataProvider(http_get=http_get, min_interval_seconds=0, jitter_seconds=0)
    result = provider.read_user_posts("@alice", limit=1)

    assert result.status == "ok"
    assert [item.id for item in result.items] == ["1"]
    assert result.metadata["user_id"] == "42"
    assert result.metadata["next_cursor"] == "cursor-1"


def test_search_posts_paginates_until_limit(monkeypatch):
    monkeypatch.setenv("SOCIALDATA_API_KEY", "token")
    calls = []

    def http_get(url, headers, timeout):
        del headers, timeout
        parsed = urlparse(url)
        calls.append(url)
        params = parse_qs(parsed.query)
        cursor = params.get("cursor", [None])[0]
        if cursor is None:
            payload = {"tweets": [_tweet("1"), _tweet("2")], "next_cursor": "next-a"}
        else:
            payload = {"tweets": [_tweet("3")], "next_cursor": None}
        return HttpResponse(status_code=200, text=json.dumps(payload))

    provider = SocialDataProvider(http_get=http_get, min_interval_seconds=0, jitter_seconds=0)
    result = provider.search_posts("from:alice", limit=3)

    assert result.status == "ok"
    assert [item.id for item in result.items] == ["1", "2", "3"]
    assert len(calls) == 2
    assert result.metadata["query"] == "from:alice"


def test_read_thread_uses_thread_endpoint(monkeypatch):
    monkeypatch.setenv("SOCIALDATA_API_KEY", "token")

    def http_get(url, headers, timeout):
        del headers, timeout
        assert "/thread/1234567890" in url
        return HttpResponse(status_code=200, text=json.dumps({"tweets": [_tweet("1"), _tweet("2")]}))

    provider = SocialDataProvider(http_get=http_get, min_interval_seconds=0, jitter_seconds=0)
    result = provider.read_thread("1234567890", limit=10)

    assert result.status == "ok"
    assert result.metadata["thread_id"] == "1234567890"


def test_read_replies_uses_comments_endpoint(monkeypatch):
    monkeypatch.setenv("SOCIALDATA_API_KEY", "token")

    def http_get(url, headers, timeout):
        del headers, timeout
        assert "/tweets/1234567890/comments" in url
        return HttpResponse(status_code=200, text=json.dumps({"tweets": [_tweet("9")]}))

    provider = SocialDataProvider(http_get=http_get, min_interval_seconds=0, jitter_seconds=0)
    result = provider.read_replies("1234567890", limit=10)

    assert result.status == "ok"
    assert "top_level_post_only" in result.warnings


def test_read_quotes_uses_quotes_endpoint(monkeypatch):
    monkeypatch.setenv("SOCIALDATA_API_KEY", "token")

    def http_get(url, headers, timeout):
        del headers, timeout
        assert "/tweets/1234567890/quotes" in url
        return HttpResponse(status_code=200, text=json.dumps({"tweets": [_tweet("10")]}))

    provider = SocialDataProvider(http_get=http_get, min_interval_seconds=0, jitter_seconds=0)
    result = provider.read_quotes("1234567890", limit=10)

    assert result.status == "ok"
    assert result.items[0].id == "10"


def test_read_follow_graph_resolves_handle_and_returns_profiles(monkeypatch):
    monkeypatch.setenv("SOCIALDATA_API_KEY", "token")

    def http_get(url, headers, timeout):
        del headers, timeout
        parsed = urlparse(url)
        if parsed.path.endswith("/user/alice"):
            return HttpResponse(status_code=200, text=json.dumps(_user("42")))
        if parsed.path.endswith("/followers/list"):
            return HttpResponse(
                status_code=200,
                text=json.dumps({"users": [_user("77", username="bob")], "next_cursor": "cur"}),
            )
        raise AssertionError(url)

    provider = SocialDataProvider(http_get=http_get, min_interval_seconds=0, jitter_seconds=0)
    result = provider.read_follow_graph("@alice", graph="followers", limit=1)

    assert result.status == "ok"
    assert result.items[0].username == "bob"
    assert result.metadata["graph"] == "followers"
    assert result.metadata["user_id"] == "42"


def test_collect_posts_uses_search_endpoint(monkeypatch):
    monkeypatch.setenv("SOCIALDATA_API_KEY", "token")

    def http_get(url, headers, timeout):
        del headers, timeout
        assert "/search?" in url
        return HttpResponse(status_code=200, text=json.dumps({"tweets": [_tweet("1"), _tweet("2")]}))

    provider = SocialDataProvider(http_get=http_get, min_interval_seconds=0, jitter_seconds=0)
    result = provider.collect_posts("lang:en ai", limit=2)

    assert result.status == "ok"
    assert result.metadata["mode"] == "bulk"


def test_insufficient_balance_maps_to_unavailable(monkeypatch):
    monkeypatch.setenv("SOCIALDATA_API_KEY", "token")

    def http_get(url, headers, timeout):
        del url, headers, timeout
        return HttpResponse(status_code=402, text=json.dumps({"status": "error", "message": "Insufficient balance"}))

    provider = SocialDataProvider(http_get=http_get, min_interval_seconds=0, jitter_seconds=0)
    result = provider.search_posts("ai", limit=1)

    assert result.status == "unavailable"
    assert result.reason == "insufficient_balance"


def test_non_2xx_non_dict_body_maps_to_status_not_error(monkeypatch):
    monkeypatch.setenv("SOCIALDATA_API_KEY", "token")

    def http_get(url, headers, timeout):
        del url, headers, timeout
        # A 404 with a JSON list (non-dict) body must still map to not_found,
        # not to an unexpected_payload error.
        return HttpResponse(status_code=404, text=json.dumps(["not", "a", "dict"]))

    provider = SocialDataProvider(http_get=http_get, min_interval_seconds=0, jitter_seconds=0)
    result = provider.search_posts("ai", limit=1)

    assert result.status == "unavailable"
    assert result.reason == "not_found"


def test_rate_limit_with_non_dict_body_maps_to_rate_limited(monkeypatch):
    monkeypatch.setenv("SOCIALDATA_API_KEY", "token")

    def http_get(url, headers, timeout):
        del url, headers, timeout
        return HttpResponse(status_code=429, text=json.dumps("rate limited string"))

    provider = SocialDataProvider(http_get=http_get)
    result = provider.fetch_urls(["1234567890"])

    assert result.status == "unavailable"
    assert result.reason == "rate_limited"


def test_rate_limit_activates_provider_cooldown(monkeypatch):
    monkeypatch.setenv("SOCIALDATA_API_KEY", "token")
    now = {"value": 100.0}

    def time_fn():
        return now["value"]

    calls = {"count": 0}

    def http_get(url, headers, timeout):
        del url, headers, timeout
        calls["count"] += 1
        return HttpResponse(status_code=429, text=json.dumps({"status": "error", "message": "Rate limited"}))

    provider = SocialDataProvider(
        http_get=http_get,
        cooldown_seconds=30,
        time_fn=time_fn,
        min_interval_seconds=0,
        jitter_seconds=0,
    )

    first = provider.search_posts("ai", limit=1)
    second = provider.search_posts("ai", limit=1)
    status = provider.status()

    assert first.status == "unavailable"
    assert first.reason == "rate_limited"
    assert second.status == "unavailable"
    assert second.reason == "cooldown_active"
    assert second.metadata["cooldown_reason"] == "rate_limited"
    assert status["cooldown_active"] is True
    assert status["cooldown_reason"] == "rate_limited"
    assert calls["count"] == 1


def test_local_rate_limiter_waits_transparently(monkeypatch):
    monkeypatch.setenv("SOCIALDATA_API_KEY", "token")
    now = {"value": 100.0}
    sleeps: list[float] = []

    def time_fn():
        return now["value"]

    def sleep_fn(seconds):
        sleeps.append(seconds)
        now["value"] += seconds

    def http_get(url, headers, timeout):
        del url, headers, timeout
        return HttpResponse(status_code=200, text=json.dumps({"tweets": [_tweet("1")]}))

    provider = SocialDataProvider(
        http_get=http_get,
        time_fn=time_fn,
        sleep_fn=sleep_fn,
        random_fn=lambda: 0.0,
        min_interval_seconds=2.0,
        jitter_seconds=0.0,
    )

    first = provider.search_posts("ai", limit=1)
    second = provider.search_posts("ai", limit=1)

    assert first.status == "ok"
    assert second.status == "ok"
    assert sleeps == [2.0]
    assert provider.status()["local_rate_limit"]["enabled"] is True


def test_non_dict_success_body_still_returns_unexpected_payload(monkeypatch):
    monkeypatch.setenv("SOCIALDATA_API_KEY", "token")

    def http_get(url, headers, timeout):
        del url, headers, timeout
        return HttpResponse(status_code=200, text=json.dumps(["a", "b"]))

    provider = SocialDataProvider(http_get=http_get, min_interval_seconds=0, jitter_seconds=0)
    result = provider.search_posts("ai", limit=1)

    assert result.status == "error"
    assert result.reason == "unexpected_payload"


def test_non_2xx_dict_body_still_surfaces_message(monkeypatch):
    monkeypatch.setenv("SOCIALDATA_API_KEY", "token")

    def http_get(url, headers, timeout):
        del url, headers, timeout
        return HttpResponse(
            status_code=402,
            text=json.dumps({"status": "error", "message": "Insufficient balance"}),
        )

    provider = SocialDataProvider(http_get=http_get, min_interval_seconds=0, jitter_seconds=0)
    result = provider.fetch_urls(["1234567890"])

    assert result.status == "unavailable"
    assert result.reason == "insufficient_balance"
    assert "Insufficient balance" in result.warnings
