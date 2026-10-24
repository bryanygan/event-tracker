"""9:16 slide decks for a video idea: a scene manifest built from facts only, plus your text edits.

The dashboard draws the slides (1080x1920 canvas, dashboard/src/slides/render.ts); this module decides what
they say. Every default line comes from the alert's facts (no LLM), so dates and venues on a slide are as
right as our data. You can edit any text field, hide slides, and attach your own image to a slide.

Stored per alert (`slide_decks`): the `base` manifest as built, the facts it was built from, and your
`overrides`. When a run later changes the facts (a corrected date, an added show), the deck keeps showing
what you saved and the dashboard warns; "Rebuild" builds a new base from the current facts and keeps each
edit only where the default it replaced is unchanged. An edit on a date that moved is dropped and reported,
never silently carried over.

Rights: no artist photos, album art or music are fetched or suggested. Images are opt-in uploads (data:
URLs, so nothing is fetched from the web and the canvas stays exportable), each with a required credit and
a rights basis stored in the manifest.
"""
from __future__ import annotations

import base64
import json
import re
from collections import Counter

from . import config, detectors, templates
from .store import now_iso

MAX_SHOWS = 8          # one slide per show; the rest are counted on the title slide
MAX_TEXT = 300         # characters per edited field ("lines" fields get MAX_LINES_TEXT)
MAX_LINES_TEXT = 1200
MAX_IMAGES = 4
MAX_IMAGE_CHARS = 2_000_000  # ~1.5 MB of image as base64; the dashboard downsizes uploads to 1080 px first
MAX_CREDIT = 200
RIGHTS = {
    "own": "My own photo",
    "licensed": "Licensed (I have a license)",
    "permission": "Used with the owner's permission",
    "open": "Public domain / Creative Commons (credited)",
}
_DATA_URL = re.compile(r"data:image/(png|jpeg|webp);base64,[A-Za-z0-9+/]+={0,2}")

FIELDS = {  # editable text fields per slide kind
    "title": ("kicker", "headline", "sub", "note"),
    "show": ("date", "time", "artist", "venue", "extra"),
    "sales": ("heading", "lines"),
    "cta": ("headline", "sub"),
}
IMAGE_KINDS = {"title", "show"}
DECK = "deck"           # deck-level override key (brand line)

# Colour mood by the shows' main genre (dashboard/src/slides/render.ts has the palettes)
THEMES = {"Hip-Hop/Rap": "ember", "R&B": "plum", "Pop": "rose", "Dance/Electronic": "electric",
          "Rock": "steel", "Alternative": "steel", "Metal": "steel", "Punk": "steel"}

_KICKERS = {"weekend": "PHILLY THIS WEEKEND", "status_change": "SHOW UPDATE",
            "team_week": "HOME GAMES THIS WEEK", "big_game": "BIG GAME"}
_CTAS = {
    "presale": ("Save this so you don't miss it", "Follow for Philly ticket drops"),
    "new_tour": ("Who's going?", "Follow for Philly announcements"),
    "weekend": ("Send this to your group chat", "Follow for Philly weekend plans"),
    "stacked_month": ("Save this for later", "Follow for Philly shows"),
    "status_change": ("Share this with anyone going", "Check your email or Ticketmaster for details"),
    "team_week": ("Which game are you going to?", "Follow for Philly game days"),
    "big_game": ("Who's going?", "Follow for Philly game days"),
}


class SlidesError(ValueError):
    """Bad overrides from the dashboard (shown as a 400)."""


def _shows(facts: dict) -> list[dict]:
    return facts.get("shows") or ([facts["show"]] if facts.get("show") else [])


def _name(s: dict) -> str:
    return s.get("artist") or s.get("event_name") or ""


def _comeback_year(facts: dict):
    return facts["history"]["last_philly_show_year"] if templates.comeback(facts) else None


