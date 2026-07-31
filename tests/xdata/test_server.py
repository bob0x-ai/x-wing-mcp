from xdata.contracts import Post, ProviderResult
from xdata.server import (
    MAX_FETCH_URLS,
    clamp_limit,
    create_mcp_server,
    x_collect_posts_handler,
    x_data_healthcheck_handler,
    x_data_status_handler,
    x_fetch_urls_handler,
    x_read_follow_graph_handler,
    x_read_quotes_handler,
    x_read_replies_handler,
    x_read_article_handler,
    x_read_thread_handler,
    x_read_user_posts_handler,
    x_search_posts_handler,
)


class _Router:
    def __init__(self):
        self.calls = []

    def fetch_urls(self, values, *, max_cost_usd):
        del max_cost_usd
        self.calls.append(("fetch_urls", values))
        return ProviderResult.ok(provider="test", items=[Post(id="1", text="ok")])

    def read_user_posts_recent(self, user, *, max_cost_usd, limit=20, cursor=None):
        del max_cost_usd
        self.calls.append(("read_user_posts_recent", user, limit, cursor))
        return ProviderResult.ok(provider="test", items=[Post(id="2", text="user")])

    def search_posts(self, query, *, max_cost_usd, limit=20, cursor=None):
        del max_cost_usd
        self.calls.append(("search_posts", query, limit, cursor))
        return ProviderResult.empty(provider="router", reason="all_routes_exhausted")

    def read_thread(self, value, *, max_cost_usd, limit=100, cursor=None):
        del max_cost_usd
        self.calls.append(("read_thread", value, limit, cursor))
        return ProviderResult.empty(provider="router", reason="all_routes_exhausted")

    def read_replies(self, value, *, max_cost_usd, limit=100, cursor=None):
        del max_cost_usd
        self.calls.append(("read_replies", value, limit, cursor))
        return ProviderResult.empty(provider="router", reason="all_routes_exhausted")

    def read_quotes(self, value, *, max_cost_usd, limit=100, cursor=None):
        del max_cost_usd
        self.calls.append(("read_quotes", value, limit, cursor))
        return ProviderResult.empty(provider="router", reason="all_routes_exhausted")

    def read_follow_graph(self, user, *, max_cost_usd, graph="followers", limit=100, cursor=None):
        del max_cost_usd
        self.calls.append(("read_follow_graph", user, graph, limit, cursor))
        return ProviderResult.empty(provider="router", reason="all_routes_exhausted")

    def read_article(self, value, *, max_cost_usd):
        del max_cost_usd
        self.calls.append(("read_article", value))
        return ProviderResult.ok(provider="test", items=[{"id": "a1", "title": "Article"}])

    def collect_posts(self, query, *, max_cost_usd, limit=100, cursor=None):
        del max_cost_usd
        self.calls.append(("collect_posts", query, limit, cursor))
        return ProviderResult.empty(provider="router", reason="all_routes_exhausted")

    def status(self):
        return {
            "providers": {
                "official_x": {
                    "provider": "official_x",
                    "implemented": True,
                    "auth_present": True,
                    "sdk_available": True,
                    "supports_tasks": ["fetch_urls"],
                    "usable": True,
                }
            },
            "routes": {"fetch_urls": ["test"]},
            "effective_routes": {"fetch_urls": ["test"]},
            "preferred_providers": {"fetch_urls": "test"},
            "task_coverage": {"fetch_urls": {"available": True, "preferred_provider": "test"}},
            "tasks": ["fetch_urls"],
        }

    def healthcheck(self, *, mode="live", provider=None):
        return {
            "mode": mode,
            "provider": provider,
            "providers": {
                "official_x": {
                    "provider": "official_x",
                    "probe_ok": True,
                    "usable": True,
                }
            },
            "task_coverage": {"fetch_urls": {"available": True, "preferred_provider": "official_x"}},
        }


def test_clamp_limit_defaults_and_bounds():
    assert clamp_limit(None) == 20
    assert clamp_limit("bad") == 20
    assert clamp_limit(0) == 1
    assert clamp_limit(999) == 100
    assert clamp_limit(7) == 7


