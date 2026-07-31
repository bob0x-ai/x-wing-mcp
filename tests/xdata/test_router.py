from xdata.contracts import CostEstimate, Post, ProviderResult
from xdata.providers.stub import StubProvider
from xdata.router import DEFAULT_ROUTES, XDataRouter


class _Provider:
    def __init__(self, name, result, *, estimate_cost=None):
        self.name = name
        self.result = result
        self.calls = []
        self._estimate_cost = estimate_cost

    def fetch_urls(self, values):
        self.calls.append(("fetch_urls", values))
        return self.result

    def read_user_posts(self, user, *, limit=20, cursor=None):
        self.calls.append(("read_user_posts", user, limit, cursor))
        return self.result

    def search_posts(self, query, *, limit=20, cursor=None):
        self.calls.append(("search_posts", query, limit, cursor))
        return self.result

    def read_thread(self, value, *, limit=100, cursor=None):
        self.calls.append(("read_thread", value, limit, cursor))
        return self.result

    def read_replies(self, value, *, limit=100, cursor=None):
        self.calls.append(("read_replies", value, limit, cursor))
        return self.result

    def read_quotes(self, value, *, limit=100, cursor=None):
        self.calls.append(("read_quotes", value, limit, cursor))
        return self.result

    def read_follow_graph(self, user, *, graph="followers", limit=100, cursor=None):
        self.calls.append(("read_follow_graph", user, graph, limit, cursor))
        return self.result

    def read_article(self, value):
        self.calls.append(("read_article", value))
        return self.result

    def collect_posts(self, query, *, limit=100, cursor=None):
        self.calls.append(("collect_posts", query, limit, cursor))
        return self.result

    def estimate_cost(self, task, **kwargs):
        if self._estimate_cost is not None:
            return self._estimate_cost(task, **kwargs)
        return CostEstimate(amount_usd=0.0, basis="test")


class _SearchRecentOnlyProvider:
    name = "official_x"

    def estimate_cost(self, task, **kwargs):
        del task, kwargs
        return CostEstimate(amount_usd=0.0, basis="test")

    def search_recent(self, query, *, limit=20):
        return ProviderResult.ok(
            provider=self.name,
            items=[Post(id="s1", text=f"{query}:{limit}")],
        )


def test_router_returns_first_non_empty_result():
    first = _Provider("first", ProviderResult.empty(provider="first"))
    second = _Provider("second", ProviderResult.ok(provider="second", items=[Post(id="1", text="ok")]))
    third = _Provider("third", ProviderResult.ok(provider="third", items=[Post(id="2", text="skip")]))
    router = XDataRouter(
        providers={"first": first, "second": second, "third": third},
        routes={"fetch_urls": ["first", "second", "third"]},
    )

    result = router.fetch_urls(["123"], max_cost_usd=0)

    assert result.status == "ok"
    assert result.provider == "second"
    assert result.items[0].id == "1"
    assert [attempt["provider"] for attempt in result.metadata["providers_attempted"]] == [
        "first",
        "second",
    ]
    assert third.calls == []


def test_router_continues_past_unavailable_error_and_empty():
    providers = {
        "a": _Provider("a", ProviderResult.unavailable(provider="a", reason="not_configured")),
        "b": _Provider("b", ProviderResult.error(provider="b", reason="boom")),
        "c": _Provider("c", ProviderResult.empty(provider="c")),
        "d": _Provider("d", ProviderResult.ok(provider="d", items=[Post(id="1", text="ok")])),
    }
    router = XDataRouter(providers=providers, routes={"fetch_urls": ["a", "b", "c", "d"]})

    result = router.fetch_urls(["123"], max_cost_usd=0)

    assert result.status == "ok"
    assert result.provider == "d"
    assert [attempt["status"] for attempt in result.metadata["providers_attempted"]] == [
        "unavailable",
        "error",
        "empty",
        "ok",
    ]


def test_router_stops_on_needs_approval():
    first = _Provider(
        "first",
        ProviderResult.needs_approval(
            provider="first",
            reason="budget_required",
            cost=CostEstimate(amount_usd=1.0, basis="test"),
        ),
    )
    second = _Provider("second", ProviderResult.ok(provider="second", items=[Post(id="1", text="skip")]))
    router = XDataRouter(
        providers={"first": first, "second": second},
        routes={"fetch_urls": ["first", "second"]},
    )

    result = router.fetch_urls(["123"], max_cost_usd=0)

    assert result.status == "needs_approval"
    assert result.provider == "first"
    assert result.metadata["providers_attempted"][0]["reason"] == "budget_required"
    assert second.calls == []