def _theme(facts: dict, shows: list[dict]) -> str:
    games = sum(s.get("kind") == "game" for s in shows)
    if facts.get("format") in ("team_week", "big_game") or (shows and games * 2 > len(shows)):
        return "court"  # a music roundup with a game or two keeps its music theme
    if facts.get("genre") in THEMES:
        return THEMES[facts["genre"]]
    genres = Counter(s.get("genre") for s in shows if s.get("genre"))
    return THEMES.get(genres.most_common(1)[0][0], "night") if genres else "night"


def _title(facts: dict, shows: list[dict]) -> dict:
    fmt, artist, n = facts.get("format"), facts.get("artist") or "", len(shows)
    first = shows[0] if shows else {}
    where = " · ".join(b for b in (first.get("date"), first.get("venue")) if b)
    year = _comeback_year(facts)
    kicker, headline, sub = _KICKERS.get(fmt, "PHILLY EVENTS"), artist or "Philly shows", ""
    if fmt == "presale":
        kicker = "BACK IN PHILLY" if year else "TICKET ALERT"
        sub = f"First Philly show since {year}" if year else (where if n == 1 else f"{n} Philly shows")
    elif fmt == "new_tour":
        kicker = "NEW PHILLY DATE" if facts.get("already_had_philly_dates") else "JUST ANNOUNCED"
        sub = f"First Philly show since {year}" if year else where
    elif fmt == "weekend" and facts.get("lane"):  # the your-lane cut: name its genres, not just a count
        headline = f"{facts['lane']} this weekend"
        sub = " · ".join(b for b in (f"{n} shows", facts.get("weekend")) if b)
    elif fmt == "weekend":
        headline, sub = f"{n} shows this weekend", facts.get("weekend", "")
    elif fmt == "stacked_month":
        month = (facts.get("month") or "").split()[0] if facts.get("month") else ""
        kicker = f"{month.upper()} IN PHILLY" if month else "PHILLY THIS MONTH"
        headline = f"{facts.get('genre', 'Live music')} in Philly"
        sub = " · ".join(b for b in (facts.get("month"), f"{facts.get('notable_show_count') or n} shows") if b)
    elif fmt == "status_change":
        sub = f"{first.get('date', '')}: {facts.get('change', 'changed')}".strip(": ")
    elif fmt == "team_week":
        headline, sub = f"{artist} home games", facts.get("week", "")
    elif fmt == "big_game":
        headline = _name(first) or artist
        sub = " · ".join(b for b in (first.get("date"), first.get("time")) if b)
    elif not artist and facts.get("genre"):
        headline = f"{facts['genre']} in Philly"
    note = "Last Philly show: setlist.fm (crowd-sourced)" if year else ""
    return {"kicker": kicker, "headline": headline, "sub": sub, "note": note}


def _show(facts: dict, s: dict) -> dict:
    extra = []
    if facts.get("format") == "status_change" and facts.get("change"):
        extra.append(facts["change"].upper())
    elif (s.get("status") or "").lower() in ("cancelled", "canceled", "postponed", "rescheduled"):
        extra.append(s["status"].upper())
    if s.get("support"):
        extra.append(f"with {s['support']}")
    if s.get("price"):
        extra.append(str(s["price"]))
    return {"date": s.get("date") or "", "time": s.get("time") or "", "artist": _name(s),
            "venue": s.get("venue") or "", "extra": " · ".join(extra)}


def _sales(facts: dict) -> list[str]:
    if facts.get("format") == "presale":
        sales = facts.get("all_sales") or ([{"name": facts["sale_name"], "opens": facts["sale_opens"]}]
                                           if facts.get("sale_opens") else [])
        return [f"{templates.pretty_sale_name(x['name'])}: {x['opens']}" for x in sales]
    if facts.get("format") == "new_tour":
        lines = [f"{templates.pretty_sale_name(p['name'])}: {p['starts']}" for p in facts.get("presales") or []]
        if facts.get("public_onsale"):
            lines.append(f"General on-sale: {facts['public_onsale']}")
        return lines
    return []


def _pick_key(s: dict) -> tuple:
    return (_name(s), s.get("date"), s.get("venue"))


