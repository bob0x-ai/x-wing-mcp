import json

from x_usage_ledger import record, statistics


def test_statistics_summarizes_http_and_provider_events(monkeypatch, tmp_path):
    log_path = tmp_path / "usage.jsonl"
    monkeypatch.setenv("X_WING_USAGE_LOG_PATH", str(log_path))

    record(
        "official_x_http",
        provider="official_x",
        endpoint="https://api.x.com/2/users/:id/timelines/reverse_chronological",
        http_status=200,
        resource_count=3,
    )
    record(
        "provider_attempt",
        task="read_owned_timeline",
        provider="official_x",
        status="ok",
        item_count=3,
        actual_estimated_cost_usd=0.003,
    )

    result = statistics(hours=24)

    assert result["official_x_http_requests"] == 1
    assert result["provider_attempts"] == 1
    assert result["locally_estimated_cost_usd"] == 0.003
    assert result["by_endpoint"]["https://api.x.com/2/users/:id/timelines/reverse_chronological"]["resources_returned"] == 3


def test_recent_events_are_sanitized_and_bounded(monkeypatch, tmp_path):
    log_path = tmp_path / "usage.jsonl"
    monkeypatch.setenv("X_WING_USAGE_LOG_PATH", str(log_path))
    record("provider_attempt", task="search_posts", provider="socialdata", status="empty", item_count=0)

    result = statistics(hours=24, detail="recent", limit=1)

    assert len(result["recent_events"]) == 1
    assert json.dumps(result["recent_events"])
