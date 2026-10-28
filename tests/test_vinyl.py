import json
from datetime import date, datetime, timezone

import pytest

from tracker import config, db, drafts, llm, vinyl, vinyl_research


def item(instance_id, release_id, artist, title, text="", descriptions=("LP", "Album"), year=2024, added="2026-10-01",
         master_id=None):
    return {"instance_id": instance_id, "date_added": f"{added}T12:00:00-07:00", "rating": 0,
            "basic_information": {"id": release_id, "master_id": master_id, "title": title, "year": year,
                                  "artists": [{"name": artist, "join": ","}],
                                  "formats": [{"name": "Vinyl", "qty": "1", "descriptions": list(descriptions),
                                               "text": text}],
                                  "labels": [{"name": "Glass Label", "catno": "GL-1"}], "genres": ["Pop"],
                                  "styles": ["Synth-pop"], "cover_image": f"https://img.example/{release_id}.jpg"}}


COLLECTION = [
    item(1, 101, "Nova Vale (2)", "Glass Hours (10th Anniversary Edition)", "Translucent Red",
         ("LP", "Album", "Limited Edition", "Numbered"), master_id=9001),
    item(2, 102, "Kilo Moon", "Static Bloom", "", ("LP", "Album")),
    item(3, 103, "Echo Bay", "Soft Arcade", "Sea Foam, Signed Insert", ("LP", "Album", "Record Store Day")),
    item(4, 104, "Iron Lark", "Night Ferry", "Black", ("LP", "Album")),
    item(5, 105, "Nova Vale (2)", "Glass Hours", "Black", ("LP", "Album"), master_id=9001, added="2026-10-25"),
    item(6, 106, "Echo Bay", "Low Tide", "Coke Bottle Clear", ("LP", "Album"), added="2026-10-26"),
]
DETAILS = {  # have, want, rating avg, count, notes, price NM
    101: (300, 900, 4.6, 40, "Limited to 1,000 copies.", 88.5),
    102: (9000, 1200, 4.1, 300, "", 31.0),
    103: (150, 400, 4.4, 12, "", 61.2),
    104: (2500, 100, 3.9, 50, "", 22.0),
    105: (5000, 700, 4.5, 200, "", 35.0),
    106: (80, 60, 4.2, 8, "", 40.0),
}


class FakeDiscogs:
    def __init__(self):
        self.calls = 0

    def collection(self):
        self.calls += 1
        return COLLECTION

    def collection_value(self):
        return {"minimum": "$100.00", "median": "$250.00", "maximum": "$400.00"}

    def release(self, rid):
        self.calls += 1
        have, want, avg, count, notes, _ = DETAILS[rid]
        return {"community": {"have": have, "want": want, "rating": {"average": avg, "count": count}},
                "lowest_price": 20.0, "num_for_sale": 5, "released": "2024-05-03", "country": "US", "notes": notes,
                "extraartists": [{"name": "Glass Hours (3)", "role": "Producer"}]}

    def price_suggestions(self, rid):
        self.calls += 1
        return {vinyl.NM: {"currency": "USD", "value": DETAILS[rid][5]}}

    def search_vinyl(self, artist, title=None, per_page=10):
        return []


class FakeMB:
    calls = 0

    def find_release_group(self, artist, title):
        self.calls += 1
        if title == "Glass Hours":
            return {"id": "rg-1", "title": title, "first_release_date": "2016-11-02", "primary_type": "Album"}
        return None


@pytest.fixture
def conn(tmp_path):
    c = db.connect(tmp_path / "v.db")
    vinyl.sync(c, client=FakeDiscogs(), mb=FakeMB(), now=datetime(2026, 10, 28, tzinfo=timezone.utc))
    return c


def test_reading_discogs():
    assert vinyl.clean_artist("Nova Vale (2)") == "Nova Vale"
    assert vinyl.album_title("Glass Hours (10th Anniversary Edition)") == "Glass Hours"
    assert vinyl.album_title("Static Bloom 10 Year Anniversary - (Complete Edition)") == "Static Bloom"
    assert vinyl.album_title("Night Ferry (Live)") == "Night Ferry (Live)"
    fmt = [{"name": "Vinyl", "qty": "2", "descriptions": ["LP", "Album"], "text": "Translucent Red"}]
    assert vinyl.pressing(fmt) == "2x, LP, Album, Translucent Red"
    assert vinyl.is_colored(fmt) and not vinyl.is_colored([{"text": "10th Anniversary"}])
    assert not vinyl.is_colored([{"text": "Black"}]) and vinyl.is_colored([{"text": "Coke Bottle Clear"}])
    assert vinyl.limited_to("Limited to 1,000 copies worldwide.") == 1000 and vinyl.limited_to("Repress") is None


