"""Shared x_data test fixtures."""

import pytest

from xdata.providers import official_x


@pytest.fixture(autouse=True)
def isolate_xdata_env(monkeypatch, tmp_path):
    """Prevent x_data tests from reading the real repo .env file."""
    monkeypatch.setattr(official_x.x_client, "env_path", tmp_path / "no-such-env")
    for key in (
        "X_OAUTH2_ACCESS_TOKEN",
        "X_ACCESS_TOKEN",
        "X_OAUTH2_CLIENT_ID",
        "X_CLIENT_ID",
        "X_OAUTH2_CLIENT_SECRET",
        "X_CLIENT_SECRET",
        "X_OAUTH2_REFRESH_TOKEN",
        "X_REFRESH_TOKEN",
    ):
        monkeypatch.delenv(key, raising=False)