def test_fetch_urls_handler_limits_batch_size():
    values = [str(i) for i in range(MAX_FETCH_URLS + 1)]

    result = x_fetch_urls_handler(values, max_cost_usd=0, router=_Router())

    assert result["status"] == "needs_approval"
    assert result["reason"] == "too_many_urls"


def test_fetch_urls_handler_calls_router_with_clean_values():
    router = _Router()

    result = x_fetch_urls_handler([" 123 ", "", "https://x.com/a/status/1"], max_cost_usd=0, router=router)

    assert result["status"] == "ok"
    assert router.calls == [("fetch_urls", ["123", "https://x.com/a/status/1"])]


def test_read_user_posts_handler_clamps_limit():
    router = _Router()

    result = x_read_user_posts_handler("@alice", max_cost_usd=0, limit=999, router=router)

    assert result["status"] == "ok"
    assert result["items"][0] == {"id": "2", "text": "user"}
    assert router.calls == [("read_user_posts_recent", "@alice", 100, None)]


def test_search_posts_handler_returns_router_result():
    result = x_search_posts_handler("ai", max_cost_usd=0, limit=5, router=_Router())

    assert result["status"] == "empty"
    assert result["reason"] == "all_routes_exhausted"


def test_handler_omits_provider_raw_payloads_from_items():
    router = _Router()
    router.search_posts = lambda query, *, max_cost_usd, limit=20, cursor=None: ProviderResult.ok(  # type: ignore[method-assign]
        provider="test",
        items=[
            Post(
                id="9",
                text="search result",
                raw={"quoted_status": {"id_str": "1"}, "user": {"screen_name": "alice"}},
            )
        ],
    )

    result = x_search_posts_handler("ai", max_cost_usd=0, limit=5, router=router)

    assert result["status"] == "ok"
    assert result["items"] == [{"id": "9", "text": "search result"}]


def test_new_handlers_validate_and_call_router():
    router = _Router()

    assert x_read_thread_handler("123", max_cost_usd=0, limit=999, router=router)["status"] == "empty"
    assert x_read_replies_handler("123", max_cost_usd=0, limit=8, router=router)["status"] == "empty"
    assert x_read_quotes_handler("123", max_cost_usd=0, limit=9, router=router)["status"] == "empty"
    assert x_read_follow_graph_handler("@alice", max_cost_usd=0, graph="following", limit=10, router=router)["status"] == "empty"
    assert x_read_article_handler("123", max_cost_usd=0, router=router)["status"] == "ok"
    assert x_collect_posts_handler("ai", max_cost_usd=0, limit=9999, router=router)["status"] == "empty"

    assert router.calls == [
        ("read_thread", "123", 100, None),
        ("read_replies", "123", 8, None),
        ("read_quotes", "123", 9, None),
        ("read_follow_graph", "@alice", "following", 10, None),
        ("read_article", "123"),
        ("collect_posts", "ai", 500, None),
    ]


def test_new_handlers_reject_missing_or_invalid_inputs():
    assert x_read_thread_handler("", max_cost_usd=0, router=_Router())["reason"] == "missing_value"
    assert x_read_replies_handler("", max_cost_usd=0, router=_Router())["reason"] == "missing_value"
    assert x_read_quotes_handler("", max_cost_usd=0, router=_Router())["reason"] == "missing_value"
    assert x_read_follow_graph_handler("", max_cost_usd=0, router=_Router())["reason"] == "missing_user"
    assert x_read_follow_graph_handler("@alice", max_cost_usd=0, graph="likes", router=_Router())["reason"] == "invalid_graph"
    assert x_collect_posts_handler("", max_cost_usd=0, router=_Router())["reason"] == "missing_query"
    assert x_read_user_posts_handler("https://x.com/alice", max_cost_usd=0, router=_Router())["reason"] == "missing_user"
    assert x_read_article_handler("", max_cost_usd=0, router=_Router())["reason"] == "missing_value"
    assert x_search_posts_handler("ai", max_cost_usd=0, start_date="bad", router=_Router())["reason"] == "invalid_time_window"
    assert x_read_thread_handler("123", max_cost_usd=0, scope="sideways", router=_Router())["reason"] == "invalid_scope"