def test_sync_reads_details_prices_and_dates(conn):
    rows = {r["release_id"]: r for r in vinyl.records(conn)}
    assert len(rows) == 6 and rows[101]["artist"] == "Nova Vale" and rows[101]["have"] == 300
    assert vinyl.suggested_price(rows[101]) == (88.5, vinyl.NM)
    assert rows[101]["original_date"] == "2016-11-02" and rows[105]["original_date"] == "2016-11-02"
    assert json.loads(rows[101]["credits"]) == [{"name": "Glass Hours", "role": "Producer"}]
    # nothing is due again on the next run; a record gone from Discogs is marked, not deleted
    again = FakeDiscogs()
    out = vinyl.sync(conn, client=again, mb=FakeMB(), now=datetime(2026, 10, 28, 1, tzinfo=timezone.utc))
    assert out["details"] == 0 and out["prices"] == 0 and again.calls == 0
    vinyl.save_collection(conn, COLLECTION[1:])
    assert len(vinyl.records(conn)) == 5


def test_uniqueness_reasons(conn):
    row = conn.execute("SELECT * FROM vinyl_records WHERE release_id=101").fetchone()
    pts, why = vinyl.uniqueness(row)
    assert {"numbered", "limited edition", "colored vinyl", "limited to 1,000 copies"} <= set(why)
    assert any(w.startswith("only 300") for w in why)
    plain = conn.execute("SELECT * FROM vinyl_records WHERE release_id=104").fetchone()
    assert vinyl.uniqueness(plain)[0] < pts


def test_ideas_from_the_collection(conn):
    today = date(2026, 10, 28)
    got = {i["facts"]["format"]: i for i in vinyl.ideas(conn, today, client=FakeDiscogs(), taste=llm_free_taste())}
    assert [r["artist"] for r in got["vinyl_unique"]["facts"]["records"]][:2] == ["Echo Bay", "Nova Vale"]  # signed, then numbered
    value = got["vinyl_value"]["facts"]
    assert [r["suggested_price_usd"] for r in value["records"]][:2] == [88.5, 61.2]
    assert value["whole_collection_value"]["median"] == "$250.00"
    assert got["vinyl_coveted"]["facts"]["records"][0]["discogs_want"] == 1200
    assert len(got["vinyl_variants"]["facts"]["records"]) == 2          # two pressings of Glass Hours
    assert len(got["vinyl_haul"]["facts"]["records"]) == 2              # added in the last two weeks
    assert got["vinyl_numbers"]["facts"]["record_count"] == 6
    stories = [got[f]["facts"]["record"]["release_id"] for f in ("vinyl_lore", "vinyl_underappreciated",
                                                                 "vinyl_underrated") if f in got]
    assert len(stories) == len(set(stories)) >= 2                       # three different records
    ann = got["vinyl_anniversary"]
    assert ann["facts"]["anniversary"] == {"years": 10, "date": "2026-11-02", "first_released": "2016-11-02"}
    assert ann["title"] == "Glass Hours turns 10 on Mon, Nov 2" and ann["post_by"].startswith("2026-11-02")
    # raised once per period, then left alone
    first = vinyl.save_ideas(conn, list(got.values()))
    assert len(first) == len(got) and vinyl.save_ideas(conn, list(got.values())) == []
    # a story's record isn't picked again soon
    again = {i["facts"]["format"]: i for i in vinyl.ideas(conn, date(2026, 10, 29), client=FakeDiscogs(),
                                                          taste=llm_free_taste())}
    assert again["vinyl_underrated"]["facts"]["record"]["release_id"] not in stories


def llm_free_taste():
    from tracker import listening
    return listening.Taste()


def test_make_idea_and_drafting_is_on_request(conn):
    alert_id = vinyl.make_idea(conn, 4, "vinyl_lore", date(2026, 10, 28))
    assert vinyl.make_idea(conn, 4, "vinyl_lore", date(2026, 10, 28)) == alert_id
    with pytest.raises(ValueError):
        vinyl.make_idea(conn, 4, "vinyl_value")
    assert drafts.draft_pending(conn) == []  # the pipeline never drafts vinyl ideas


def test_numbers_check():
    srcs = [{"text": "Released in 2016. Discogs suggested price: $88.50. Sold 12,000 copies."}]
    assert vinyl_research.number_warnings("Out since 2016, about $88, 12,000 copies", {}, srcs) == []
    assert vinyl_research.number_warnings("It went platinum in 2019", {}, srcs) == [
        "Check the number 2019: it isn't in the sources"]


