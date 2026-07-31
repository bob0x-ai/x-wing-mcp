from pathlib import Path
from types import SimpleNamespace

from xdata.providers.twikit import TwikitProvider


class _Result:
    def __init__(self, items, next_result=None, next_cursor=None):
        self._items = items
        self._next_result = next_result
        self.next_cursor = next_cursor

    def __iter__(self):
        return iter(self._items)

    async def next(self):
        if self._next_result is None:
            raise RuntimeError("no_next_page")
        return self._next_result


class _Client:
    def __init__(self, locale):
        self.locale = locale
        self.loaded_path = None

    async def load_cookies(self, path):
        self.loaded_path = path

    async def get_user_by_screen_name(self, handle):
        assert handle == "alice"
        return SimpleNamespace(id="42", screen_name="alice", name="Alice")

    async def get_user_tweets(self, user_id, tweet_type, count=40):
        assert user_id == "42"
        assert tweet_type == "Tweets"
        assert count == 2
        return _Result(
            [
                SimpleNamespace(
                    id="1",
                    full_text="first",
                    created_at="2026-07-09",
                    user=SimpleNamespace(id="42", screen_name="alice", name="Alice"),
                ),
                SimpleNamespace(
                    id="2",
                    full_text="second",
                    created_at="2026-07-09",
                    user=SimpleNamespace(id="42", screen_name="alice", name="Alice"),
                ),
            ],
            next_cursor="cursor-1",
        )

    async def search_tweet(self, query, mode, count=20):
        assert query == "ai"
        assert mode == "Latest"
        first = _Result(
            [
                SimpleNamespace(
                    id="1",
                    full_text="one",
                    created_at="2026-07-09",
                    user=SimpleNamespace(id="42", screen_name="alice", name="Alice"),
                )
            ],
            next_cursor="cursor-1",
        )
        second = _Result(
            [
                SimpleNamespace(
                    id="2",
                    full_text="two",
                    created_at="2026-07-09",
                    user=SimpleNamespace(id="42", screen_name="alice", name="Alice"),
                )
            ],
            next_cursor=None,
        )
        first._next_result = second
        return first


def test_missing_cookie_file_returns_unavailable(tmp_path):
    provider = TwikitProvider(
        cookies_file=str(tmp_path / "missing.json"),
        client_factory=lambda locale: _Client(locale),
        min_interval_seconds=0,
        jitter_seconds=0,
    )

    result = provider.search_posts("ai", limit=1)

    assert result.status == "unavailable"
    assert result.reason == "session_file_missing"


def test_read_user_posts_uses_cookie_file_session(tmp_path):
    cookies = tmp_path / "cookies.json"
    cookies.write_text("{}", encoding="utf-8")
    provider = TwikitProvider(
        cookies_file=str(cookies),
        client_factory=lambda locale: _Client(locale),
        min_interval_seconds=0,
        jitter_seconds=0,
    )

    result = provider.read_user_posts("@alice", limit=2)

    assert result.status == "ok"
    assert [item.id for item in result.items] == ["1", "2"]
    assert result.metadata["user_id"] == "42"
    assert result.metadata["next_cursor"] == "cursor-1"


def test_search_posts_paginates(tmp_path):
    cookies = tmp_path / "cookies.json"
    cookies.write_text("{}", encoding="utf-8")
    provider = TwikitProvider(
        cookies_file=str(cookies),
        client_factory=lambda locale: _Client(locale),
        min_interval_seconds=0,
        jitter_seconds=0,
    )

    result = provider.search_posts("ai", limit=2)

    assert result.status == "ok"
    assert [item.id for item in result.items] == ["1", "2"]
    assert result.metadata["query"] == "ai"
    assert result.metadata["search_type"] == "Latest"


def test_unauthorized_maps_to_session_invalid(tmp_path):
    class Unauthorized(Exception):
        pass

    class _UnauthorizedClient(_Client):
        async def search_tweet(self, query, mode, count=20):
            del query, mode, count
            raise Unauthorized("bad session")

    cookies = tmp_path / "cookies.json"
    cookies.write_text("{}", encoding="utf-8")
    provider = TwikitProvider(
        cookies_file=str(cookies),
        client_factory=lambda locale: _UnauthorizedClient(locale),
        min_interval_seconds=0,
        jitter_seconds=0,
    )

    result = provider.search_posts("ai", limit=1)

    assert result.status == "unavailable"
    assert result.reason == "session_invalid"
    assert provider.status()["cooldown_active"] is True


def test_rate_limit_activates_cooldown(tmp_path):
    class TooManyRequests(Exception):
        rate_limit_reset = 9999999999

    class _LimitedClient(_Client):
        async def search_tweet(self, query, mode, count=20):
            del query, mode, count
            raise TooManyRequests("slow down")

    cookies = tmp_path / "cookies.json"
    cookies.write_text("{}", encoding="utf-8")
    provider = TwikitProvider(
        cookies_file=str(cookies),
        client_factory=lambda locale: _LimitedClient(locale),
        min_interval_seconds=0,
        jitter_seconds=0,
    )

    first = provider.search_posts("ai", limit=1)
    second = provider.search_posts("ai", limit=1)

    assert first.status == "unavailable"
    assert first.reason == "rate_limited"
    assert second.status == "unavailable"
    assert second.reason == "cooldown_active"
