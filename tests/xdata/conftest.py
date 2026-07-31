"""Shared x_data test fixtures."""

import pytest

from xdata.providers import official_x


@pytest.fixture(autouse=True)
def isolate_xdata_env(monkeypatch, tmp_path):
    """Prevent x_data tests from reading the real repo .env file."""
    monkeypatch.setattr(official_x, "_dotenv_path", lambda: tmp_path / "no-such-env")
