from types import SimpleNamespace

from xdata.providers import official_x
from xdata.providers.official_x import OfficialXProvider


class _Users:
    def get_by_username(self, username):
        assert username == "alice"
        return SimpleNamespace(data={"id": "42", "username": "alice"})

    def get_posts(self, **kwargs):
        assert kwargs["id"] == "42"
        return [
            SimpleNamespace(
                data=[
                    {
                        "id": "1",
                        "text": "official one",
                        "author_id": "42",
                        "created_at": "2026-07-03T00:00:00Z",
                        "public_metrics": {"like_count": 1},
                    }
                ]
            )
        ]

    def get_me(self):
        return SimpleNamespace(data={"id": "99"})

    def get_timeline(self, **kwargs):
        assert kwargs["id"] == "99"
        return [SimpleNamespace(data=[{"id": "2", "text": "home", "author_id": "42"}])]

    def get_mentions(self, **kwargs):
        assert kwargs["id"] == "99"
        return [SimpleNamespace(data=[])]

    def get_followers(self, **kwargs):
        assert kwargs["id"] == "42"
        return [
            SimpleNamespace(
                data=[
                    {
                        "id": "77",
                        "username": "follower",
                        "name": "Follower",
                        "description": "reads things",
                        "public_metrics": {"followers_count": 10, "following_count": 5, "tweet_count": 30},
                    }
                ]
            )
        ]

    def get_following(self, **kwargs):
        assert kwargs["id"] == "42"
        return [SimpleNamespace(data=[{"id": "88", "username": "followed", "name": "Followed"}])]


class _Posts:
    def search_recent(self, **kwargs):
        assert kwargs["query"] in {
            "ai",
            "conversation_id:1234567890",
            "conversation_id:1234567890 is:reply",
        }
        return [SimpleNamespace(data=[{"id": "3", "text": "search", "author_id": "42"}])]

    def get_quoted(self, **kwargs):
        assert kwargs["id"] == "1234567890"
        return [SimpleNamespace(data=[{"id": "4", "text": "quote", "author_id": "77"}])]


class _Client:
    users = _Users()
    posts = _Posts()


def test_missing_auth_returns_unavailable(monkeypatch):
    monkeypatch.delenv("X_OAUTH2_ACCESS_TOKEN", raising=False)
    monkeypatch.delenv("X_ACCESS_TOKEN", raising=False)

    provider = OfficialXProvider(client_factory=lambda token: _Client())
    result = provider.fetch_urls(["1234567890"])

    assert result.status == "unavailable"
    assert result.reason == "auth_required"


def test_missing_sdk_returns_unavailable_when_no_factory(monkeypatch):
    monkeypatch.setenv("X_OAUTH2_ACCESS_TOKEN", "token")
    monkeypatch.setattr("xdata.providers.official_x._load_xdk_client_factory", lambda: None)

    result = OfficialXProvider().fetch_urls(["1234567890"])

    assert result.status == "unavailable"
    assert result.reason == "sdk_missing"


def test_provider_uses_canonical_credential_manager(monkeypatch):
    monkeypatch.setenv("X_OAUTH2_ACCESS_TOKEN", "stale-process-token")
    seen = {}

    def ensure_access_token():
        seen["called"] = True
        return "canonical-token"

    monkeypatch.setattr(official_x.x_client, "ensure_access_token", ensure_access_token)
    provider = OfficialXProvider(client_factory=lambda token: seen.update(token=token) or _Client())

    result = provider.read_owned_timeline(limit=1)

    assert result.status == "ok"
    assert seen == {"called": True, "token": "canonical-token"}


def test_read_user_posts_resolves_username_and_normalizes(monkeypatch):
    monkeypatch.setenv("X_OAUTH2_ACCESS_TOKEN", "token")
    provider = OfficialXProvider(client_factory=lambda token: _Client())

    result = provider.read_user_posts("@alice", limit=10)

    assert result.status == "ok"
    assert result.items[0].id == "1"
    assert result.items[0].metrics.likes == 1
    assert result.cost.amount_usd > 0


def test_owned_timeline_uses_owned_read_cost(monkeypatch):
    monkeypatch.setenv("X_OAUTH2_ACCESS_TOKEN", "token")
    provider = OfficialXProvider(client_factory=lambda token: _Client())

    result = provider.read_owned_timeline(limit=10)

    assert result.status == "ok"
    assert result.items[0].text == "home"
    assert result.cost.basis == "$0.001/owned read (returned API resources)"


def test_owned_timeline_uses_xdk_post_fields(monkeypatch):
    """Protect the XDK v0.9+ parameter name from regressing to tweet_fields."""
    monkeypatch.setenv("X_OAUTH2_ACCESS_TOKEN", "token")
    captured = {}

    class _Users:
        def get_me(self):
            return SimpleNamespace(data={"id": "99"})

        def get_timeline(self, **kwargs):
            captured.update(kwargs)
            return [SimpleNamespace(data=[])]

    provider = OfficialXProvider(
        client_factory=lambda token: SimpleNamespace(users=_Users())
    )

    result = provider.read_owned_timeline(limit=1)

    assert result.status == "empty"
    assert captured["post_fields"] == ["created_at", "public_metrics", "text", "author_id"]
    assert "tweet_fields" not in captured


def test_search_recent(monkeypatch):
    monkeypatch.setenv("X_OAUTH2_ACCESS_TOKEN", "token")
    provider = OfficialXProvider(client_factory=lambda token: _Client())

    result = provider.search_recent("ai", limit=10)

    assert result.status == "ok"
    assert result.items[0].id == "3"


