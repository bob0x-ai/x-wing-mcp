"""MCP server wrapper for the X data router."""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from typing import Annotated, Any, Literal

from pydantic import Field
from xdata.config import load_config
from xdata.contracts import Article, Post, ProviderResult, UserProfile
from xdata.diagnostics import doctor_summary_from_healthcheck, doctor_summary_from_status
from xdata.providers.syndication import normalize_handle
from xdata.router import XDataRouter

_SERVER_CONFIG = load_config()["server"]
DEFAULT_LIMIT = int(_SERVER_CONFIG["default_limit"])
MAX_LIMIT = int(_SERVER_CONFIG["max_limit"])
MAX_FETCH_URLS = int(_SERVER_CONFIG["max_fetch_urls"])
MAX_COLLECT_LIMIT = int(_SERVER_CONFIG["max_collect_limit"])
MAX_GRAPH_LIMIT = max(MAX_LIMIT, 1000)
FOLLOW_GRAPHS = {"followers", "following"}
HEALTHCHECK_MODES = {"basic", "live", "deep"}
DETAIL_LEVELS = {"summary", "detailed"}
DetailLevel = Literal["summary", "detailed"]
HealthcheckMode = Literal["basic", "live", "deep"]
FollowGraph = Literal["followers", "following"]
ThreadScope = Literal["conversation", "self"]
MaxCostUsd = Annotated[
    float,
    Field(
        description=(
            "Required. Hard spend cap in USD for this request. The router never exceeds it. "
            "Use `0` only for strictly zero-cost routes. Use a small positive value such as `0.01` "
            "or `0.10` when paid fallbacks are allowed. If every usable backend would cost more, "
            "the call returns `needs_approval` instead of escalating."
        ),
        ge=0,
    ),
]
CursorParam = Annotated[
    str | None,
    Field(
        description=(
            "Opaque pagination cursor from `metadata.next_cursor` in a previous response. "
            "Leave unset for the first page."
        )
    ),
]
TimeBoundParam = Annotated[
    str | None,
    Field(
        description=(
            "Optional UTC time bound in ISO 8601 (`2026-07-10T00:00:00Z`) or date-only "
            "(`2026-07-10`). Date-only values are interpreted in UTC."
        )
    ),
]


def clamp_limit(limit: int | None, *, default: int = DEFAULT_LIMIT, maximum: int = MAX_LIMIT) -> int:
    if limit is None:
        return default
    try:
        parsed = int(limit)
    except (TypeError, ValueError):
        return default
    return max(1, min(parsed, maximum))


def _result(result: ProviderResult) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "status": result.status,
        "provider": result.provider,
        "items": [_serialize_item(item) for item in result.items],
    }
    if result.reason is not None:
        payload["reason"] = result.reason
    if result.warnings:
        payload["warnings"] = list(result.warnings)
    if result.cost is not None:
        payload["cost"] = asdict(result.cost)
    if result.raw_ref is not None:
        payload["raw_ref"] = result.raw_ref
    if result.metadata:
        payload["metadata"] = dict(result.metadata)
    return payload


def _serialize_item(item: Any) -> dict[str, Any]:
    if isinstance(item, Post):
        payload = {
            "id": item.id,
            "text": item.text,
            "author": asdict(item.author) if item.author is not None else None,
            "created_at": item.created_at,
            "metrics": asdict(item.metrics) if item.metrics is not None else None,
            "source_url": item.source_url,
        }
        return {key: value for key, value in payload.items() if value is not None}
    if isinstance(item, Article):
        payload = {
            "id": item.id,
            "title": item.title,
            "body_text": item.body_text,
            "author": asdict(item.author) if item.author is not None else None,
            "created_at": item.created_at,
            "metrics": asdict(item.metrics) if item.metrics is not None else None,
            "preview_text": item.preview_text,
            "cover_image_url": item.cover_image_url,
            "source_url": item.source_url,
            "images": [asdict(image) for image in item.images],
            "blocks": [asdict(block) for block in item.blocks],
        }
        return {key: value for key, value in payload.items() if value not in (None, [], "")}
    if isinstance(item, UserProfile):
        payload = {
            "id": item.id,
            "username": item.username,
            "name": item.name,
            "description": item.description,
            "public_metrics": item.public_metrics,
            "source_url": item.source_url,
        }
        return {key: value for key, value in payload.items() if value is not None}
    if hasattr(item, "__dict__"):
        data = dict(item.__dict__)
    elif isinstance(item, dict):
        data = dict(item)
    else:
        return {"value": item}
    data.pop("raw", None)
    return data