def _show_ids(shows: list[dict]) -> list[str]:
    """Stable slide ids: the ticket URL survives a date correction; the name is the fallback."""
    ids, seen = [], Counter()
    for s in shows:
        key = "show:" + (s.get("url") or _name(s).casefold() or "tba")
        seen[key] += 1
        ids.append(key if seen[key] == 1 else f"{key}#{seen[key]}")
    return ids


def _music_deck(facts: dict) -> dict:
    """Music ideas (roadmap M6): a title slide, one slide per release (the show layout: date pill, big name, title
    underneath, status in the accent colour), Philly dates if any, and a closing slide. All from the facts."""
    from . import music_formats as mf
    fmt = facts["format"]
    slides = []
    if fmt == "music_story":
        phrase = mf.status_phrase(facts)
        outlets = list(dict.fromkeys(s["outlet"] for s in facts.get("sources") or []))
        slides.append({"id": "title", "kind": "title", "fields": {
            "kicker": mf.KICKER[mf.format_key(facts)], "headline": facts.get("artist") or "",
            "sub": f"“{facts['release_title']}”" if facts.get("release_title") else facts.get("headline", ""),
            "note": ("via " + ", ".join(outlets[:3])) if outlets else ""}})
        if facts.get("release_title") or phrase:
            reported = facts.get("release_dates_reported") or []
            slides.append({"id": "release", "kind": "show", "fields": {
                "date": "DATE NOT SETTLED" if reported else facts.get("release_date") or (phrase or "").upper(),
                "time": "", "artist": facts.get("artist") or "",
                "venue": f"“{facts['release_title']}”" if facts.get("release_title") else "",
                "extra": " · ".join(f"{r['date']} per {r['from']}" for r in reported) or (phrase or "").upper()}})
        shows = facts.get("philly_shows") or []
        if shows:
            slides.append({"id": "sales", "kind": "sales", "fields": {
                "heading": "Philly dates", "lines": "\n".join(f"{s['date']}: {s['venue']}" for s in shows[:4])}})
        cta = ("What do you think?", "Follow for music news")
    else:
        items = mf._items(facts)
        if fmt == "new_music_friday":
            kicker, headline, sub = "NEW MUSIC FRIDAY", f"{len(items)} new releases", facts.get("week", "")
        elif fmt == "upcoming_albums":
            kicker, headline, sub = f"{facts['month'].split()[0].upper()} ALBUMS", "Albums I'm watching", facts["month"]
        else:
            kicker, headline, sub = "ARTISTS TO WATCH", f"{len(items)} artists to know", "Before everyone else does"
        slides.append({"id": "title", "kind": "title", "fields": {"kicker": kicker, "headline": headline, "sub": sub,
                                                                   "note": "Release info: Deezer / iTunes" if fmt != "emerging_artists" else ""}})
        ids = Counter()
        for i in items[:MAX_SHOWS]:
            sid = "item:" + re.sub(r"\W+", "-", f"{i['artist']} {i.get('title', '')}".casefold()).strip("-")
            ids[sid] += 1
            if fmt == "emerging_artists":
                fields = {"date": "ON MY RADAR", "time": "", "artist": i["artist"], "venue": i.get("why", ""), "extra": ""}
            else:
                kind = {"album": "ALBUM", "ep": "EP", "single": "SINGLE"}.get(i.get("type"), "RELEASE")
                status = "" if fmt == "new_music_friday" else ("ANNOUNCED" if i.get("status") == "announced" else "")
                fields = {"date": i.get("date") or "DATE TBA" if fmt == "upcoming_albums" else kind, "time": "",
                          "artist": i["artist"], "venue": f"“{i['title']}”" if i.get("title") else "",
                          "extra": " · ".join(b for b in (kind if fmt == "upcoming_albums" else "", status) if b)}
            slides.append({"id": sid if ids[sid] == 1 else f"{sid}#{ids[sid]}", "kind": "show", "fields": fields})
        cta = {"new_music_friday": ("Which one are you playing first?", "Follow for New Music Friday"),
               "upcoming_albums": ("Save this for later", "Follow for music news"),
               "emerging_artists": ("Who should I add?", "Follow for new artists")}[fmt]
    slides.append({"id": "cta", "kind": "cta", "fields": {"headline": cta[0], "sub": cta[1]}})
    shown = sum(s["kind"] == "show" for s in slides)
    return {"version": 1, "format": fmt, "theme": "night", "brand": config.SLIDES_BRAND,
            "shows_total": len(mf._items(facts)) or shown, "shows_on_slides": shown, "slides": slides}


