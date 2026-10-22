import pytest

from tracker import config


@pytest.fixture(autouse=True)
def no_real_services(monkeypatch):
    """Tests must never deploy to Cloudflare or call keyed APIs, whatever is in the real .env."""
    monkeypatch.setattr(config, "PAGES_PROJECT", "")
    monkeypatch.setattr(config, "CLOUDFLARE_API_TOKEN", "")
    monkeypatch.setattr(config, "CLOUDFLARE_ACCOUNT_ID", "")
    monkeypatch.setattr(config, "PAGES_ALLOW_PUBLIC", False)
    monkeypatch.setattr(config, "SETLISTFM_API_KEY", "")  # and never spend the real setlist.fm quota
    monkeypatch.setattr(config, "VENUE_CALENDARS", [])    # or fetch real venue sites
    monkeypatch.setattr(config, "LISTENING_DB", "")       # or read the user's real listening history
    monkeypatch.setattr(config, "PERPLEXITY_API_KEY", "")  # or spend money on web lookups
    from tracker import api, listening
    monkeypatch.setattr(listening, "_cache", {})
    monkeypatch.setattr(api, "LOCAL_HOSTS", api.LOCAL_HOSTS | {"testserver"})  # TestClient's Host header
