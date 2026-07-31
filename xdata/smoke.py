"""Live smoke harness for X data providers and router."""

from __future__ import annotations

import argparse
import json
import os
from dataclasses import asdict
from pathlib import Path
from typing import Any

from xdata.config import load_config
from xdata.contracts import Post, ProviderResult, UserProfile
from xdata.providers.official_x import OfficialXProvider
from xdata.providers.socialdata import SocialDataProvider
from xdata.providers.syndication import SyndicationProvider
from xdata.router import XDataRouter


KNOWN_ENV_KEYS = {
    "SOCIALDATA_API_KEY",
    "X_OAUTH2_CLIENT_ID",
    "X_OAUTH2_CLIENT_SECRET",
    "X_OAUTH2_ACCESS_TOKEN",
    "X_OAUTH2_REFRESH_TOKEN",
    "X_CLIENT_ID",
    "X_CLIENT_SECRET",
    "X_ACCESS_TOKEN",
    "X_REFRESH_TOKEN",
}


def parse_args() -> argparse.Namespace:
    config = load_config()
    smoke_defaults = config.get("smoke", {})
    parser = argparse.ArgumentParser(description="Run a live smoke check against X data providers.")
    parser.add_argument(
        "--env-file",
        default=None,
        help="Optional env file to load provider credentials from for this run.",
    )
    parser.add_argument(
        "--user",
        default=smoke_defaults.get("user", "@OpenAI"),
        help="Seed user for recent-post and graph reads.",
    )
    parser.add_argument(
        "--search-query",
        default=smoke_defaults.get("search_query", "from:OpenAI"),
        help="Search query for search smoke checks.",
    )
    parser.add_argument(
        "--collect-query",
        default=smoke_defaults.get("collect_query", "OpenAI"),
        help="Collection query for bulk/search smoke checks.",
    )
    parser.add_argument(
        "--graph",
        default=smoke_defaults.get("graph", "followers"),
        choices=["followers", "following"],
        help="Graph direction for follow-graph reads.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=int(smoke_defaults.get("limit", 3)),
        help="Default item limit for lightweight checks.",
    )
    parser.add_argument(
        "--format",
        default="text",
        choices=["text", "json"],
        help="Output format.",
    )
    return parser.parse_args()


def load_env_file(path: str | None) -> None:
    if not path:
        return
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key in KNOWN_ENV_KEYS and key not in os.environ:
            os.environ[key] = value.strip()


def summarize_result(result: ProviderResult, *, sample_size: int = 3) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "status": result.status,
        "provider": result.provider,
        "reason": result.reason,
        "count": len(result.items),
        "warnings": result.warnings,
        "metadata": result.metadata,
    }
    if result.cost:
        summary["cost"] = asdict(result.cost)
    if result.items:
        sample = result.items[:sample_size]
        summary["sample"] = [summarize_item(item) for item in sample]
    return summary


def summarize_item(item: Post | UserProfile) -> dict[str, Any]:
    if isinstance(item, Post):
        return {
            "kind": "post",
            "id": item.id,
            "author": item.author.username if item.author else None,
            "created_at": item.created_at,
            "text": item.text[:120],
        }
    return {
        "kind": "user",
        "id": item.id,
        "username": item.username,
        "name": item.name,
    }


def run_smoke(args: argparse.Namespace) -> dict[str, Any]:
    provider = SocialDataProvider()
    router = XDataRouter()
    syndication = SyndicationProvider()
    official = OfficialXProvider()

    results: dict[str, Any] = {
        "config": {
            "user": args.user,
            "search_query": args.search_query,
            "collect_query": args.collect_query,
            "graph": args.graph,
            "limit": args.limit,
        },
        "status": {
            "socialdata": provider.status(),
            "syndication": syndication.status(),
            "official_x": official.status(),
            "router": router.status(),
        },
    }

    recent = provider.read_user_posts(args.user, limit=args.limit)
    results["socialdata.read_user_posts"] = summarize_result(recent)

    first_post_id = recent.items[0].id if recent.items else None
    if first_post_id:
        results["socialdata.fetch_urls"] = summarize_result(provider.fetch_urls([first_post_id]))
        results["socialdata.read_thread"] = summarize_result(provider.read_thread(first_post_id, limit=max(5, args.limit)))
        results["socialdata.read_replies"] = summarize_result(
            provider.read_replies(first_post_id, limit=max(5, args.limit))
        )
        results["socialdata.read_quotes"] = summarize_result(
            provider.read_quotes(first_post_id, limit=max(5, args.limit))
        )
        results["router.fetch_urls"] = summarize_result(
            router.fetch_urls([f"https://x.com/i/web/status/{first_post_id}"], max_cost_usd=0.05)
        )

    results["socialdata.search_posts"] = summarize_result(provider.search_posts(args.search_query, limit=args.limit))
    results["socialdata.read_follow_graph"] = summarize_result(
        provider.read_follow_graph(args.user, graph=args.graph, limit=args.limit)
    )
    results["socialdata.collect_posts"] = summarize_result(
        provider.collect_posts(args.collect_query, limit=max(5, args.limit))
    )
    results["router.search_posts"] = summarize_result(router.search_posts(args.search_query, limit=args.limit, max_cost_usd=0.05))
    return results


def render_text(results: dict[str, Any]) -> str:
    lines: list[str] = []
    config = results["config"]
    lines.append(
        "Smoke config: "
        f"user={config['user']} search={config['search_query']} collect={config['collect_query']} "
        f"graph={config['graph']} limit={config['limit']}"
    )
    lines.append("")
    for name, status in results["status"].items():
        if name == "router":
            continue
        lines.append(
            f"{name} status: auth_required={status.get('auth_required')} "
            f"auth_present={status.get('auth_present', 'n/a')} read_only={status.get('read_only', 'n/a')}"
        )
    lines.append("")
    for key, value in results.items():
        if key in {"config", "status"}:
            continue
        line = (
            f"{key}: status={value['status']} provider={value['provider']} "
            f"count={value['count']}"
        )
        if value.get("reason"):
            line += f" reason={value['reason']}"
        lines.append(line)
        if value.get("warnings"):
            lines.append(f"  warnings={', '.join(value['warnings'])}")
        sample = value.get("sample") or []
        if sample:
            sample_tokens = []
            for item in sample:
                if item["kind"] == "post":
                    sample_tokens.append(f"post:{item['id']}@{item.get('author')}")
                else:
                    sample_tokens.append(f"user:{item['id']}@{item.get('username')}")
            lines.append(f"  sample={'; '.join(sample_tokens)}")
        metadata = value.get("metadata") or {}
        if metadata:
            filtered = {k: v for k, v in metadata.items() if k in {"next_cursor", "query", "user_id", "graph", "mode"}}
            if filtered:
                lines.append(f"  metadata={json.dumps(filtered, sort_keys=True)}")
    return "\n".join(lines)


def main() -> None:
    args = parse_args()
    load_env_file(args.env_file)
    results = run_smoke(args)
    if args.format == "json":
        print(json.dumps(results, indent=2, sort_keys=True))
        return
    print(render_text(results))


if __name__ == "__main__":
    main()
