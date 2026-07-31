"""Sequential task router for X data providers."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import inspect
from typing import Any

from xdata.config import load_config
from xdata.contracts import CostEstimate, ProviderResult
from xdata.diagnostics import provider_health_report, provider_status_report, task_coverage_summary
from xdata.providers.getxapi import GetXApiProvider
from xdata.providers.official_x import OfficialXProvider
from xdata.providers.socialdata import SocialDataProvider
from xdata.providers.stub import StubProvider
from xdata.providers.syndication import SyndicationProvider
from xdata.providers.twikit import TwikitProvider

DEFAULT_ROUTES: dict[str, list[str]] = load_config()["routes"]


TASK_METHODS: dict[str, tuple[str, ...]] = {
    "fetch_urls": ("fetch_urls",),
    "read_user_posts_recent": ("read_user_posts",),
    "search_posts": ("search_posts", "search_recent"),
    "read_owned_timeline": ("read_owned_timeline",),
    "read_mentions": ("read_mentions",),
    "read_thread": ("read_thread",),
    "read_replies": ("read_replies",),
    "read_quotes": ("read_quotes",),
    "read_follow_graph": ("read_follow_graph",),
    "read_article": ("read_article",),
    "collect_posts": ("collect_posts",),
}


TERMINAL_STATUSES = {"needs_approval"}
CONTINUE_STATUSES = {"empty", "unavailable", "error"}


@dataclass(frozen=True)
class ProviderAttempt:
    provider: str
    status: str
    reason: str | None
    items: int
    estimated_cost_usd: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "status": self.status,
            "reason": self.reason,
            "items": self.items,
            "estimated_cost_usd": self.estimated_cost_usd,
        }


class XDataRouter:
    def __init__(
        self,
        *,
        providers: Mapping[str, Any] | None = None,
        routes: Mapping[str, list[str]] | None = None,
        config: dict[str, Any] | None = None,
    ) -> None:
        self.config = config or load_config()
        self.routes = dict(routes or self.config["routes"] or DEFAULT_ROUTES)
        self.providers = dict(providers or default_providers(self.config))

    def fetch_urls(self, values: list[str], *, max_cost_usd: float) -> ProviderResult:
        return self.run_task("fetch_urls", values=values, max_cost_usd=max_cost_usd)

    def read_user_posts_recent(
        self,
        user: str,
        *,
        max_cost_usd: float,
        limit: int = 20,
        cursor: str | None = None,
    ) -> ProviderResult:
        return self.run_task("read_user_posts_recent", user=user, limit=limit, cursor=cursor, max_cost_usd=max_cost_usd)

    def search_posts(
        self,
        query: str,
        *,
        max_cost_usd: float,
        limit: int = 20,
        cursor: str | None = None,
    ) -> ProviderResult:
        return self.run_task("search_posts", query=query, limit=limit, cursor=cursor, max_cost_usd=max_cost_usd)

    def read_owned_timeline(self, *, max_cost_usd: float, limit: int = 20) -> ProviderResult:
        return self.run_task("read_owned_timeline", limit=limit, max_cost_usd=max_cost_usd)

    def read_mentions(self, *, max_cost_usd: float, limit: int = 20) -> ProviderResult:
        return self.run_task("read_mentions", limit=limit, max_cost_usd=max_cost_usd)

    def read_thread(
        self,
        value: str,
        *,
        max_cost_usd: float,
        limit: int = 100,
        cursor: str | None = None,
    ) -> ProviderResult:
        return self.run_task("read_thread", value=value, limit=limit, cursor=cursor, max_cost_usd=max_cost_usd)

    def read_replies(
        self,
        value: str,
        *,
        max_cost_usd: float,
        limit: int = 100,
        cursor: str | None = None,
    ) -> ProviderResult:
        return self.run_task("read_replies", value=value, limit=limit, cursor=cursor, max_cost_usd=max_cost_usd)

    def read_quotes(
        self,
        value: str,
        *,
        max_cost_usd: float,
        limit: int = 100,
        cursor: str | None = None,
    ) -> ProviderResult:
        return self.run_task("read_quotes", value=value, limit=limit, cursor=cursor, max_cost_usd=max_cost_usd)

    def read_follow_graph(
        self,
        user: str,
        *,
        max_cost_usd: float,
        graph: str = "followers",
        limit: int = 100,
        cursor: str | None = None,
    ) -> ProviderResult:
        return self.run_task(
            "read_follow_graph",
            user=user,
            graph=graph,
            limit=limit,
            cursor=cursor,
            max_cost_usd=max_cost_usd,
        )

    def read_article(self, value: str, *, max_cost_usd: float) -> ProviderResult:
        return self.run_task("read_article", value=value, max_cost_usd=max_cost_usd)

    def collect_posts(
        self,
        query: str,
        *,
        max_cost_usd: float,
        limit: int = 100,
        cursor: str | None = None,
    ) -> ProviderResult:
        return self.run_task("collect_posts", query=query, limit=limit, cursor=cursor, max_cost_usd=max_cost_usd)

    def status(self) -> dict[str, Any]:
        provider_status: dict[str, Any] = {}
        for name, provider in self.providers.items():
            provider_status[name] = provider_status_report(name, provider)
        effective_routes = {
            task: [
                provider_name
                for provider_name in route
                if not isinstance(self.providers.get(provider_name) or StubProvider(provider_name), StubProvider)
            ]
            for task, route in self.routes.items()
        }
        return {
            "providers": provider_status,
            "routes": self.routes,
            "effective_routes": effective_routes,
            "preferred_providers": {
                task: (effective_routes[task][0] if effective_routes[task] else None)
                for task in self.routes
            },
            "task_coverage": task_coverage_summary(self.routes, provider_status),
            "config_path": self.config.get("config_path"),
            "tasks": sorted(self.routes),
        }

    def healthcheck(self, *, mode: str = "live", provider: str | None = None) -> dict[str, Any]:
        selected = (
            {provider: self.providers.get(provider) or StubProvider(provider)}
            if provider
            else self.providers
        )
        reports = {
            name: provider_health_report(name, active_provider, mode=mode)
            for name, active_provider in selected.items()
        }
        return {
            "mode": mode,
            "provider": provider,
            "providers": reports,
            "task_coverage": task_coverage_summary(self.routes, reports),
            "config_path": self.config.get("config_path"),
        }

    def run_task(self, task: str, **kwargs: Any) -> ProviderResult:
        max_cost_usd = _coerce_budget(kwargs.get("max_cost_usd"))
        if max_cost_usd is None:
            return ProviderResult.error(
                provider="router",
                reason="missing_or_invalid_max_cost_usd",
                metadata={"task": task},
            )
        if task not in self.routes:
            return ProviderResult.error(
                provider="router",
                reason="unknown_task",
                metadata={"task": task},
            )
        if task not in TASK_METHODS:
            return ProviderResult.error(
                provider="router",
                reason="unmapped_task",
                metadata={"task": task},
            )

        attempts: list[ProviderAttempt] = []
        route = self.routes[task]
        budget_blocked: list[ProviderAttempt] = []
        for provider_name in route:
            provider = self.providers.get(provider_name) or StubProvider(provider_name)
            estimate = self._estimate_provider_cost(task, provider, kwargs)
            if estimate is not None and estimate.amount_usd > max_cost_usd:
                result = ProviderResult.needs_approval(
                    provider=getattr(provider, "name", provider.__class__.__name__),
                    reason="budget_exceeded",
                    cost=estimate,
                    metadata={"task": task, "max_cost_usd": max_cost_usd},
                )
            elif estimate is None and not isinstance(provider, StubProvider):
                result = ProviderResult.needs_approval(
                    provider=getattr(provider, "name", provider.__class__.__name__),
                    reason="cost_unknown",
                    metadata={"task": task, "max_cost_usd": max_cost_usd},
                )
            else:
                result = self._call_provider(task, provider, kwargs)
            attempts.append(
                ProviderAttempt(
                    provider=result.provider,
                    status=result.status,
                    reason=result.reason,
                    items=len(result.items),
                    estimated_cost_usd=estimate.amount_usd if estimate else None,
                )
            )

            if result.status == "ok" and result.items:
                return _with_attempts(result, attempts, max_cost_usd=max_cost_usd, estimated_cost=estimate)
            if result.status == "needs_approval" and result.reason in {"budget_exceeded", "cost_unknown"}:
                budget_blocked.append(attempts[-1])
                continue
            if result.status in TERMINAL_STATUSES:
                return _with_attempts(result, attempts, max_cost_usd=max_cost_usd, estimated_cost=estimate)
            if result.status in CONTINUE_STATUSES:
                continue

            return _with_attempts(
                ProviderResult.error(
                    provider="router",
                    reason=f"unexpected_provider_status:{result.status}",
                    metadata={"task": task},
                ),
                attempts,
                max_cost_usd=max_cost_usd,
            )

        if budget_blocked:
            blocking = min(
                (attempt for attempt in budget_blocked if attempt.estimated_cost_usd is not None),
                key=lambda attempt: attempt.estimated_cost_usd,
                default=None,
            )
            return ProviderResult.needs_approval(
                provider="router",
                reason="budget_exceeded",
                cost=CostEstimate(
                    amount_usd=blocking.estimated_cost_usd if blocking and blocking.estimated_cost_usd is not None else 0.0,
                    basis="lowest estimated paid/provider cost that exceeded the request budget",
                ) if blocking else None,
                metadata={
                    "task": task,
                    "max_cost_usd": max_cost_usd,
                    "providers_attempted": [attempt.to_dict() for attempt in attempts],
                },
            )
        return ProviderResult.empty(
            provider="router",
            reason="all_routes_exhausted",
            metadata={
                "task": task,
                "providers_attempted": [attempt.to_dict() for attempt in attempts],
                "max_cost_usd": max_cost_usd,
            },
        )

    def _call_provider(self, task: str, provider: Any, kwargs: dict[str, Any]) -> ProviderResult:
        for method_name in TASK_METHODS[task]:
            method = getattr(provider, method_name, None)
            if method is None:
                continue
            try:
                return method(**_filter_supported_kwargs(method, _task_kwargs(task, kwargs)))
            except Exception as exc:
                return ProviderResult.error(
                    provider=getattr(provider, "name", provider.__class__.__name__),
                    reason="provider_exception",
                    warnings=[str(exc)],
                    metadata={"task": task},
                )
        return ProviderResult.unavailable(
            provider=getattr(provider, "name", provider.__class__.__name__),
            reason="not_implemented",
            metadata={"task": task},
        )

    def _estimate_provider_cost(self, task: str, provider: Any, kwargs: dict[str, Any]) -> CostEstimate | None:
        estimate_method = getattr(provider, "estimate_cost", None)
        if not callable(estimate_method):
            return None
        try:
            return estimate_method(task, **_filter_supported_kwargs(estimate_method, _task_kwargs(task, kwargs)))
        except Exception:
            return None


def default_providers(config: dict[str, Any] | None = None) -> dict[str, Any]:
    config = config or load_config()
    provider_config = config.get("providers", {})
    return {
        "syndication": _build_syndication_provider(provider_config.get("syndication", {})),
        "official_x": _build_official_x_provider(provider_config.get("official_x", {})),
        "socialdata": _build_socialdata_provider(provider_config.get("socialdata", {})),
        "xpoz": _build_stub_provider("xpoz", provider_config.get("xpoz", {})),
        "getxapi": _build_getxapi_provider(provider_config.get("getxapi", {})),
        "twikit": _build_twikit_provider(provider_config.get("twikit", {})),
        "twscrape": _build_stub_provider("twscrape", provider_config.get("twscrape", {})),
        "apify": _build_stub_provider("apify", provider_config.get("apify", {})),
        "xactions": _build_stub_provider("xactions", provider_config.get("xactions", {})),
    }


def _build_syndication_provider(config: dict[str, Any]) -> Any:
    if config.get("enabled", True) is False:
        return StubProvider("syndication")
    rate_limit = config.get("rate_limit", {})
    return SyndicationProvider(
        cooldown_seconds=int(config.get("cooldown_seconds", 60)),
        requests_per_minute=_float_or_default(rate_limit.get("requests_per_minute"), 12),
        min_interval_seconds=_float_or_none(rate_limit.get("min_interval_seconds")),
        jitter_seconds=_float_or_default(rate_limit.get("jitter_seconds"), 0.35),
    )


def _build_socialdata_provider(config: dict[str, Any]) -> Any:
    if config.get("enabled", True) is False:
        return StubProvider("socialdata")
    rate_limit = config.get("rate_limit", {})
    return SocialDataProvider(
        cooldown_seconds=int(config.get("cooldown_seconds", 60)),
        requests_per_minute=_float_or_default(rate_limit.get("requests_per_minute"), 20),
        min_interval_seconds=_float_or_none(rate_limit.get("min_interval_seconds")),
        jitter_seconds=_float_or_default(rate_limit.get("jitter_seconds"), 0.25),
    )


def _build_official_x_provider(config: dict[str, Any]) -> Any:
    if config.get("enabled", True) is False:
        return StubProvider("official_x")
    return OfficialXProvider()


def _build_getxapi_provider(config: dict[str, Any]) -> Any:
    if config.get("enabled", True) is False:
        return StubProvider("getxapi")
    rate_limit = config.get("rate_limit", {})
    return GetXApiProvider(
        cooldown_seconds=int(config.get("cooldown_seconds", 60)),
        requests_per_minute=_float_or_default(rate_limit.get("requests_per_minute"), 20),
        min_interval_seconds=_float_or_none(rate_limit.get("min_interval_seconds")),
        jitter_seconds=_float_or_default(rate_limit.get("jitter_seconds"), 0.2),
    )


def _build_twikit_provider(config: dict[str, Any]) -> Any:
    if config.get("enabled", False) is False:
        return StubProvider("twikit")
    rate_limit = config.get("rate_limit", {})
    return TwikitProvider(
        cookies_file=str(config.get("cookies_file") or ""),
        locale=str(config.get("locale") or "en-US"),
        cooldown_seconds=int(config.get("cooldown_seconds", 300)),
        requests_per_minute=_float_or_default(rate_limit.get("requests_per_minute"), 6),
        min_interval_seconds=_float_or_none(rate_limit.get("min_interval_seconds")),
        jitter_seconds=_float_or_default(rate_limit.get("jitter_seconds"), 0.75),
    )


def _build_stub_provider(name: str, config: dict[str, Any]) -> Any:
    if config.get("enabled", False) is True:
        return StubProvider(name)
    return StubProvider(name)


def _float_or_default(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _float_or_none(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _task_kwargs(task: str, kwargs: dict[str, Any]) -> dict[str, Any]:
    if task == "fetch_urls":
        return {"values": kwargs["values"]}
    if task == "read_user_posts_recent":
        return {"user": kwargs["user"], "limit": kwargs.get("limit", 20), "cursor": kwargs.get("cursor")}
    if task == "search_posts":
        return {"query": kwargs["query"], "limit": kwargs.get("limit", 20), "cursor": kwargs.get("cursor")}
    if task in {"read_owned_timeline", "read_mentions"}:
        return {"limit": kwargs.get("limit", 20)}
    if task in {"read_thread", "read_replies", "read_quotes"}:
        return {"value": kwargs["value"], "limit": kwargs.get("limit", 100), "cursor": kwargs.get("cursor")}
    if task == "read_follow_graph":
        return {
            "user": kwargs["user"],
            "graph": kwargs.get("graph", "followers"),
            "limit": kwargs.get("limit", 100),
            "cursor": kwargs.get("cursor"),
        }
    if task == "read_article":
        return {"value": kwargs["value"]}
    if task == "collect_posts":
        return {"query": kwargs["query"], "limit": kwargs.get("limit", 100), "cursor": kwargs.get("cursor")}
    return kwargs


def _filter_supported_kwargs(method: Any, kwargs: dict[str, Any]) -> dict[str, Any]:
    try:
        signature = inspect.signature(method)
    except (TypeError, ValueError):
        return {key: value for key, value in kwargs.items() if value is not None}
    if any(parameter.kind == inspect.Parameter.VAR_KEYWORD for parameter in signature.parameters.values()):
        return {key: value for key, value in kwargs.items() if value is not None}
    allowed = {
        name
        for name, parameter in signature.parameters.items()
        if parameter.kind in {inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.KEYWORD_ONLY}
    }
    return {
        key: value
        for key, value in kwargs.items()
        if key in allowed and value is not None
    }


def _with_attempts(
    result: ProviderResult,
    attempts: list[ProviderAttempt],
    *,
    max_cost_usd: float | None = None,
    estimated_cost: CostEstimate | None = None,
) -> ProviderResult:
    metadata = {
        **result.metadata,
        "providers_attempted": [attempt.to_dict() for attempt in attempts],
    }
    if max_cost_usd is not None:
        metadata["max_cost_usd"] = max_cost_usd
    if estimated_cost is not None:
        metadata["estimated_cost_usd"] = estimated_cost.amount_usd
    return ProviderResult(
        status=result.status,
        provider=result.provider,
        items=result.items,
        reason=result.reason,
        warnings=result.warnings,
        cost=result.cost,
        raw_ref=result.raw_ref,
        metadata=metadata,
    )


def _coerce_budget(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if parsed < 0:
        return None
    return parsed
