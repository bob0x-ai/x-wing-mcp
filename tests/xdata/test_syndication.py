import json

from xdata.providers.syndication import (
    HttpResponse,
    SyndicationProvider,
    extract_post_id,
    normalize_handle,
)


def test_extract_post_id_accepts_ids_and_urls():
    assert extract_post_id("1234567890") == "1234567890"
    assert extract_post_id("https://x.com/alice/status/1234567890") == "1234567890"
    assert extract_post_id("https://twitter.com/alice/statuses/1234567890") == "1234567890"
    assert extract_post_id("https://x.com/i/web/status/1234567890") == "1234567890"
    assert extract_post_id("not-a-post") is None


def test_normalize_handle_strips_at():
    assert normalize_handle("@alice") == "alice"
    assert normalize_handle(" alice ") == "alice"


def test_fetch_urls_returns_unavailable_for_deleted_or_protected_post():
    def http_get(url, timeout):
        del url, timeout
        return HttpResponse(status_code=404, text="")

    provider = SyndicationProvider(http_get=http_get, min_interval_seconds=0, jitter_seconds=0)
    result = provider.fetch_urls(["https://x.com/alice/status/1234567890"])

    assert result.status == "unavailable"
    assert result.reason == "no_fetchable_posts"
    assert "post_unavailable:1234567890" in result.warnings


def test_fetch_urls_returns_unavailable_for_rate_limit():
    def http_get(url, timeout):
        del url, timeout
        return HttpResponse(status_code=429, text="rate limited")

    provider = SyndicationProvider(http_get=http_get, min_interval_seconds=0, jitter_seconds=0)
    result = provider.fetch_urls(["1234567890"])

    assert result.status == "unavailable"
    assert result.reason == "rate_limited"


def test_fetch_urls_parses_tweet_result_json():
    payload = {
        "id_str": "1234567890",
        "text": "A post",
        "created_at": "Fri Jul 03 00:00:00 +0000 2026",
        "user": {"id_str": "42", "screen_name": "alice", "name": "Alice"},
        "reply_count": 1,
        "retweet_count": 2,
        "favorite_count": 3,
        "quote_count": 4,
    }

    def http_get(url, timeout):
        del timeout
        assert "tweet-result" in url
        return HttpResponse(status_code=200, text=json.dumps(payload))

    provider = SyndicationProvider(http_get=http_get, min_interval_seconds=0, jitter_seconds=0)
    result = provider.fetch_urls(["1234567890"])

    assert result.status == "ok"
    assert result.items[0].author.username == "alice"
    assert result.items[0].metrics.likes == 3


def test_read_user_posts_parses_embedded_json_script():
    html = """
    <html><body>
      <script type="application/json">
      {"tweets":[{"id_str":"1","text":"one","user":{"screen_name":"alice"}},
                 {"id_str":"2","text":"two","user":{"screen_name":"alice"}}]}
      </script>
    </body></html>
    """

    def http_get(url, timeout):
        del timeout
        assert "screen-name/alice" in url
        return HttpResponse(status_code=200, text=html)

    provider = SyndicationProvider(http_get=http_get, min_interval_seconds=0, jitter_seconds=0)
    result = provider.read_user_posts("@alice", limit=1)

    assert result.status == "ok"
    assert len(result.items) == 1
    assert result.items[0].id == "1"


def test_read_user_posts_empty_when_no_parseable_posts():
    def http_get(url, timeout):
        del url, timeout
        return HttpResponse(status_code=200, text="<html></html>")

    provider = SyndicationProvider(http_get=http_get, min_interval_seconds=0, jitter_seconds=0)
    result = provider.read_user_posts("alice")

    assert result.status == "empty"
    assert result.reason == "timeline_no_parseable_posts"


def test_read_user_posts_returns_unavailable_for_rate_limit():
    def http_get(url, timeout):
        del url, timeout
        return HttpResponse(status_code=429, text="rate limited")

    provider = SyndicationProvider(http_get=http_get, min_interval_seconds=0, jitter_seconds=0)
    result = provider.read_user_posts("alice")

    assert result.status == "unavailable"
    assert result.reason == "rate_limited"


def test_rate_limit_activates_syndication_cooldown():
    now = {"value": 100.0}

    def time_fn():
        return now["value"]

    calls = {"count": 0}

    def http_get(url, timeout):
        del url, timeout
        calls["count"] += 1
        return HttpResponse(status_code=429, text="rate limited")

    provider = SyndicationProvider(
        http_get=http_get,
        cooldown_seconds=30,
        time_fn=time_fn,
        min_interval_seconds=0,
        jitter_seconds=0,
    )

    first = provider.fetch_urls(["1234567890"])
    second = provider.fetch_urls(["1234567890"])
    status = provider.status()

    assert first.status == "unavailable"
    assert first.reason == "rate_limited"
    assert second.status == "unavailable"
    assert second.reason == "cooldown_active"
    assert second.metadata["cooldown_reason"] == "rate_limited"
    assert status["cooldown_active"] is True
    assert status["cooldown_reason"] == "rate_limited"
    assert calls["count"] == 1


def test_local_rate_limiter_waits_transparently():
    now = {"value": 100.0}
    sleeps: list[float] = []
    payload = {
        "id_str": "1234567890",
        "text": "A post",
        "user": {"id_str": "42", "screen_name": "alice", "name": "Alice"},
    }

    def time_fn():
        return now["value"]

    def sleep_fn(seconds):
        sleeps.append(seconds)
        now["value"] += seconds

    def http_get(url, timeout):
        del url, timeout
        return HttpResponse(status_code=200, text=json.dumps(payload))

    provider = SyndicationProvider(
        http_get=http_get,
        time_fn=time_fn,
        sleep_fn=sleep_fn,
        random_fn=lambda: 0.0,
        min_interval_seconds=2.0,
        jitter_seconds=0.0,
    )

    first = provider.fetch_urls(["1234567890"])
    second = provider.fetch_urls(["1234567890"])

    assert first.status == "ok"
    assert second.status == "ok"
    assert sleeps == [2.0]
    assert provider.status()["local_rate_limit"]["enabled"] is True
