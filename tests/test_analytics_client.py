import json
from unittest.mock import MagicMock, patch

import analytics_client


def config_file(tmp_path, text="analytics_service:\n  enabled: true\n  base_url: http://127.0.0.1:9684\n  timeout_seconds: 3\n"):
    path = tmp_path / "analytics.yaml"
    path.write_text(text)
    return path


def test_read_analytics_uses_configured_local_service_and_passes_window(tmp_path):
    response = MagicMock()
    response.read.return_value = json.dumps({"as_of": "2026-08-14T12:00:00+00:00"}).encode()
    response.__enter__.return_value = response
    response.headers = {"Content-Type": "application/json"}
    opener = MagicMock()
    opener.open.return_value = response
    with patch("analytics_client.build_opener", return_value=opener):
        result = analytics_client.read_analytics(view="posts", window_days=31, sort="growth", limit=5, config_path=config_file(tmp_path))
    assert result["as_of"] == "2026-08-14T12:00:00+00:00"
    url = opener.open.call_args.args[0].full_url
    assert "/v1/analytics/posts?" in url
    assert "window=30d" in url
    assert "limit=5" in url


def test_read_analytics_requires_a_post_id_for_history(tmp_path):
    try:
        analytics_client.read_analytics(view="post_history", config_path=config_file(tmp_path))
    except analytics_client.AnalyticsServiceConfigurationError as exc:
        assert "post_id" in str(exc)
    else:
        raise AssertionError("expected configuration error")


def test_read_analytics_reports_missing_configuration(tmp_path):
    try:
        analytics_client.read_analytics(config_path=tmp_path / "missing.yaml")
    except analytics_client.AnalyticsServiceConfigurationError as exc:
        assert "not configured" in str(exc)
    else:
        raise AssertionError("expected configuration error")


def test_read_analytics_rejects_non_loopback_target(tmp_path):
    path = config_file(tmp_path, "analytics_service:\n  enabled: true\n  base_url: https://example.com\n")
    try:
        analytics_client.read_analytics(config_path=path)
    except analytics_client.AnalyticsServiceConfigurationError as exc:
        assert "loopback" in str(exc)
    else:
        raise AssertionError("expected configuration error")


def test_read_analytics_reports_service_outage(tmp_path):
    opener = MagicMock()
    opener.open.side_effect = analytics_client.URLError("offline")
    with patch("analytics_client.build_opener", return_value=opener):
        try:
            analytics_client.read_analytics(config_path=config_file(tmp_path))
        except analytics_client.AnalyticsServiceUnavailable as exc:
            assert "unreachable" in str(exc)
        else:
            raise AssertionError("expected service error")