def test_router_returns_empty_when_all_routes_exhausted():
    router = XDataRouter(
        providers={"stub": StubProvider("stub")},
        routes={"fetch_urls": ["stub"]},
    )

    result = router.fetch_urls(["123"], max_cost_usd=0)

    assert result.status == "empty"
    assert result.provider == "router"
    assert result.reason == "all_routes_exhausted"
    assert result.metadata["providers_attempted"][0]["reason"] == "not_implemented"


def test_unknown_task_returns_error():
    result = XDataRouter(providers={}, routes={}).run_task("nope", max_cost_usd=0)

    assert result.status == "error"
    assert result.reason == "unknown_task"


def test_router_uses_search_recent_fallback_method_name():
    router = XDataRouter(
        providers={"official_x": _SearchRecentOnlyProvider()},
        routes={"search_posts": ["official_x"]},
    )

    result = router.search_posts("ai", max_cost_usd=0, limit=5)

    assert result.status == "ok"
    assert result.items[0].text == "ai:5"


def test_read_user_posts_passes_user_and_limit():
    provider = _Provider("p", ProviderResult.ok(provider="p", items=[Post(id="1", text="ok")]))
    router = XDataRouter(
        providers={"p": provider},
        routes={"read_user_posts_recent": ["p"]},
    )

    result = router.read_user_posts_recent("@alice", max_cost_usd=0, limit=7)

    assert result.status == "ok"
    assert provider.calls == [("read_user_posts", "@alice", 7, None)]


def test_new_matrix_tasks_route_to_provider_methods():
    provider = _Provider("p", ProviderResult.ok(provider="p", items=[Post(id="1", text="ok")]))
    router = XDataRouter(
        providers={"p": provider},
        routes={
            "read_thread": ["p"],
            "read_replies": ["p"],
            "read_quotes": ["p"],
            "read_follow_graph": ["p"],
            "read_article": ["p"],
            "collect_posts": ["p"],
        },
    )

    assert router.read_thread("123", max_cost_usd=0, limit=11).status == "ok"
    assert router.read_replies("123", max_cost_usd=0, limit=12).status == "ok"
    assert router.read_quotes("123", max_cost_usd=0, limit=13).status == "ok"
    assert router.read_follow_graph("@alice", max_cost_usd=0, graph="following", limit=14).status == "ok"
    assert router.read_article("123", max_cost_usd=0).status == "ok"
    assert router.collect_posts("ai", max_cost_usd=0, limit=15).status == "ok"

    assert provider.calls == [
        ("read_thread", "123", 11, None),
        ("read_replies", "123", 12, None),
        ("read_quotes", "123", 13, None),
        ("read_follow_graph", "@alice", "following", 14, None),
        ("read_article", "123"),
        ("collect_posts", "ai", 15, None),
    ]


def test_cursor_is_passed_when_provider_supports_it():
    provider = _Provider("p", ProviderResult.ok(provider="p", items=[Post(id="1", text="ok")]))
    router = XDataRouter(
        providers={"p": provider},
        routes={"search_posts": ["p"]},
    )

    result = router.search_posts("ai", max_cost_usd=0, limit=5, cursor="cursor-1")

    assert result.status == "ok"
    assert provider.calls == [("search_posts", "ai", 5, "cursor-1")]


def test_cursor_is_ignored_for_provider_methods_without_support():
    class _NoCursorProvider:
        name = "nocursor"

        def estimate_cost(self, task, **kwargs):
            del task, kwargs
            return CostEstimate(amount_usd=0.0, basis="test")

        def search_posts(self, query, *, limit=20):
            return ProviderResult.ok(provider=self.name, items=[Post(id="1", text=f"{query}:{limit}")])

    router = XDataRouter(
        providers={"nocursor": _NoCursorProvider()},
        routes={"search_posts": ["nocursor"]},
    )

    result = router.search_posts("ai", max_cost_usd=0, limit=5, cursor="cursor-1")

    assert result.status == "ok"
    assert result.items[0].text == "ai:5"


