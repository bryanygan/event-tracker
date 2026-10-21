import pytest

from tracker import config


@pytest.fixture(autouse=True)
def no_real_publishing(monkeypatch):
    """Tests must never deploy to Cloudflare, whatever is in the real .env."""
    monkeypatch.setattr(config, "PAGES_PROJECT", "")
    monkeypatch.setattr(config, "CLOUDFLARE_API_TOKEN", "")
    monkeypatch.setattr(config, "CLOUDFLARE_ACCOUNT_ID", "")
    monkeypatch.setattr(config, "PAGES_ALLOW_PUBLIC", False)