def build(facts: dict) -> dict:
    """The default manifest for these facts. Deterministic: same facts, same manifest."""
    if facts.get("format") in config.MUSIC_KINDS:
        return _music_deck(facts)
    shows = _shows(facts)
    slides = [{"id": "title", "kind": "title", "fields": _title(facts, shows)}]
    # Roundup picks are flagged so the overlay can show "picks only"; when there are more shows than slides,
    # the picks always get one (then the rest by date), keeping the facts' date order.
    picked = {_pick_key(p) for p in facts.get("picks") or []}
    paired = list(zip(_show_ids(shows), shows))
    keep = sorted(range(len(paired)), key=lambda i: (_pick_key(paired[i][1]) not in picked, i))[:MAX_SHOWS]
    for i in sorted(keep):
        sid, s = paired[i]
        slides.append({"id": sid, "kind": "show", "fields": _show(facts, s), "pick": _pick_key(s) in picked})
    sales = _sales(facts)
    if sales:
        slides.append({"id": "sales", "kind": "sales", "fields": {"heading": "Ticket times", "lines": "\n".join(sales)}})
    headline, sub = _CTAS.get(facts.get("format"), ("Save this for later", "Follow for Philly events"))
    slides.append({"id": "cta", "kind": "cta", "fields": {"headline": headline, "sub": sub}})
    return {"version": 1, "format": facts.get("format"), "theme": _theme(facts, shows),
            "brand": config.SLIDES_BRAND, "shows_total": len(shows), "shows_on_slides": min(len(shows), MAX_SHOWS),
            "slides": slides}


def apply(base: dict, overrides: dict) -> dict:
    """The manifest you see: base slides with your edits, hidden flags and images on top."""
    out = json.loads(json.dumps(base))
    deck = overrides.get(DECK) or {}
    if deck.get("brand") is not None:
        out["brand"] = deck["brand"]
    for slide in out["slides"]:
        o = overrides.get(slide["id"]) or {}
        slide["fields"].update({k: v for k, v in o.items() if k in slide["fields"]})
        slide["edited"] = sorted(k for k in o if k in slide["fields"])
        slide["hidden"] = bool(o.get("hidden"))
        if o.get("image"):
            slide["image"] = o["image"]
    return out


def validate(base: dict, overrides) -> dict:
    """Overrides from the dashboard, checked against the deck they edit. Raises SlidesError."""
    if not isinstance(overrides, dict):
        raise SlidesError("overrides must be an object")
    kinds = {s["id"]: s["kind"] for s in base["slides"]}
    clean, images = {}, 0
    for sid, o in overrides.items():
        if not isinstance(o, dict):
            raise SlidesError(f"{sid}: must be an object")
        if sid == DECK:
            brand = o.get("brand")
            if brand is not None and (not isinstance(brand, str) or len(brand) > 60):
                raise SlidesError("brand: text up to 60 characters")
            clean[sid] = {"brand": brand.strip()} if isinstance(brand, str) else {}
            continue
        if sid not in kinds:
            raise SlidesError(f"unknown slide {sid!r}")
        kept = {}
        for k, v in o.items():
            if k in FIELDS[kinds[sid]]:
                limit = MAX_LINES_TEXT if k == "lines" else MAX_TEXT
                if not isinstance(v, str) or len(v) > limit:
                    raise SlidesError(f"{sid}.{k}: text up to {limit} characters")
                kept[k] = v
            elif k == "hidden":
                if not isinstance(v, bool):
                    raise SlidesError(f"{sid}.hidden must be true or false")
                if v:
                    kept[k] = True
            elif k == "image":
                if v is None:
                    continue
                if kinds[sid] not in IMAGE_KINDS:
                    raise SlidesError(f"{sid}: images only go on the title and show slides")
                kept[k] = _image(sid, v)
                images += 1
            else:
                raise SlidesError(f"{sid}: unknown field {k!r}")
        if kept:
            clean[sid] = kept
    if images > MAX_IMAGES:
        raise SlidesError(f"at most {MAX_IMAGES} images per deck")
    if all(clean.get(s["id"], {}).get("hidden") for s in base["slides"]):
        raise SlidesError("at least one slide has to stay visible")
    return clean


