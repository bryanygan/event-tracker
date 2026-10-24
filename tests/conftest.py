import pytest

from tracker import config


@pytest.fixture(autouse=True)
def no_real_services(monkeypatch, tmp_path):
    """Tests must never deploy to Cloudflare or call keyed APIs, whatever is in the real .env."""
    monkeypatch.setattr(config, "PAGES_PROJECT", "")
    monkeypatch.setattr(config, "CLOUDFLARE_API_TOKEN", "")
    monkeypatch.setattr(config, "CLOUDFLARE_ACCOUNT_ID", "")
    monkeypatch.setattr(config, "PAGES_ALLOW_PUBLIC", False)
    monkeypatch.setattr(config, "SETLISTFM_API_KEY", "")  # and never spend the real setlist.fm quota
    monkeypatch.setattr(config, "VENUE_CALENDARS", [])    # or fetch real venue sites
    monkeypatch.setattr(config, "NEWS_SOURCES", [])       # or real music-news feeds
    monkeypatch.setattr(config, "YOUTUBE_API_KEY", "")    # or the YouTube Data API (and its quota)
    monkeypatch.setattr(config, "LAST_FM_API_KEY", "")    # or Last.fm / Deezer / ListenBrainz discovery (M7)
    monkeypatch.setattr(config, "YOUTUBE_SOURCES", [])
    monkeypatch.setattr(config, "WATCHLIST_IDS_PER_RUN", 0)  # or Deezer/MusicBrainz artist lookups
    monkeypatch.setattr(config, "CATALOG_ARTISTS_PER_CHECK", 0)  # or Deezer/iTunes release catalogs
    monkeypatch.setattr(config, "LISTENING_DB", "")       # or read the user's real listening history
    monkeypatch.setattr(config, "PERPLEXITY_API_KEY", "")  # or spend money on web lookups
    monkeypatch.setattr(config, "ANTHROPIC_API_KEY", "")   # or on Claude drafts
    monkeypatch.setattr(config, "DRAFT_PROVIDER", "ollama")
    for name in ("REMOTE_HOST", "CF_ACCESS_TEAM_DOMAIN", "CF_ACCESS_AUD"):  # or trust the real tunnel setup
        monkeypatch.setattr(config, name, "")
    monkeypatch.setattr(config, "REMOTE_EMAILS", set())
    monkeypatch.setattr(config, "RUN_LOCK", tmp_path / "pipeline.lock")  # never contend with a real run
    monkeypatch.setattr(config, "EXPERIENCE_SOURCES", [])  # or read Center City District / the waterfront (P2-14)
    from tracker import api, listening
    monkeypatch.setattr(listening, "_cache", {})
    monkeypatch.setattr(api, "LOCAL_HOSTS", api.LOCAL_HOSTS | {"testserver"})  # TestClient's Host header