def test_new_matrix_tasks_return_stubbed_empty_by_default():
    router = XDataRouter()

    assert router.read_thread("123", max_cost_usd=0).status == "needs_approval"
    assert router.read_replies("123", max_cost_usd=0).status == "needs_approval"
    assert router.read_quotes("123", max_cost_usd=0).status == "needs_approval"
    assert router.read_follow_graph("@alice", max_cost_usd=0).status == "needs_approval"
    assert router.read_article("123", max_cost_usd=0).status == "needs_approval"
    assert router.collect_posts("ai", max_cost_usd=0).status == "needs_approval"


def test_default_recent_user_posts_route_prefers_socialdata():
    assert DEFAULT_ROUTES["read_user_posts_recent"][0] == "socialdata"
    assert DEFAULT_ROUTES["read_user_posts_recent"][1] == "syndication"


def test_status_reports_effective_routes_and_preferred_providers():
    router = XDataRouter(
        providers={
            "socialdata": _Provider("socialdata", ProviderResult.empty(provider="socialdata")),
            "stub": StubProvider("stub"),
        },
        routes={
            "search_posts": ["socialdata", "stub"],
            "collect_posts": ["stub"],
        },
    )

    status = router.status()

    assert status["effective_routes"]["search_posts"] == ["socialdata"]
    assert status["effective_routes"]["collect_posts"] == []
    assert status["preferred_providers"]["search_posts"] == "socialdata"
    assert status["preferred_providers"]["collect_posts"] is None
    assert status["task_coverage"]["search_posts"]["preferred_provider"] == "socialdata"


def test_healthcheck_reports_provider_and_task_coverage():
    provider = _Provider("socialdata", ProviderResult.ok(provider="socialdata", items=[Post(id="1", text="ok")]))
    provider.status = lambda: {
        "auth_required": True,
        "auth_present": True,
        "supports_tasks": ["search_posts", "read_user_posts_recent"],
    }
    router = XDataRouter(
        providers={"socialdata": provider, "stub": StubProvider("stub")},
        routes={
            "search_posts": ["socialdata", "stub"],
            "read_thread": ["stub"],
        },
    )

    health = router.healthcheck(mode="live")

    assert health["providers"]["socialdata"]["probe_ok"] is True
    assert health["providers"]["socialdata"]["usable"] is True
    assert health["task_coverage"]["search_posts"]["preferred_provider"] == "socialdata"
    assert health["task_coverage"]["read_thread"]["available"] is False


def test_router_skips_budget_blocked_provider_and_continues():
    paid = _Provider(
        "paid",
        ProviderResult.ok(provider="paid", items=[Post(id="p1", text="paid")]),
        estimate_cost=lambda task, **kwargs: CostEstimate(amount_usd=1.0, basis=task),
    )
    free = _Provider("free", ProviderResult.ok(provider="free", items=[Post(id="f1", text="free")]))
    router = XDataRouter(providers={"paid": paid, "free": free}, routes={"search_posts": ["paid", "free"]})

    result = router.search_posts("ai", max_cost_usd=0, limit=5)

    assert result.status == "ok"
    assert result.provider == "free"
    assert result.metadata["providers_attempted"][0]["reason"] == "budget_exceeded"
    assert result.metadata["providers_attempted"][0]["estimated_cost_usd"] == 1.0


def test_router_returns_needs_approval_when_all_paths_exceed_budget():
    paid = _Provider(
        "paid",
        ProviderResult.ok(provider="paid", items=[Post(id="p1", text="paid")]),
        estimate_cost=lambda task, **kwargs: CostEstimate(amount_usd=2.0, basis=task),
    )
    router = XDataRouter(providers={"paid": paid}, routes={"search_posts": ["paid"]})

    result = router.search_posts("ai", max_cost_usd=0.5, limit=5)

    assert result.status == "needs_approval"
    assert result.provider == "router"
    assert result.reason == "budget_exceeded"
    assert result.metadata["max_cost_usd"] == 0.5


def test_router_rejects_missing_budget():
    router = XDataRouter(providers={}, routes={"search_posts": []})

    result = router.run_task("search_posts", query="ai")

    assert result.status == "error"
    assert result.reason == "missing_or_invalid_max_cost_usd"