def _real_image(kind: str, url: str) -> bool:
    """The bytes match the label (the dashboard re-encodes uploads; a direct API call might not)."""
    try:
        head = base64.b64decode(url.split(",", 1)[1][:24])
    except ValueError:
        return False
    return {"png": head.startswith(b"\x89PNG\r\n\x1a\n"), "jpeg": head.startswith(b"\xff\xd8\xff"),
            "webp": head[:4] == b"RIFF" and head[8:12] == b"WEBP"}[kind]


def _image(sid: str, v) -> dict:
    if not isinstance(v, dict):
        raise SlidesError(f"{sid}.image must be an object")
    url, credit, rights = v.get("data_url"), v.get("credit"), v.get("rights")
    if (not isinstance(url, str) or len(url) > MAX_IMAGE_CHARS or not (m := _DATA_URL.fullmatch(url))
            or not _real_image(m.group(1), url)):
        raise SlidesError(f"{sid}.image: upload a PNG, JPEG or WebP under ~1.5 MB (web links aren't accepted)")
    if not isinstance(credit, str) or not credit.strip() or len(credit) > MAX_CREDIT:
        raise SlidesError(f"{sid}.image: a credit (who took it / where it's from) is required")
    if rights not in RIGHTS:
        raise SlidesError(f"{sid}.image: say why you can use it ({', '.join(RIGHTS)})")
    return {"data_url": url, "credit": credit.strip(), "rights": rights}


def rebase(old_base: dict, overrides: dict, new_base: dict) -> tuple[dict, list[str]]:
    """Carry edits onto a rebuilt deck. An edit survives only where the default it replaced didn't change;
    hidden flags and images follow their slide. Returns (overrides, dropped edit descriptions)."""
    old = {s["id"]: s for s in old_base["slides"]}
    new = {s["id"]: s for s in new_base["slides"]}
    kept, dropped = {}, []
    if overrides.get(DECK):
        kept[DECK] = overrides[DECK]
    for sid, o in overrides.items():
        if sid == DECK:
            continue
        if sid not in new:
            dropped.append(f"{_label(old.get(sid), sid)}: slide removed (the show is no longer in the facts)")
            continue
        k_new = {}
        for k, v in o.items():
            if k in ("hidden", "image"):
                k_new[k] = v
            elif k in new[sid]["fields"]:
                before = (old.get(sid) or {}).get("fields", {}).get(k)
                if before == new[sid]["fields"][k]:
                    k_new[k] = v
                else:
                    dropped.append(f"{_label(new[sid], sid)} {k}: your text \"{v}\" was based on \"{before}\", "
                                   f"now \"{new[sid]['fields'][k]}\"")
        if k_new:
            kept[sid] = k_new
    return kept, dropped


def _label(slide: dict | None, sid: str) -> str:
    if slide and slide["kind"] == "show":
        return slide["fields"].get("artist") or sid
    return slide["kind"] if slide else sid


# ---------- storage ----------

def _visible(manifest: dict) -> list:
    """The words on the slides. A genre filled in later can change the colour theme, but a saved deck keeps
    its theme, so that alone isn't a change worth a warning."""
    return [(s["id"], s["fields"]) for s in manifest["slides"]]