def test_read_thread_uses_recent_conversation_search(monkeypatch):
    monkeypatch.setenv("X_OAUTH2_ACCESS_TOKEN", "token")
    provider = OfficialXProvider(client_factory=lambda token: _Client())

    result = provider.read_thread("https://x.com/alice/status/1234567890", limit=10)

    assert result.status == "ok"
    assert result.items[0].id == "3"
    assert "official_recent_search_only" in result.warnings
    assert result.metadata["query"] == "conversation_id:1234567890"


def test_read_replies_uses_recent_conversation_search(monkeypatch):
    monkeypatch.setenv("X_OAUTH2_ACCESS_TOKEN", "token")
    provider = OfficialXProvider(client_factory=lambda token: _Client())

    result = provider.read_replies("1234567890", limit=10)

    assert result.status == "ok"
    assert result.metadata["query"] == "conversation_id:1234567890 is:reply"


def test_read_quotes_uses_quote_endpoint(monkeypatch):
    monkeypatch.setenv("X_OAUTH2_ACCESS_TOKEN", "token")
    provider = OfficialXProvider(client_factory=lambda token: _Client())

    result = provider.read_quotes("1234567890", limit=10)

    assert result.status == "ok"
    assert result.items[0].id == "4"


def test_read_follow_graph_returns_user_profiles(monkeypatch):
    monkeypatch.setenv("X_OAUTH2_ACCESS_TOKEN", "token")
    provider = OfficialXProvider(client_factory=lambda token: _Client())

    result = provider.read_follow_graph("@alice", graph="followers", limit=10)

    assert result.status == "ok"
    assert result.items[0].id == "77"
    assert result.items[0].username == "follower"
    assert result.items[0].public_metrics["followers_count"] == 10
    assert result.metadata["graph"] == "followers"


def test_official_provider_exposes_no_write_methods():
    provider = OfficialXProvider(client_factory=lambda token: _Client())

    forbidden = {"post", "delete", "like", "unlike", "repost", "follow", "unfollow", "dm_send"}

    assert forbidden.isdisjoint(set(dir(provider)))


def test_search_recent_enforces_min_max_results_for_small_limit(monkeypatch):
    monkeypatch.setenv("X_OAUTH2_ACCESS_TOKEN", "token")
    captured = {}

    class _Posts:
        def search_recent(self, **kwargs):
            captured.update(kwargs)
            return [
                SimpleNamespace(
                    data=[
                        {"id": "1", "text": "a", "author_id": "42"},
                        {"id": "2", "text": "b", "author_id": "42"},
                        {"id": "3", "text": "c", "author_id": "42"},
                        {"id": "4", "text": "d", "author_id": "42"},
                    ]
                )
            ]

    provider = OfficialXProvider(
        client_factory=lambda token: SimpleNamespace(
            posts=_Posts(),
            users=SimpleNamespace(get_me=lambda: SimpleNamespace(data={"id": "99"})),
        )
    )
    result = provider.search_recent("ai", limit=3)

    assert result.status == "ok"
    assert [item.id for item in result.items] == ["1", "2", "3"]
    # The requested limit (3) is below the search endpoint floor (10), so the
    # API page size must be raised to 10 even though we only keep 3 items.
    assert captured["max_results"] == 10


def test_search_recent_caps_request_size_at_max(monkeypatch):
    monkeypatch.setenv("X_OAUTH2_ACCESS_TOKEN", "token")
    captured = {}

    class _Posts:
        def search_recent(self, **kwargs):
            captured.update(kwargs)
            return [SimpleNamespace(data=[{"id": "1", "text": "a", "author_id": "42"}])]

    provider = OfficialXProvider(
        client_factory=lambda token: SimpleNamespace(
            posts=_Posts(),
            users=SimpleNamespace(get_me=lambda: SimpleNamespace(data={"id": "99"})),
        )
    )
    provider.search_recent("ai", limit=999)

    assert captured["max_results"] == 100


def test_read_user_posts_enforces_min_max_results_for_small_limit(monkeypatch):
    monkeypatch.setenv("X_OAUTH2_ACCESS_TOKEN", "token")
    captured = {}

    class _Users:
        def get_me(self):
            return SimpleNamespace(data={"id": "99"})

        def get_posts(self, **kwargs):
            captured.update(kwargs)
            return [
                SimpleNamespace(
                    data=[
                        {"id": "1", "text": "a", "author_id": "42"},
                        {"id": "2", "text": "b", "author_id": "42"},
                        {"id": "3", "text": "c", "author_id": "42"},
                    ]
                )
            ]

    # Passing a numeric id avoids the username-resolution call.
    provider = OfficialXProvider(client_factory=lambda token: SimpleNamespace(users=_Users()))
    result = provider.read_user_posts("42", limit=2)

    assert result.status == "ok"
    assert [item.id for item in result.items] == ["1", "2"]
    # X bills the three resources delivered in the API page, even though the
    # MCP caller asked to receive only two of them.
    assert result.cost.amount_usd == 0.015
    assert result.metadata["billed_resource_count"] == 3
    # The user posts endpoint floor is 5.
    assert captured["max_results"] == 5


def test_read_owned_timeline_enforces_min_max_results(monkeypatch):
    monkeypatch.setenv("X_OAUTH2_ACCESS_TOKEN", "token")
    captured = {}

    class _Users:
        def get_me(self):
            return SimpleNamespace(data={"id": "99"})

        def get_timeline(self, **kwargs):
            captured.update(kwargs)
            return [SimpleNamespace(data=[{"id": "1", "text": "home", "author_id": "99"}])]

    provider = OfficialXProvider(client_factory=lambda token: SimpleNamespace(users=_Users()))
    provider.read_owned_timeline(limit=3)

    assert captured["max_results"] == 5