def validate_max_cost_usd(value: float | int | str | None) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if parsed < 0:
        return None
    return parsed


def _optional_text(value: str | None) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _normalize_time_bound(value: str | None, *, end_of_day: bool) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    if len(text) == 10:
        base = datetime.fromisoformat(text)
        if end_of_day:
            base = base + timedelta(days=1) - timedelta(microseconds=1)
        return base.replace(tzinfo=timezone.utc)
    normalized = text.replace("Z", "+00:00")
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _time_window(
    start_date: str | None,
    end_date: str | None,
) -> tuple[datetime | None, datetime | None, str | None]:
    try:
        start = _normalize_time_bound(start_date, end_of_day=False)
        end = _normalize_time_bound(end_date, end_of_day=True)
    except ValueError:
        return None, None, "invalid_time_window"
    if start and end and start > end:
        return None, None, "invalid_time_window"
    return start, end, None


def _post_created_at(value: str | None) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _apply_time_window(
    result: ProviderResult,
    *,
    start_date: str | None = None,
    end_date: str | None = None,
) -> ProviderResult:
    if not start_date and not end_date:
        return result
    if result.status != "ok" or not result.items:
        return result
    start, end, error = _time_window(start_date, end_date)
    if error:
        return ProviderResult.error(provider="mcp", reason=error)
    filtered = []
    for item in result.items:
        if not isinstance(item, Post):
            filtered.append(item)
            continue
        created_at = _post_created_at(item.created_at)
        if created_at is None:
            continue
        if start and created_at < start:
            continue
        if end and created_at > end:
            continue
        filtered.append(item)
    metadata = {
        **result.metadata,
        "time_window": {"start_date": start_date, "end_date": end_date},
        "time_window_filtered_local": True,
    }
    if filtered:
        return ProviderResult(
            status="ok",
            provider=result.provider,
            items=filtered,
            reason=result.reason,
            warnings=sorted(set([*result.warnings, "time_window_filtered_local"])),
            cost=result.cost,
            raw_ref=result.raw_ref,
            metadata=metadata,
        )
    return ProviderResult(
        status="empty",
        provider=result.provider,
        items=[],
        reason="time_window_no_results",
        warnings=sorted(set([*result.warnings, "time_window_filtered_local"])),
        cost=result.cost,
        raw_ref=result.raw_ref,
        metadata=metadata,
    )


def _apply_thread_scope(result: ProviderResult, *, scope: str) -> ProviderResult:
    if scope != "self" or result.status != "ok" or not result.items:
        return result
    root = result.items[0]
    if not isinstance(root, Post) or root.author is None:
        return result
    root_author_id = root.author.id
    root_author_username = root.author.username
    filtered = []
    for item in result.items:
        if not isinstance(item, Post) or item.author is None:
            continue
        same_author = (
            root_author_id is not None
            and item.author.id == root_author_id
        ) or (
            root_author_id is None
            and root_author_username is not None
            and item.author.username == root_author_username
        )
        if same_author:
            filtered.append(item)
    metadata = {**result.metadata, "scope": scope}
    return ProviderResult(
        status="ok" if filtered else "empty",
        provider=result.provider,
        items=filtered,
        reason=result.reason if filtered else "scope_no_results",
        warnings=sorted(set([*result.warnings, "scope_filtered_self"])),
        cost=result.cost,
        raw_ref=result.raw_ref,
        metadata=metadata,
    )


def _augment_query_with_time_bounds(query: str, *, start_date: str | None = None, end_date: str | None = None) -> str:
    if not start_date and not end_date:
        return query
    start, end, error = _time_window(start_date, end_date)
    if error:
        return ""
    parts = [query.strip()]
    if start:
        parts.append(f"since:{start.date().isoformat()}")
    if end:
        parts.append(f"until:{(end + timedelta(microseconds=1)).date().isoformat()}")
    return " ".join(part for part in parts if part)


