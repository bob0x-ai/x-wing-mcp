"""Configuration loading for provider/server behavior."""

from __future__ import annotations

import os
from copy import deepcopy
from pathlib import Path
from typing import Any

import yaml


_REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = str(_REPO_ROOT / "config" / "providers.yaml")
CONFIG_ENV_VAR = "X_MCP_CONFIG_FILE"

DEFAULT_CONFIG: dict[str, Any] = {
    "server": {
        "max_fetch_urls": 25,
        "max_limit": 100,
        "max_collect_limit": 500,
        "default_limit": 20,
    },
    "providers": {
        "socialdata": {
            "enabled": True,
            "cooldown_seconds": 60,
            "rate_limit": {
                "requests_per_minute": 20,
                "jitter_seconds": 0.25,
            },
        },
        "syndication": {
            "enabled": True,
            "cooldown_seconds": 60,
            "rate_limit": {
                "requests_per_minute": 12,
                "jitter_seconds": 0.35,
            },
        },
        "official_x": {
            "enabled": True,
        },
        "getxapi": {
            "enabled": True,
            "cooldown_seconds": 60,
            "rate_limit": {
                "requests_per_minute": 20,
                "jitter_seconds": 0.2,
            },
        },
        "xpoz": {"enabled": False},
        "twikit": {
            "enabled": False,
            "cookies_file": str(_REPO_ROOT / "config" / "x_cookies.json"),
            "locale": "en-US",
            "cooldown_seconds": 300,
            "rate_limit": {
                "requests_per_minute": 6,
                "jitter_seconds": 0.75,
            },
        },
        "twscrape": {"enabled": False},
        "apify": {"enabled": False},
        "xactions": {"enabled": False},
    },
    "routes": {
        "fetch_urls": ["syndication", "official_x", "socialdata", "apify"],
        "read_user_posts_recent": [
            "socialdata",
            "syndication",
            "twikit",
            "twscrape",
            "xpoz",
            "apify",
            "official_x",
        ],
        "search_posts": ["socialdata", "xpoz", "twikit", "twscrape", "apify", "official_x"],
        "read_owned_timeline": ["official_x"],
        "read_mentions": ["official_x"],
        "read_thread": ["twikit", "twscrape", "socialdata", "xpoz", "apify", "official_x"],
        "read_replies": ["socialdata", "xpoz", "apify", "twikit", "twscrape", "official_x"],
        "read_quotes": ["socialdata", "xpoz", "apify", "twikit", "twscrape", "official_x"],
        "read_follow_graph": ["socialdata", "xpoz", "twscrape", "twikit", "apify", "official_x"],
        "read_article": ["getxapi"],
        "collect_posts": ["socialdata", "xpoz", "twscrape", "twikit", "apify"],
    },
        "smoke": {
            "user": "@OpenAI",
        "search_query": "from:OpenAI",
        "collect_query": "OpenAI",
        "graph": "followers",
        "limit": 3,
    },
}


def load_config(path: str | None = None) -> dict[str, Any]:
    config = deepcopy(DEFAULT_CONFIG)
    config_path = path or os.getenv(CONFIG_ENV_VAR) or DEFAULT_CONFIG_PATH
    config["config_path"] = config_path
    file_path = Path(config_path)
    if not file_path.exists():
        return config
    raw = yaml.safe_load(file_path.read_text(encoding="utf-8"))
    if raw is None:
        return config
    if not isinstance(raw, dict):
        raise ValueError(f"config_root_must_be_mapping:{config_path}")
    return _deep_merge(config, raw)


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged
