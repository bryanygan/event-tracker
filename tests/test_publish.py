"""Cloudflare Pages snapshot: export contents, the Access guardrail, and deploy wiring (no network)."""
import json
from datetime import date, timedelta

import pytest
import responses

from tracker import config, db, detectors, publish, store
from tracker.fetchers import ticketmaster as tm
from tests.factory import tm_event


SOON = (date.today() + timedelta(days=10)).isoformat()


@pytest.fixture
def conn(tmp_path):
    c = db.connect(tmp_path / "p.db")
    r = store.start_run(c, "ticketmaster")
    store.upsert_events(c, r, [tm.normalize(tm_event(event_id="e1", name="Mt. Joy", date=SOON))], baseline=True)
    store.finish_run(c, r, fetched=1, api_calls=0, ok=True)
    detectors.save_alerts(c, [{"key": "k", "kind": "weekend", "title": "Weekend", "score": 50,
                               "facts": {"format": "weekend", "shows": []}}])
    return c


@pytest.fixture
def fake_build(tmp_path, monkeypatch):
    build = tmp_path / "dist-static"
    (build / "assets").mkdir(parents=True)
    (build / "index.html").write_text("<html>static</html>")
    monkeypatch.setattr(publish, "STATIC_BUILD", build)
    monkeypatch.setattr(publish, "SITE_DIR", tmp_path / "site")
    monkeypatch.setattr(publish, "STATUS_FILE", tmp_path / "status.json")
    return build


@pytest.fixture
def pages(monkeypatch):
    monkeypatch.setattr(config, "PAGES_PROJECT", "philly-test")
    return "https://philly-test.pages.dev/"


def test_export_writes_app_data_and_privacy_headers(conn, fake_build, tmp_path):
    out = publish.export(conn, tmp_path / "site")
    assert (out / "index.html").read_text() == "<html>static</html>"
    summary = json.loads((out / "data" / "summary.json").read_text(encoding="utf-8"))
    assert summary["upcoming_events"] == 1 and "generated_at" in summary
    assert json.loads((out / "data" / "alerts.json").read_text(encoding="utf-8"))[0]["title"] == "Weekend"
    events = json.loads((out / "data" / "events.json").read_text(encoding="utf-8"))
    assert [e["headliner"] for e in events] == ["Mt. Joy"]
    assert "noindex" in (out / "_headers").read_text() and "Disallow: /" in (out / "robots.txt").read_text()


def test_export_requires_static_build(conn, tmp_path, monkeypatch):
    monkeypatch.setattr(publish, "STATIC_BUILD", tmp_path / "missing")
    with pytest.raises(publish.PublishError, match="build:static"):
        publish.export(conn, tmp_path / "site")


@responses.activate
def test_access_check(pages):
    responses.get(pages, status=302, headers={"Location": "https://me.cloudflareaccess.com/cdn-cgi/access/login/x"})
    assert publish.access_protected()[0] is True
    responses.replace(responses.GET, pages, status=200, body="<html>")
    assert publish.access_protected()[0] is False
    responses.replace(responses.GET, pages, status=302, headers={"Location": "https://evil.example/"})
    assert publish.access_protected()[0] is False


def test_publish_skipped_when_not_configured(conn):
    assert publish.publish(conn).startswith("skipped")


@responses.activate
def test_publish_refuses_when_site_is_public(conn, fake_build, pages, monkeypatch):
    responses.get(pages, status=200, body="public!")
    deployed = []
    monkeypatch.setattr(publish, "deploy", lambda *a: deployed.append(1))
    result = publish.publish(conn)
    assert "refusing to upload" in result and deployed == []
    assert publish.last_status()["ok"] is False


@pytest.fixture(autouse=True)
def no_subdomain_lookup(monkeypatch):
    monkeypatch.setattr(publish, "_subdomain_cache", {"philly-test": "philly-test.pages.dev"})


@responses.activate
def test_publish_refuses_when_preview_urls_are_public(conn, fake_build, pages, monkeypatch):
    responses.get(pages, status=302, headers={"Location": "https://me.cloudflareaccess.com/login"})
    responses.get("https://guardcheck.philly-test.pages.dev/", status=200, body="public preview")
    monkeypatch.setattr(publish, "deploy", lambda *a: pytest.fail("must not deploy"))
    assert "preview URLs" in publish.publish(conn)


@responses.activate
def test_publish_deploys_when_protected(conn, fake_build, pages, monkeypatch):
    responses.get(pages, status=302, headers={"Location": "https://me.cloudflareaccess.com/login"})
    responses.get("https://guardcheck.philly-test.pages.dev/", status=302,
                  headers={"Location": "https://me.cloudflareaccess.com/login"})
    calls = []
    monkeypatch.setattr(publish, "run_wrangler", lambda *args, **kw: calls.append(args) or "Deployment complete")
    assert publish.publish(conn) == f"published to {pages}"
    args = calls[0]
    assert args[:2] == ("pages", "deploy") and "philly-test" in args and "main" in args
    assert publish.last_status()["ok"] is True


def test_wrangler_needs_credentials(pages):
    with pytest.raises(publish.PublishError, match="CLOUDFLARE_API_TOKEN"):
        publish.run_wrangler("whoami")