def x_fetch_urls_handler(
    values: list[str],
    *,
    max_cost_usd: float | int | str | None,
    router: XDataRouter | None = None,
) -> dict[str, Any]:
    router = router or XDataRouter()
    budget = validate_max_cost_usd(max_cost_usd)
    if budget is None:
        return _result(ProviderResult.error(provider="mcp", reason="missing_or_invalid_max_cost_usd"))
    clean_values = [str(value).strip() for value in values or [] if str(value).strip()]
    if not clean_values:
        return _result(ProviderResult.error(provider="mcp", reason="missing_values"))
    if len(clean_values) > MAX_FETCH_URLS:
        return _result(
            ProviderResult.needs_approval(
                provider="mcp",
                reason="too_many_urls",
                metadata={"max": MAX_FETCH_URLS, "requested": len(clean_values)},
            )
        )
    return _result(router.fetch_urls(clean_values, max_cost_usd=budget))


def x_read_user_posts_handler(
    user: str,
    *,
    max_cost_usd: float | int | str | None,
    limit: int | None = DEFAULT_LIMIT,
    cursor: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    router: XDataRouter | None = None,
) -> dict[str, Any]:
    router = router or XDataRouter()
    budget = validate_max_cost_usd(max_cost_usd)
    if budget is None:
        return _result(ProviderResult.error(provider="mcp", reason="missing_or_invalid_max_cost_usd"))
    if _time_window(start_date, end_date)[2]:
        return _result(ProviderResult.error(provider="mcp", reason="invalid_time_window"))
    user = normalize_handle(user)
    if not user:
        return _result(ProviderResult.error(provider="mcp", reason="missing_user"))
    result = router.read_user_posts_recent(
        f"@{user}",
        max_cost_usd=budget,
        limit=clamp_limit(limit),
        cursor=_optional_text(cursor),
    )
    return _result(_apply_time_window(result, start_date=start_date, end_date=end_date))


def x_search_posts_handler(
    query: str,
    *,
    max_cost_usd: float | int | str | None,
    limit: int | None = DEFAULT_LIMIT,
    cursor: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    router: XDataRouter | None = None,
) -> dict[str, Any]:
    router = router or XDataRouter()
    budget = validate_max_cost_usd(max_cost_usd)
    if budget is None:
        return _result(ProviderResult.error(provider="mcp", reason="missing_or_invalid_max_cost_usd"))
    query = str(query or "").strip()
    if not query:
        return _result(ProviderResult.error(provider="mcp", reason="missing_query"))
    query = _augment_query_with_time_bounds(query, start_date=start_date, end_date=end_date)
    if not query:
        return _result(ProviderResult.error(provider="mcp", reason="invalid_time_window"))
    result = router.search_posts(
        query,
        max_cost_usd=budget,
        limit=clamp_limit(limit),
        cursor=_optional_text(cursor),
    )
    return _result(_apply_time_window(result, start_date=start_date, end_date=end_date))


def x_read_owned_timeline_handler(
    *,
    max_cost_usd: float | int | str | None,
    limit: int | None = DEFAULT_LIMIT,
    start_date: str | None = None,
    end_date: str | None = None,
    router: XDataRouter | None = None,
) -> dict[str, Any]:
    router = router or XDataRouter()
    budget = validate_max_cost_usd(max_cost_usd)
    if budget is None:
        return _result(ProviderResult.error(provider="mcp", reason="missing_or_invalid_max_cost_usd"))
    if _time_window(start_date, end_date)[2]:
        return _result(ProviderResult.error(provider="mcp", reason="invalid_time_window"))
    result = router.read_owned_timeline(max_cost_usd=budget, limit=clamp_limit(limit))
    return _result(_apply_time_window(result, start_date=start_date, end_date=end_date))


def x_read_mentions_handler(
    *,
    max_cost_usd: float | int | str | None,
    limit: int | None = DEFAULT_LIMIT,
    start_date: str | None = None,
    end_date: str | None = None,
    router: XDataRouter | None = None,
) -> dict[str, Any]:
    router = router or XDataRouter()
    budget = validate_max_cost_usd(max_cost_usd)
    if budget is None:
        return _result(ProviderResult.error(provider="mcp", reason="missing_or_invalid_max_cost_usd"))
    if _time_window(start_date, end_date)[2]:
        return _result(ProviderResult.error(provider="mcp", reason="invalid_time_window"))
    result = router.read_mentions(max_cost_usd=budget, limit=clamp_limit(limit))
    return _result(_apply_time_window(result, start_date=start_date, end_date=end_date))