def test_research_draft_flags_unbacked_claims(conn, monkeypatch):
    alert_id = vinyl.make_idea(conn, 4, "vinyl_lore", date(2026, 10, 28))
    monkeypatch.setattr(llm, "uses_claude", lambda: True)
    monkeypatch.setattr(vinyl_research, "find_album_page", lambda a, t: (
        "Night Ferry (album)", "Night Ferry is the debut album by Iron Lark, recorded on a boat in 2019.",
        "https://en.wikipedia.org/wiki/Night_Ferry_(album)"))
    monkeypatch.setattr(vinyl_research, "find_artist_page", lambda a: None)
    calls = []

    def fake_chat(c, model, prompt, schema, system, max_tokens=2000, timeout=60.0):
        calls.append((model, prompt))
        if schema is vinyl_research.DRAFT_SCHEMA:
            assert "recorded on a boat in 2019" in prompt and "release_id" not in prompt
            return {"hook": "This album was recorded on a boat", "script": "Iron Lark made Night Ferry on a boat in 2019. "
                    "It sold a million copies.", "closer": "Would you record on a boat?",
                    "caption": "The story of Night Ferry #ironlark", "hashtags": ["#vinyl"],
                    "claims": [{"text": "Recorded on a boat in 2019", "sources": [3]},
                               {"text": "It sold a million copies", "sources": []}]}
        return {"checks": [{"claim": 0, "verdict": "supported", "note": ""},
                           {"claim": 1, "verdict": "unsupported", "note": "no source"}]}
    monkeypatch.setattr(llm, "_claude_chat", fake_chat)
    draft_id = drafts.draft_alert(conn, alert_id)
    d = drafts.draft_row(conn.execute("SELECT * FROM drafts WHERE id=?", (draft_id,)).fetchone(), {})
    assert [m for m, _ in calls] == [config.VINYL_STORY_MODEL, config.VINYL_CHECK_MODEL]
    assert d["warnings"] == ["Not in the sources: It sold a million copies"]
    assert "YOUR TAKE:" in d["script"] and d["script"].endswith("Would you record on a boat?")
    assert [s["kind"] for s in d["sources"]["sources"]] == ["discogs", "wikipedia_album"]
    assert d["onscreen"][0] == "Iron Lark – Night Ferry" and "#ironlark" in d["hashtags"]
    # the Wikipedia lookup is cached: a second draft doesn't search again
    monkeypatch.setattr(vinyl_research, "find_album_page", lambda a, t: pytest.fail("looked up twice"))
    drafts.draft_alert(conn, alert_id)


def test_api_keeps_vinyl_on_its_own_tab(conn, tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from tracker import api
    monkeypatch.setattr(api, "conn", lambda: db.connect(tmp_path / "v.db"))
    vinyl.make_idea(conn, 4, "vinyl_lore", date(2026, 10, 28))
    client = TestClient(api.app)
    assert client.get("/api/alerts").json() == []
    assert [a["kind"] for a in client.get("/api/alerts", params={"kind": "vinyl"}).json()] == ["vinyl"]
    v = client.get("/api/vinyl").json()
    assert len(v["records"]) == 6 and v["value"]["median"] == "$250.00"
    assert client.post("/api/vinyl/4/idea", json={"format": "vinyl_value"}).status_code == 400
    assert client.post("/api/vinyl/999/idea", json={"format": "vinyl_lore"}).status_code == 404
    [q] = [i for i in client.get("/api/queue").json()["items"] if i["pillar"] == "vinyl"]
    assert q["lane"] == "vinyl" and q["reasons"] == ["You picked this record"]


def test_comma_names_and_notes_only_features(tmp_path):
    c = db.connect(tmp_path / "n.db")
    vinyl.save_collection(c, [item(1, 201, "Static, The Echo", "Kilo Moon"), item(2, 202, "Static, The Echo", "Iron Lark"),
                              item(3, 203, "Soft Arcade", "Glass Hours")])
    c.execute("UPDATE vinyl_records SET notes='Some copies include a signed art card.', details_at='x' WHERE release_id=203")
    rows = vinyl.records(c)
    assert vinyl.numbers_idea(rows * 2, date(2026, 10, 28), None)["facts"]["most_collected_artists"][0]["artist"] == \
        "Static, The Echo"
    row = c.execute("SELECT * FROM vinyl_records WHERE release_id=203").fetchone()
    assert "signed (per the release notes)" in vinyl.uniqueness(row)[1]


def test_countdown_onscreen_and_rounding():
    facts = {"format": "vinyl_value", "records": [
        {"artist": "Nova Vale", "title": "Glass Hours", "suggested_price_usd": 195.5},
        {"artist": "Kilo Moon", "title": "Static Bloom", "suggested_price_usd": 152.91}]}
    assert vinyl_research.onscreen(facts) == ["#2 Kilo Moon – Static Bloom · $153", "#1 Nova Vale – Glass Hours · $196"]
    srcs = [{"text": "suggested 152.91; cheapest 281.69"}]
    assert vinyl_research.number_warnings("about $153, cheapest $282, and 2 copies", facts, srcs) == []
