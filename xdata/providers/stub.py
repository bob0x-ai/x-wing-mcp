"""Stub provider for planned-but-unimplemented backends."""

from __future__ import annotations

from xdata.contracts import ProviderResult


class StubProvider:
    """Return explicit unavailable results for unimplemented providers."""

    def __init__(self, name: str) -> None:
        self.name = name

    def fetch_urls(self, values: list[str]) -> ProviderResult:
        del values
        return self._not_implemented("fetch_urls")

    def read_user_posts(self, user: str, *, limit: int = 20) -> ProviderResult:
        del user, limit
        return self._not_implemented("read_user_posts")

    def search_posts(self, query: str, *, limit: int = 20) -> ProviderResult:
        del query, limit
        return self._not_implemented("search_posts")

    def read_owned_timeline(self, *, limit: int = 20) -> ProviderResult:
        del limit
        return self._not_implemented("read_owned_timeline")

    def read_mentions(self, *, limit: int = 20) -> ProviderResult:
        del limit
        return self._not_implemented("read_mentions")

    def read_thread(self, value: str, *, limit: int = 100) -> ProviderResult:
        del value, limit
        return self._not_implemented("read_thread")

    def read_replies(self, value: str, *, limit: int = 100) -> ProviderResult:
        del value, limit
        return self._not_implemented("read_replies")

    def read_quotes(self, value: str, *, limit: int = 100) -> ProviderResult:
        del value, limit
        return self._not_implemented("read_quotes")

    def read_follow_graph(self, user: str, *, graph: str = "followers", limit: int = 100) -> ProviderResult:
        del user, graph, limit
        return self._not_implemented("read_follow_graph")

    def read_article(self, value: str) -> ProviderResult:
        del value
        return self._not_implemented("read_article")

    def collect_posts(self, query: str, *, limit: int = 100) -> ProviderResult:
        del query, limit
        return self._not_implemented("collect_posts")

    def estimate_cost(self, task: str, **kwargs) -> None:
        del task, kwargs
        return None

    def _not_implemented(self, task: str) -> ProviderResult:
        return ProviderResult.unavailable(
            provider=self.name,
            reason="not_implemented",
            metadata={"task": task},
        )