def x_data_status_handler(*, detail: str = "summary", router: XDataRouter | None = None) -> dict[str, Any]:
    router = router or XDataRouter()
    detail = str(detail or "summary").strip().lower()
    if detail not in DETAIL_LEVELS:
        return {
            "status": "error",
            "server": "x-data",
            "reason": "invalid_detail_level",
            "metadata": {"allowed": sorted(DETAIL_LEVELS)},
        }
    payload = router.status()
    response = {
        "status": "ok",
        "server": "x-data",
        "summary": doctor_summary_from_status(payload),
    }
    if detail == "detailed":
        response["details"] = payload
    return response


def x_data_healthcheck_handler(
    *,
    mode: str = "live",
    detail: str = "summary",
    provider: str | None = None,
    router: XDataRouter | None = None,
) -> dict[str, Any]:
    router = router or XDataRouter()
    mode = str(mode or "live").strip().lower()
    detail = str(detail or "summary").strip().lower()
    provider = str(provider or "").strip() or None
    if mode not in HEALTHCHECK_MODES:
        return {
            "status": "error",
            "server": "x-data",
            "reason": "invalid_healthcheck_mode",
            "metadata": {"allowed": sorted(HEALTHCHECK_MODES)},
        }
    if detail not in DETAIL_LEVELS:
        return {
            "status": "error",
            "server": "x-data",
            "reason": "invalid_detail_level",
            "metadata": {"allowed": sorted(DETAIL_LEVELS)},
        }
    payload = router.healthcheck(mode=mode, provider=provider)
    response = {
        "status": "ok",
        "server": "x-data",
        "summary": doctor_summary_from_healthcheck(payload),
    }
    if detail == "detailed":
        response["details"] = payload
    return response


def x_read_thread_handler(
    value: str,
    *,
    max_cost_usd: float | int | str | None,
    limit: int | None = MAX_LIMIT,
    cursor: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    scope: str = "conversation",
    router: XDataRouter | None = None,
) -> dict[str, Any]:
    router = router or XDataRouter()
    budget = validate_max_cost_usd(max_cost_usd)
    if budget is None:
        return _result(ProviderResult.error(provider="mcp", reason="missing_or_invalid_max_cost_usd"))
    if _time_window(start_date, end_date)[2]:
        return _result(ProviderResult.error(provider="mcp", reason="invalid_time_window"))
    value = str(value or "").strip()
    if not value:
        return _result(ProviderResult.error(provider="mcp", reason="missing_value"))
    if scope not in {"conversation", "self"}:
        return _result(ProviderResult.error(provider="mcp", reason="invalid_scope"))
    result = router.read_thread(
        value,
        max_cost_usd=budget,
        limit=clamp_limit(limit, default=MAX_LIMIT),
        cursor=_optional_text(cursor),
    )
    result = _apply_thread_scope(result, scope=scope)
    return _result(_apply_time_window(result, start_date=start_date, end_date=end_date))


def x_read_replies_handler(
    value: str,
    *,
    max_cost_usd: float | int | str | None,
    limit: int | None = MAX_LIMIT,
    cursor: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    router: XDataRouter | None = None,
) -> dict[str, Any]:
    router = router or XDataRouter()
    budget = validate_max_cost_usd(max_cost_usd)
    if budget is None:
        return _result(ProviderResult.error(provider="mcp", reason="missing_or_invalid_max_cost_usd"))
    if _time_window(start_date, end_date)[2]:
        return _result(ProviderResult.error(provider="mcp", reason="invalid_time_window"))
    value = str(value or "").strip()
    if not value:
        return _result(ProviderResult.error(provider="mcp", reason="missing_value"))
    result = router.read_replies(
        value,
        max_cost_usd=budget,
        limit=clamp_limit(limit, default=MAX_LIMIT),
        cursor=_optional_text(cursor),
    )
    return _result(_apply_time_window(result, start_date=start_date, end_date=end_date))