def deck(conn, alert_id: int) -> dict | None:
    """What the dashboard shows: the manifest, your overrides, and whether the facts moved since you saved."""
    alert = conn.execute("SELECT facts FROM alerts WHERE id=?", (alert_id,)).fetchone()
    if not alert:
        return None
    facts = json.loads(alert["facts"])
    row = conn.execute("SELECT * FROM slide_decks WHERE alert_id=?", (alert_id,)).fetchone()
    if not row:
        base = build(facts)
        return {"alert_id": alert_id, "saved": False, "base": base, "manifest": apply(base, {}), "overrides": {},
                "facts_changes": [], "updated_at": None, "rights": RIGHTS}
    base, overrides, built_from = json.loads(row["base"]), json.loads(row["overrides"]), json.loads(row["facts"])
    changes = []
    if _visible(build(facts)) != _visible(build(built_from)):  # only changes that would change a slide's text count
        changes = detectors.facts_diff(built_from, facts) or ["Sale times or other slide text changed"]
    # `base` lets the editor show which fields you changed and reset one; `manifest` is what gets drawn
    return {"alert_id": alert_id, "saved": True, "base": base, "manifest": apply(base, overrides), "overrides": overrides,
            "facts_changes": changes, "updated_at": row["updated_at"], "rights": RIGHTS}


def save(conn, alert_id: int, overrides) -> dict:
    """Store your edits. A deck saved for the first time is built from the current facts."""
    alert = conn.execute("SELECT facts FROM alerts WHERE id=?", (alert_id,)).fetchone()
    if not alert:
        raise KeyError(alert_id)
    row = conn.execute("SELECT base FROM slide_decks WHERE alert_id=?", (alert_id,)).fetchone()
    base = json.loads(row["base"]) if row else build(json.loads(alert["facts"]))
    clean = validate(base, overrides)
    if row:
        conn.execute("UPDATE slide_decks SET overrides=?, updated_at=? WHERE alert_id=?",
                     (json.dumps(clean), now_iso(), alert_id))
    else:
        conn.execute("""INSERT INTO slide_decks (alert_id, base, facts, overrides, created_at, updated_at)
                        VALUES (?,?,?,?,?,?)""",
                     (alert_id, json.dumps(base), alert["facts"], json.dumps(clean), now_iso(), now_iso()))
    conn.commit()
    return deck(conn, alert_id)


def rebuild(conn, alert_id: int) -> dict:
    """New base from the current facts; edits carried over where safe. Returns the deck plus `dropped`."""
    alert = conn.execute("SELECT facts FROM alerts WHERE id=?", (alert_id,)).fetchone()
    if not alert:
        raise KeyError(alert_id)
    row = conn.execute("SELECT * FROM slide_decks WHERE alert_id=?", (alert_id,)).fetchone()
    if not row:
        return {**deck(conn, alert_id), "dropped": []}
    new_base = build(json.loads(alert["facts"]))
    overrides, dropped = rebase(json.loads(row["base"]), json.loads(row["overrides"]), new_base)
    if overrides and all(overrides.get(s["id"], {}).get("hidden") for s in new_base["slides"]):
        overrides = {k: {f: v for f, v in o.items() if f != "hidden"} for k, o in overrides.items()}
        dropped.append("Every slide would be hidden, so all slides are visible again")
    conn.execute("UPDATE slide_decks SET base=?, facts=?, overrides=?, updated_at=? WHERE alert_id=?",
                 (json.dumps(new_base), alert["facts"], json.dumps(overrides), now_iso(), alert_id))
    conn.commit()
    return {**deck(conn, alert_id), "dropped": dropped}


def reset(conn, alert_id: int) -> dict | None:
    conn.execute("DELETE FROM slide_decks WHERE alert_id=?", (alert_id,))
    conn.commit()
    return deck(conn, alert_id)


def export_all(conn, alert_ids: list[int]) -> dict:
    """Manifests for the read-only snapshot, by alert id."""
    out = {}
    for alert_id in alert_ids:
        d = deck(conn, alert_id)
        if d:
            # images are already in `manifest`; a second copy in `overrides` doubled the snapshot's size
            d["overrides"] = {sid: {k: v for k, v in o.items() if k != "image"} for sid, o in d["overrides"].items()}
            out[str(alert_id)] = d
    return out