def test_search_handler_passes_cursor_and_augments_query_time_window():
    router = _Router()

    result = x_search_posts_handler(
        "ai",
        max_cost_usd=0,
        limit=5,
        cursor="cursor-1",
        start_date="2026-07-01",
        end_date="2026-07-02",
        router=router,
    )

    assert result["status"] == "empty"
    assert router.calls == [("search_posts", "ai since:2026-07-01 until:2026-07-03", 5, "cursor-1")]


def test_thread_scope_self_filters_to_root_author():
    class _ThreadRouter(_Router):
        def read_thread(self, value, *, max_cost_usd, limit=100, cursor=None):
            del value, max_cost_usd, limit, cursor
            return ProviderResult.ok(
                provider="test",
                items=[
                    Post(id="1", text="root", author=None),
                ],
            )

    result = x_read_thread_handler("123", max_cost_usd=0, scope="self", router=_ThreadRouter())

    assert result["status"] == "ok"


def test_time_window_filters_returned_posts_locally():
    class _TimeRouter(_Router):
        def read_user_posts_recent(self, user, *, max_cost_usd, limit=20, cursor=None):
            del user, max_cost_usd, limit, cursor
            return ProviderResult.ok(
                provider="test",
                items=[
                    Post(id="1", text="old", created_at="2026-07-01T00:00:00Z"),
                    Post(id="2", text="new", created_at="2026-07-10T00:00:00Z"),
                ],
            )

    result = x_read_user_posts_handler(
        "@alice",
        max_cost_usd=0,
        start_date="2026-07-05",
        end_date="2026-07-11",
        router=_TimeRouter(),
    )

    assert result["status"] == "ok"
    assert [item["id"] for item in result["items"]] == ["2"]
    assert "time_window_filtered_local" in result["warnings"]


def test_budget_is_required_for_request_handlers():
    assert x_search_posts_handler("ai", max_cost_usd=None, router=_Router())["reason"] == "missing_or_invalid_max_cost_usd"
    assert x_fetch_urls_handler(["123"], max_cost_usd=None, router=_Router())["reason"] == "missing_or_invalid_max_cost_usd"


def test_status_handler_is_token_safe_shape():
    result = x_data_status_handler(router=_Router())

    assert result["status"] == "ok"
    assert result["server"] == "x-data"
    assert result["summary"]["task_recommendations"]["fetch_urls"] == "test"
    assert "details" not in result
    assert "token" not in str(result).lower()


def test_status_handler_can_return_detailed_payload():
    result = x_data_status_handler(detail="detailed", router=_Router())

    assert result["status"] == "ok"
    assert result["details"]["providers"]["official_x"]["auth_present"] is True
    assert result["details"]["effective_routes"]["fetch_urls"] == ["test"]


def test_healthcheck_handler_returns_router_health():
    result = x_data_healthcheck_handler(mode="deep", detail="detailed", provider="official_x", router=_Router())

    assert result["status"] == "ok"
    assert result["summary"]["overall"] == "healthy"
    assert result["details"]["mode"] == "deep"
    assert result["details"]["provider"] == "official_x"
    assert result["details"]["providers"]["official_x"]["probe_ok"] is True


def test_healthcheck_handler_rejects_invalid_mode():
    result = x_data_healthcheck_handler(mode="wild", router=_Router())

    assert result["status"] == "error"
    assert result["reason"] == "invalid_healthcheck_mode"


def test_handlers_reject_invalid_detail_level():
    status = x_data_status_handler(detail="verbose", router=_Router())
    health = x_data_healthcheck_handler(detail="verbose", router=_Router())

    assert status["reason"] == "invalid_detail_level"
    assert health["reason"] == "invalid_detail_level"


def test_mcp_server_constructs():
    server = create_mcp_server(router=_Router())

    assert server is not None