def x_read_quotes_handler(
    value: str,
    *,
    max_cost_usd: float | int | str | None,
    limit: int | None = MAX_LIMIT,
    cursor: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    router: XDataRouter | None = None,
) -> dict[str, Any]:
    router = router or XDataRouter()
    budget = validate_max_cost_usd(max_cost_usd)
    if budget is None:
        return _result(ProviderResult.error(provider="mcp", reason="missing_or_invalid_max_cost_usd"))
    if _time_window(start_date, end_date)[2]:
        return _result(ProviderResult.error(provider="mcp", reason="invalid_time_window"))
    value = str(value or "").strip()
    if not value:
        return _result(ProviderResult.error(provider="mcp", reason="missing_value"))
    result = router.read_quotes(
        value,
        max_cost_usd=budget,
        limit=clamp_limit(limit, default=MAX_LIMIT),
        cursor=_optional_text(cursor),
    )
    return _result(_apply_time_window(result, start_date=start_date, end_date=end_date))


def x_read_follow_graph_handler(
    user: str,
    *,
    max_cost_usd: float | int | str | None,
    graph: str = "followers",
    limit: int | None = MAX_LIMIT,
    cursor: str | None = None,
    router: XDataRouter | None = None,
) -> dict[str, Any]:
    router = router or XDataRouter()
    budget = validate_max_cost_usd(max_cost_usd)
    if budget is None:
        return _result(ProviderResult.error(provider="mcp", reason="missing_or_invalid_max_cost_usd"))
    user = normalize_handle(user)
    graph = str(graph or "").strip().lower()
    if not user:
        return _result(ProviderResult.error(provider="mcp", reason="missing_user"))
    if graph not in FOLLOW_GRAPHS:
        return _result(
            ProviderResult.error(
                provider="mcp",
                reason="invalid_graph",
                metadata={"allowed": sorted(FOLLOW_GRAPHS)},
            )
        )
    return _result(
        router.read_follow_graph(
            f"@{user}",
            max_cost_usd=budget,
            graph=graph,
            limit=clamp_limit(limit, default=MAX_GRAPH_LIMIT, maximum=MAX_GRAPH_LIMIT),
            cursor=_optional_text(cursor),
        )
    )


def x_read_article_handler(
    value: str,
    *,
    max_cost_usd: float | int | str | None,
    router: XDataRouter | None = None,
) -> dict[str, Any]:
    router = router or XDataRouter()
    budget = validate_max_cost_usd(max_cost_usd)
    if budget is None:
        return _result(ProviderResult.error(provider="mcp", reason="missing_or_invalid_max_cost_usd"))
    value = str(value or "").strip()
    if not value:
        return _result(ProviderResult.error(provider="mcp", reason="missing_value"))
    return _result(router.read_article(value, max_cost_usd=budget))


def x_collect_posts_handler(
    query: str,
    *,
    max_cost_usd: float | int | str | None,
    limit: int | None = MAX_LIMIT,
    cursor: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    router: XDataRouter | None = None,
) -> dict[str, Any]:
    router = router or XDataRouter()
    budget = validate_max_cost_usd(max_cost_usd)
    if budget is None:
        return _result(ProviderResult.error(provider="mcp", reason="missing_or_invalid_max_cost_usd"))
    query = str(query or "").strip()
    if not query:
        return _result(ProviderResult.error(provider="mcp", reason="missing_query"))
    query = _augment_query_with_time_bounds(query, start_date=start_date, end_date=end_date)
    if not query:
        return _result(ProviderResult.error(provider="mcp", reason="invalid_time_window"))
    result = router.collect_posts(
        query,
        max_cost_usd=budget,
        limit=clamp_limit(limit, default=MAX_LIMIT, maximum=MAX_COLLECT_LIMIT),
        cursor=_optional_text(cursor),
    )
    return _result(
        _apply_time_window(result, start_date=start_date, end_date=end_date)
    )


