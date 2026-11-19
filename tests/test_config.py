"""Environment-driven configuration."""

from __future__ import annotations

import os

import pytest

from ebay_mcp.config import Config, ConfigError


@pytest.fixture
def clean_env(monkeypatch):
    for key in list(os.environ):
        if key.startswith("EBAY_"):
            monkeypatch.delenv(key, raising=False)
    return monkeypatch


def test_load_defaults(clean_env):
    config = Config.load()
    assert config.environment == "production"
    assert config.marketplace_id == "EBAY_GB"
    assert config.item_location_country == "GB"
    assert config.host == "https://api.ebay.com"
    assert config.oauth_token_url.endswith("/identity/v1/oauth2/token")
    assert config.browse_base_url.endswith("/buy/browse/v1")


def test_location_follows_marketplace(clean_env):
    clean_env.setenv("EBAY_MARKETPLACE_ID", "EBAY_DE")
    assert Config.load().item_location_country == "DE"


def test_location_override_and_any(clean_env):
    clean_env.setenv("EBAY_ITEM_LOCATION_COUNTRY", "ie")
    assert Config.load().item_location_country == "IE"
    clean_env.setenv("EBAY_ITEM_LOCATION_COUNTRY", "ANY")
    assert Config.load().item_location_country is None


def test_delivery_location(clean_env):
    clean_env.setenv("EBAY_DELIVERY_COUNTRY", "GB")
    clean_env.setenv("EBAY_DELIVERY_POSTAL_CODE", "SW1A 1AA")
    config = Config.load()
    assert (config.delivery_country, config.delivery_postal_code) == ("GB", "SW1A 1AA")


def test_sandbox_environment(clean_env):
    clean_env.setenv("EBAY_ENVIRONMENT", "sandbox")
    assert Config.load().host == "https://api.sandbox.ebay.com"


def test_invalid_environment_raises(clean_env):
    clean_env.setenv("EBAY_ENVIRONMENT", "staging")
    with pytest.raises(ConfigError):
        Config.load()


def test_require_credentials():
    with pytest.raises(ConfigError) as info:
        Config(client_id="", client_secret="").require_credentials()
    assert "EBAY_CLIENT_ID" in str(info.value)
    assert "EBAY_CLIENT_SECRET" in str(info.value)
    Config(client_id="a", client_secret="b").require_credentials()  # no raise
