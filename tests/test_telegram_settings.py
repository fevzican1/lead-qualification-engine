"""Tests for Telegram operational settings (ops channel + watchdog)."""

import config


def test_owner_linkedin_url_is_set():
    assert config.OWNER_LINKEDIN_URL.startswith("https://www.linkedin.com/")
    assert config.LINKEDIN_PROFILE_URL == config.OWNER_LINKEDIN_URL


def test_notify_settings_present():
    # Ops channel tokens can be empty in dev, but the keys must exist
    assert hasattr(config, "TELEGRAM_NOTIFY_BOT_TOKEN")
    assert hasattr(config, "TELEGRAM_NOTIFY_CHAT_ID")
    assert hasattr(config, "WATCHDOG_BOT_TOKEN")
    assert hasattr(config, "WATCHDOG_CHAT_ID")


def test_admin_code_attribute():
    assert hasattr(config, "ADMIN_CODE")
    assert hasattr(config, "OWNER_CHAT_ID")
