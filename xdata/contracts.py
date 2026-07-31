"""Shared provider contracts for X data collection.

The provider layer returns explicit status values so the router can tell the
difference between "worked but no data" and "not implemented/configured".
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal, TypeAlias

ProviderStatus = Literal["ok", "empty", "unavailable", "error", "needs_approval"]


@dataclass(frozen=True)
class UserRef:
    id: str | None = None
    username: str | None = None
    name: str | None = None


@dataclass(frozen=True)
class Metrics:
    replies: int | None = None
    reposts: int | None = None
    likes: int | None = None
    quotes: int | None = None
    views: int | None = None


@dataclass(frozen=True)
class Post:
    id: str
    text: str
    author: UserRef | None = None
    created_at: str | None = None
    metrics: Metrics | None = None
    source_url: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class UserProfile:
    id: str
    username: str | None = None
    name: str | None = None
    description: str | None = None
    public_metrics: dict[str, int] | None = None
    source_url: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ArticleImage:
    url: str
    width: int | None = None
    height: int | None = None


@dataclass(frozen=True)
class ArticleBlock:
    type: str
    text: str | None = None
    url: str | None = None
    width: int | None = None
    height: int | None = None
    inline_style_ranges: list[dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True)
class Article:
    id: str
    title: str
    body_text: str
    author: UserRef | None = None
    created_at: str | None = None
    metrics: Metrics | None = None
    preview_text: str | None = None
    cover_image_url: str | None = None
    source_url: str | None = None
    images: list[ArticleImage] = field(default_factory=list)
    blocks: list[ArticleBlock] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)


ProviderItem: TypeAlias = Post | UserProfile | Article


@dataclass(frozen=True)
class CostEstimate:
    amount_usd: float
    basis: str


@dataclass(frozen=True)
class ProviderResult:
    status: ProviderStatus
    provider: str
    items: list[ProviderItem] = field(default_factory=list)
    reason: str | None = None
    warnings: list[str] = field(default_factory=list)
    cost: CostEstimate | None = None
    raw_ref: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def ok(
        cls,
        *,
        provider: str,
        items: list[ProviderItem],
        warnings: list[str] | None = None,
        cost: CostEstimate | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> ProviderResult:
        if not items:
            return cls.empty(provider=provider, reason="no_results", warnings=warnings)
        return cls(
            status="ok",
            provider=provider,
            items=items,
            warnings=warnings or [],
            cost=cost,
            metadata=metadata or {},
        )

    @classmethod
    def empty(
        cls,
        *,
        provider: str,
        reason: str = "no_results",
        warnings: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> ProviderResult:
        return cls(
            status="empty",
            provider=provider,
            reason=reason,
            warnings=warnings or [],
            metadata=metadata or {},
        )

    @classmethod
    def unavailable(
        cls,
        *,
        provider: str,
        reason: str,
        warnings: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> ProviderResult:
        return cls(
            status="unavailable",
            provider=provider,
            reason=reason,
            warnings=warnings or [],
            metadata=metadata or {},
        )

    @classmethod
    def error(
        cls,
        *,
        provider: str,
        reason: str,
        warnings: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> ProviderResult:
        return cls(
            status="error",
            provider=provider,
            reason=reason,
            warnings=warnings or [],
            metadata=metadata or {},
        )

    @classmethod
    def needs_approval(
        cls,
        *,
        provider: str,
        reason: str,
        cost: CostEstimate | None = None,
        warnings: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> ProviderResult:
        return cls(
            status="needs_approval",
            provider=provider,
            reason=reason,
            warnings=warnings or [],
            cost=cost,
            metadata=metadata or {},
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
