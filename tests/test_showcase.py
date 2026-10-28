import json

from tracker import config, showcase


def test_clean_drops_private_keys_and_scrubs_play_counts():
    data = {"title": "🎧 New Philly show: Nova Vale", "my_plays": 412, "notes": "film at the merch table",
            "why": ["Philly show, in your rotation (412 plays)", "Core watchlist artist, 88 plays"],
            "artists": [{"name": "Kilo Moon", "plays": 30, "tier": "core", "seen_live": True}],
            "venue_tier": 20, "facts": {"your_plays": 412, "headliner": "Nova Vale"}}
    out = showcase.clean(data)
    assert out == {"title": "New Philly show: Nova Vale",
                   "why": ["Philly show, in your rotation", "Core watchlist artist"],
                   "artists": [{"name": "Kilo Moon"}], "venue_tier": 20, "facts": {"headliner": "Nova Vale"}}
    assert data["my_plays"] == 412  # the input is left alone


def test_leaks_finds_env_values_hosts_and_play_counts(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text("ANTHROPIC_API_KEY=sk-test-abcdef123\nOLLAMA_MODEL=llama3.1\n# X=ignored-value\n",
                                   encoding="utf-8")
    monkeypatch.setattr(config, "ROOT", tmp_path)
    monkeypatch.setattr(config, "REMOTE_HOST", "edit.example.org")
    data = tmp_path / "site" / "data"
    data.mkdir(parents=True)
    (data / "ok.json").write_text(json.dumps({"model": "llama3.1", "title": "Echo Bay plays Union Transfer"}),
                                  encoding="utf-8")
    assert showcase.leaks(tmp_path / "site") == []

    (data / "bad.json").write_text(json.dumps({"a": "sk-test-abcdef123", "b": "https://edit.example.org/",
                                               "c": "in your rotation (12 plays)"}), encoding="utf-8")
    problems = showcase.leaks(tmp_path / "site")
    assert len(problems) == 2 and all(p.startswith("bad.json") for p in problems)
    assert not any("sk-test" in p for p in problems if "contains" in p)  # never echoes the secret


def test_publish_is_off_without_a_project(monkeypatch):
    monkeypatch.setattr(config, "SHOWCASE_PROJECT", "")
    assert showcase.publish_showcase(None).startswith("skipped")
