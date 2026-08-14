import json
from unittest.mock import MagicMock, patch

import analytics_client


def test_read_analytics_uses_local_service_and_passes_days(monkeypatch):
    monkeypatch.setenv("X_ANALYTICS_URL", "http://127.0.0.1:9684")
    response = MagicMock()
    response.read.return_value = json.dumps({"as_of": "2026-08-14T12:00:00+00:00"}).encode()
    response.__enter__.return_value = response
    with patch("analytics_client.urlopen", return_value=response) as open_url:
        result = analytics_client.read_analytics(days=31)
    assert result["as_of"] == "2026-08-14T12:00:00+00:00"
    assert "days=30" in open_url.call_args.args[0].full_url


def test_read_analytics_reports_service_outage(monkeypatch):
    monkeypatch.setenv("X_ANALYTICS_URL", "http://127.0.0.1:1")
    with patch("analytics_client.urlopen", side_effect=analytics_client.URLError("offline")):
        try:
            analytics_client.read_analytics()
        except analytics_client.AnalyticsServiceError as exc:
            assert "unavailable" in str(exc)
        else:
            raise AssertionError("expected service error")
