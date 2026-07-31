"""Tests for OAuth setup credential persistence."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import oauth_setup


def test_update_env_tokens_preserves_existing_refresh_when_x_omits_one(tmp_path):
    env_path = tmp_path / ".env"
    env_path.write_text(
        "X_OAUTH2_ACCESS_TOKEN=old_access\n"
        "X_OAUTH2_REFRESH_TOKEN=old_refresh\n"
        "X_OAUTH2_SCOPES=tweet.read users.read offline.access\n"
        "X_ACCESS_TOKEN=old_access\n"
        "X_REFRESH_TOKEN=old_refresh\n"
        "X_SCOPES=tweet.read users.read offline.access\n"
    )

    oauth_setup.update_env_tokens(env_path, "new_access", None)

    env_text = env_path.read_text()
    assert "X_OAUTH2_ACCESS_TOKEN=new_access\n" in env_text
    assert "X_ACCESS_TOKEN=new_access\n" in env_text
    assert "X_OAUTH2_REFRESH_TOKEN=old_refresh\n" in env_text
    assert "X_REFRESH_TOKEN=old_refresh\n" in env_text
    assert "X_OAUTH2_SCOPES=tweet.read users.read offline.access\n" in env_text
    assert "X_SCOPES=tweet.read users.read offline.access\n" in env_text


def test_update_env_tokens_creates_backup_before_replacing_env(tmp_path):
    env_path = tmp_path / ".env"
    original = "X_OAUTH2_ACCESS_TOKEN=old_access\nX_OAUTH2_REFRESH_TOKEN=old_refresh\n"
    env_path.write_text(original)

    oauth_setup.update_env_tokens(env_path, "new_access", "new_refresh")

    backups = list(tmp_path.glob(".env.bak.*"))
    assert len(backups) == 1
    assert backups[0].read_text() == original
    env_text = env_path.read_text()
    assert "X_OAUTH2_ACCESS_TOKEN=new_access\n" in env_text
    assert "X_OAUTH2_REFRESH_TOKEN=new_refresh\n" in env_text


def test_update_env_tokens_persists_granted_scopes(tmp_path):
    env_path = tmp_path / ".env"
    env_path.write_text("X_OAUTH2_ACCESS_TOKEN=old_access\n")

    oauth_setup.update_env_tokens(
        env_path,
        "new_access",
        "new_refresh",
        "tweet.read tweet.write users.read offline.access",
    )

    env_text = env_path.read_text()
    assert "X_OAUTH2_SCOPES=tweet.read tweet.write users.read offline.access\n" in env_text
    assert "X_SCOPES=tweet.read tweet.write users.read offline.access\n" in env_text
