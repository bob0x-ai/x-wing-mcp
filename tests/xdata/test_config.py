from xdata.config import DEFAULT_CONFIG, load_config
from xdata.router import XDataRouter


def test_load_config_uses_defaults_when_file_missing(tmp_path):
    config_path = tmp_path / "missing.yaml"

    config = load_config(str(config_path))

    assert config["config_path"] == str(config_path)
    assert config["server"]["max_fetch_urls"] == DEFAULT_CONFIG["server"]["max_fetch_urls"]
    assert config["routes"]["fetch_urls"] == DEFAULT_CONFIG["routes"]["fetch_urls"]


def test_load_config_merges_yaml_overrides(tmp_path):
    config_path = tmp_path / "providers.yaml"
    config_path.write_text(
        """
server:
  max_limit: 42
providers:
  socialdata:
    enabled: false
    rate_limit:
      requests_per_minute: 9
routes:
  search_posts:
    - official_x
smoke:
  user: "@example"
""".strip(),
        encoding="utf-8",
    )

    config = load_config(str(config_path))

    assert config["server"]["max_limit"] == 42
    assert config["providers"]["socialdata"]["enabled"] is False
    assert config["providers"]["socialdata"]["rate_limit"]["requests_per_minute"] == 9
    assert config["routes"]["search_posts"] == ["official_x"]
    assert config["smoke"]["user"] == "@example"
    assert config["routes"]["fetch_urls"] == DEFAULT_CONFIG["routes"]["fetch_urls"]


def test_router_uses_configured_routes_and_provider_enablement():
    config = {
        "config_path": "/tmp/test.yaml",
        "server": DEFAULT_CONFIG["server"],
        "smoke": DEFAULT_CONFIG["smoke"],
        "providers": {
            "socialdata": {"enabled": False},
            "official_x": {"enabled": True},
            "getxapi": {"enabled": True},
            "syndication": {"enabled": True},
            "xpoz": {"enabled": False},
            "twikit": {"enabled": False},
            "twscrape": {"enabled": False},
            "apify": {"enabled": False},
            "xactions": {"enabled": False},
        },
        "routes": {
            "fetch_urls": ["socialdata", "syndication"],
            "read_user_posts_recent": ["socialdata", "official_x"],
            "search_posts": ["official_x"],
            "read_owned_timeline": ["official_x"],
            "read_mentions": ["official_x"],
            "read_thread": ["official_x"],
            "read_replies": ["official_x"],
            "read_quotes": ["official_x"],
            "read_follow_graph": ["official_x"],
            "read_article": ["getxapi"],
            "collect_posts": ["socialdata"],
        },
    }

    router = XDataRouter(config=config)
    status = router.status()

    assert status["routes"]["fetch_urls"] == ["socialdata", "syndication"]
    assert status["effective_routes"]["fetch_urls"] == ["syndication"]
    assert status["preferred_providers"]["fetch_urls"] == "syndication"
    assert status["config_path"] == "/tmp/test.yaml"