def create_mcp_server(router: XDataRouter | None = None, mcp: Any | None = None) -> Any:
    """Create the FastMCP server, or register tools on an existing one.

    Imported lazily so tests and provider usage do not require MCP unless the
    server boundary is actually constructed.
    """
    from mcp.server.fastmcp import FastMCP

    active_router = router or XDataRouter()
    if mcp is None:
        mcp = FastMCP(
            "x-data",
            instructions=(
                "Read-only X data tools. Use task tools only; provider routing and "
                "credentials are internal. Every data tool call except status/healthcheck must include "
                "`max_cost_usd` as a strict spend ceiling. Use `0` only when you "
                "want zero-cost routes; if no free backend can satisfy the task, "
                "the call returns `needs_approval`. Prefer summary diagnostics "
                "unless detailed output is explicitly needed. For user inputs, pass "
                "an X handle or numeric user ID; do not pass a profile URL. Use "
                "`metadata.next_cursor` to continue paginated list calls. Use `x_read_article` "
                "for published X Articles by wrapper tweet share link or wrapper tweet ID."
            ),
        )

    @mcp.tool()
    def x_fetch_urls(
        values: Annotated[
            list[str],
            Field(
                description=(
                    "Exact public post URLs or raw post IDs. Partial success is allowed: invalid or unavailable "
                    "entries are skipped and reported in `warnings` while fetchable posts are still returned."
                )
            ),
        ],
        max_cost_usd: MaxCostUsd,
    ) -> dict[str, Any]:
        """Requires `max_cost_usd`. Fetch exact public posts only. Use `0` for strictly free routes; unavailable items become warnings, not fatal batch failures."""
        return x_fetch_urls_handler(values, max_cost_usd=max_cost_usd, router=active_router)

    @mcp.tool()
    def x_read_user_posts(
        user: Annotated[
            str,
            Field(
                description=(
                    "Public X handle or numeric user ID, for example `@OpenAI`, `OpenAI`, or `4398626122`. "
                    "Do not pass a profile URL. Protected or unavailable accounts return `unavailable`."
                )
            ),
        ],
        max_cost_usd: MaxCostUsd,
        limit: Annotated[
            int,
            Field(description=f"Max posts to return on this page. Default `{DEFAULT_LIMIT}`.", ge=1, le=MAX_LIMIT),
        ] = DEFAULT_LIMIT,
        cursor: CursorParam = None,
        start_date: TimeBoundParam = None,
        end_date: TimeBoundParam = None,
    ) -> dict[str, Any]:
        """Requires `max_cost_usd`. Read recent public posts for one user. Cursor paginates when the selected provider supports it. Time bounds are applied locally to the returned page."""
        return x_read_user_posts_handler(
            user,
            max_cost_usd=max_cost_usd,
            limit=limit,
            cursor=cursor,
            start_date=start_date,
            end_date=end_date,
            router=active_router,
        )

    @mcp.tool()
    def x_search_posts(
        query: Annotated[
            str,
            Field(
                description=(
                    "Public X search query. Native X-style operators are passed through as provider hints when supported, "
                    "including `from:`, `to:`, `since:`, `until:`, `lang:`, `filter:links`, `filter:images`, "
                    "`min_faves:`, `min_retweets:`, quoted phrases, and `-exclude`. Parenthetical groups and boolean "
                    "operator semantics are provider-dependent, not a guaranteed server-side DSL."
                )
            ),
        ],
        max_cost_usd: MaxCostUsd,
        limit: Annotated[
            int,
            Field(description=f"Max posts to return on this page. Default `{DEFAULT_LIMIT}`.", ge=1, le=MAX_LIMIT),
        ] = DEFAULT_LIMIT,
        cursor: CursorParam = None,
        start_date: TimeBoundParam = None,
        end_date: TimeBoundParam = None,
    ) -> dict[str, Any]:
        """Requires `max_cost_usd`. Search public X posts. `start_date` / `end_date` are translated to query operators and also applied locally to the returned page."""
        return x_search_posts_handler(
            query,
            max_cost_usd=max_cost_usd,
            limit=limit,
            cursor=cursor,
            start_date=start_date,
            end_date=end_date,
            router=active_router,
        )

    @mcp.tool()
    def x_read_owned_timeline(
        max_cost_usd: MaxCostUsd,
        limit: Annotated[
            int,
            Field(description=f"Max posts to return on this page. Default `{DEFAULT_LIMIT}`.", ge=1, le=MAX_LIMIT),
        ] = DEFAULT_LIMIT,
        start_date: TimeBoundParam = None,
        end_date: TimeBoundParam = None,
    ) -> dict[str, Any]:
        """Requires `max_cost_usd`. Read the authenticated account timeline. This is an owned-account read, normally paid-capable, and currently returns only the first page exposed by the chosen provider."""
        return x_read_owned_timeline_handler(
            max_cost_usd=max_cost_usd,
            limit=limit,
            start_date=start_date,
            end_date=end_date,
            router=active_router,
        )

    @mcp.tool()
    def x_read_mentions(
        max_cost_usd: MaxCostUsd,
        limit: Annotated[
            int,
            Field(description=f"Max posts to return on this page. Default `{DEFAULT_LIMIT}`.", ge=1, le=MAX_LIMIT),
        ] = DEFAULT_LIMIT,
        start_date: TimeBoundParam = None,
        end_date: TimeBoundParam = None,
    ) -> dict[str, Any]:
        """Requires `max_cost_usd`. Read mentions for the authenticated account. This is an owned-account read, normally paid-capable, and currently returns only the first page exposed by the chosen provider."""
        return x_read_mentions_handler(
            max_cost_usd=max_cost_usd,
            limit=limit,
            start_date=start_date,
            end_date=end_date,
            router=active_router,
        )

    @mcp.tool()
    def x_read_thread(
        value: Annotated[
            str,
            Field(description="Public post URL or raw post ID used as the thread/conversation anchor."),
        ],
        max_cost_usd: MaxCostUsd,
        limit: Annotated[
            int,
            Field(description=f"Max posts to return on this page. Default `{MAX_LIMIT}`.", ge=1, le=MAX_LIMIT),
        ] = MAX_LIMIT,
        cursor: CursorParam = None,
        start_date: TimeBoundParam = None,
        end_date: TimeBoundParam = None,
        scope: Annotated[
            ThreadScope,
            Field(description="`conversation` returns the returned conversation snapshot. `self` keeps only posts by the root author from that snapshot."),
        ] = "conversation",
    ) -> dict[str, Any]:
        """Requires `max_cost_usd`. Read a public thread or conversation from one anchor post. Protected or unavailable content returns `unavailable`."""
        return x_read_thread_handler(
            value,
            max_cost_usd=max_cost_usd,
            limit=limit,
            cursor=cursor,
            start_date=start_date,
            end_date=end_date,
            scope=scope,
            router=active_router,
        )

    @mcp.tool()
    def x_read_replies(
        value: Annotated[str, Field(description="Public post URL or raw post ID.")],
        max_cost_usd: MaxCostUsd,
        limit: Annotated[
            int,
            Field(description=f"Max replies to return on this page. Default `{MAX_LIMIT}`.", ge=1, le=MAX_LIMIT),
        ] = MAX_LIMIT,
        cursor: CursorParam = None,
        start_date: TimeBoundParam = None,
        end_date: TimeBoundParam = None,
    ) -> dict[str, Any]:
        """Requires `max_cost_usd`. Read replies for one public post. Results respect the authenticated principal's visibility; protected or unavailable content returns `unavailable`."""
        return x_read_replies_handler(
            value,
            max_cost_usd=max_cost_usd,
            limit=limit,
            cursor=cursor,
            start_date=start_date,
            end_date=end_date,
            router=active_router,
        )

    @mcp.tool()
    def x_read_quotes(
        value: Annotated[str, Field(description="Public post URL or raw post ID.")],
        max_cost_usd: MaxCostUsd,
        limit: Annotated[
            int,
            Field(description=f"Max quote posts to return on this page. Default `{MAX_LIMIT}`.", ge=1, le=MAX_LIMIT),
        ] = MAX_LIMIT,
        cursor: CursorParam = None,
        start_date: TimeBoundParam = None,
        end_date: TimeBoundParam = None,
    ) -> dict[str, Any]:
        """Requires `max_cost_usd`. Read quote posts for one public post. Results respect the authenticated principal's visibility; protected or unavailable content returns `unavailable`."""
        return x_read_quotes_handler(
            value,
            max_cost_usd=max_cost_usd,
            limit=limit,
            cursor=cursor,
            start_date=start_date,
            end_date=end_date,
            router=active_router,
        )

    @mcp.tool()
    def x_read_follow_graph(
        user: Annotated[
            str,
            Field(
                description=(
                    "Public X handle or numeric user ID, for example `@OpenAI`, `OpenAI`, or `4398626122`. "
                    "Do not pass a profile URL."
                )
            ),
        ],
        max_cost_usd: MaxCostUsd,
        graph: Annotated[FollowGraph, Field(description="Which edge set to read: `followers` or `following`.")] = "followers",
        limit: Annotated[
            int,
            Field(description=f"Max users to return on this page. Default `{MAX_LIMIT}`; maximum `{MAX_GRAPH_LIMIT}`.", ge=1, le=MAX_GRAPH_LIMIT),
        ] = MAX_LIMIT,
        cursor: CursorParam = None,
    ) -> dict[str, Any]:
        """Requires `max_cost_usd`. Read followers or following for one public user. Use `metadata.next_cursor` to continue beyond the first page when the selected provider supports it."""
        return x_read_follow_graph_handler(
            user,
            max_cost_usd=max_cost_usd,
            graph=graph,
            limit=limit,
            cursor=cursor,
            router=active_router,
        )

    @mcp.tool()
    def x_read_article(
        value: Annotated[
            str,
            Field(
                description=(
                    "Wrapper tweet URL or raw wrapper tweet ID for a published X Article. "
                    "Use the article share/status link from X. Direct `x.com/i/article/...` URLs are not supported in v1."
                )
            ),
        ],
        max_cost_usd: MaxCostUsd,
    ) -> dict[str, Any]:
        """Requires `max_cost_usd`. Read a published X Article through its wrapper tweet share link or wrapper tweet ID. Returns structured article text, blocks, images, author, and metadata."""
        return x_read_article_handler(value, max_cost_usd=max_cost_usd, router=active_router)

    @mcp.tool()
    def x_collect_posts(
        query: Annotated[
            str,
            Field(
                description=(
                    "One-shot bulk collection query for the current call only. This does not register a persistent monitor. "
                    "Uses the same operator hints as `x_search_posts`."
                )
            ),
        ],
        max_cost_usd: MaxCostUsd,
        limit: Annotated[
            int,
            Field(description=f"Max posts to return on this page. Default `{MAX_LIMIT}`; maximum `{MAX_COLLECT_LIMIT}`.", ge=1, le=MAX_COLLECT_LIMIT),
        ] = MAX_LIMIT,
        cursor: CursorParam = None,
        start_date: TimeBoundParam = None,
        end_date: TimeBoundParam = None,
    ) -> dict[str, Any]:
        """Requires `max_cost_usd`. Run a one-shot bulk collection query. It returns posts immediately; it does not create a scheduled monitor or job handle."""
        return x_collect_posts_handler(
            query,
            max_cost_usd=max_cost_usd,
            limit=limit,
            cursor=cursor,
            start_date=start_date,
            end_date=end_date,
            router=active_router,
        )

    @mcp.tool()
    def x_data_status(
        detail: Annotated[
            DetailLevel,
            Field(description="`summary` returns high-signal provider/task health. `detailed` also returns provider status payloads, routes, and task coverage internals."),
        ] = "summary",
    ) -> dict[str, Any]:
        """Return server status. Prefer `summary`; request `detailed` only when debugging."""
        return x_data_status_handler(detail=detail, router=active_router)

    @mcp.tool()
    def x_data_healthcheck(
        mode: Annotated[
            HealthcheckMode,
            Field(description="`basic` inspects config/status only. `live` runs one cheap live probe per supported provider. `deep` runs every configured probe for a fuller failure report."),
        ] = "live",
        detail: Annotated[
            DetailLevel,
            Field(description="`summary` returns a concise doctor-style report. `detailed` also returns per-provider probe records and task coverage internals."),
        ] = "summary",
    ) -> dict[str, Any]:
        """Run diagnostics. Prefer `basic` or `live`; use `deep` only for troubleshooting."""
        return x_data_healthcheck_handler(mode=mode, detail=detail, router=active_router)

    return mcp


def main() -> None:
    create_mcp_server().run()


if __name__ == "__main__":
    main()
