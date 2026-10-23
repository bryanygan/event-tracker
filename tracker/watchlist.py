"""Music watchlist (docs/agent-notes/music-roadmap.md M2): the artists whose news is a candidate for your feed, and
which articles mention them.

- Seeded from your listening history: artists with ROTATION_MIN_PLAYS+ plays are `core`. Only names and tiers are
  stored (in the gitignored tracker.db); play counts stay in plays.db and are read live for the dashboard.
- Your edits (tier, mute, aliases, "common word") set `user_edited` and survive re-seeding. Artists you add are
  `manual`. An artist who drops out of your rotation becomes `dropped` (kept, not matched), never deleted.
- Matching: the name or an alias on word boundaries in the headline, then the feed's summary. A name that's also an
  ordinary word ("Tomorrow") or two letters long counts only in a headline that also has a music word (album, tour,
  feat...) and is "low" confidence. An artist MusicBrainz knows several of is never matched on the summary alone.
- Ids: Deezer (exact name, most fans) and MusicBrainz (exactly one strong exact-name match), a few per run.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timedelta, timezone

import requests

from . import config, listening

log = logging.getLogger(__name__)

TIERS = ("core", "adjacent", "watch", "breakout", "dropped")
MATCHED_TIERS = ("core", "adjacent", "watch", "breakout")
# A common-word name only counts next to one of these in the headline
MUSIC_WORDS = re.compile(
    r"\b(albums?|singles?|songs?|tracks?|ep|lp|mixtapes?|tours?|tour dates|concerts?|shows?|festival|videos?|"
    r"feat\.?|ft\.?|featuring|collab\w*|remix|announces?|shares?|drops?|releases?|debuts?|premieres?|"
    r"rappers?|singers?|producers?|dj|verse|lyrics|deluxe|vinyl|setlist|headlin\w+|stream\w*|chart\w*)\b", re.I)
IDS_REFRESH_DAYS = 90


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _norm(text: str | None) -> str:
    """Curly quotes to straight ones, so "O’Neil" in a headline matches "O'Neil"."""
    return (text or "").replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')


# ---------- seeding and edits ----------

def seed(conn, taste: listening.Taste | None = None) -> dict:
    """Bring `core` in line with your listening history. Without a listening history nothing is changed."""
    taste = taste if taste is not None else listening.load()
    if not taste.plays:
        return {"added": 0, "returned": 0, "dropped": 0}
    rotation = listening.rotation(taste)
    rows = {r["key"]: r for r in conn.execute("SELECT * FROM artist_identities")}
    added = returned = dropped = 0
    for key, (name, _plays) in rotation.items():
        row = rows.get(key)
        if row is None:
            conn.execute("""INSERT INTO artist_identities (key, name, tier, origin, created_at)
                            VALUES (?, ?, 'core', 'listening', ?)""", (key, name, _now()))
            added += 1
        elif not row["user_edited"] and row["tier"] != "core":  # back in rotation, or a manual add you now play
            conn.execute("UPDATE artist_identities SET tier='core' WHERE id=?", (row["id"],))
            returned += 1
    for key, row in rows.items():
        if (key not in rotation and row["origin"] == "listening" and not row["user_edited"]
                and row["tier"] == "core"):
            conn.execute("UPDATE artist_identities SET tier='dropped' WHERE id=?", (row["id"],))
            dropped += 1
    link_attractions(conn)
    conn.commit()
    return {"added": added, "returned": returned, "dropped": dropped}


def link_attractions(conn) -> None:
    """Tie watchlist artists to the events pipeline's artists by name key, so a story and a Philly date meet."""
    by_key = {}
    for a in conn.execute("SELECT id, name FROM attractions ORDER BY first_seen"):
        by_key.setdefault(listening.artist_key(a["name"]), a["id"])
    for r in conn.execute("SELECT id, key FROM artist_identities WHERE attraction_id IS NULL").fetchall():
        if r["key"] in by_key:
            conn.execute("UPDATE artist_identities SET attraction_id=? WHERE id=?", (by_key[r["key"]], r["id"]))


class WatchlistError(ValueError):
    pass


def _clean_aliases(aliases) -> list[str]:
    if not isinstance(aliases, list) or not all(isinstance(a, str) for a in aliases):
        raise WatchlistError("aliases must be a list of names")
    out = []
    for a in aliases:
        a = a.strip()
        if a and a.casefold() not in {x.casefold() for x in out}:
            out.append(a[:100])
    return out[:20]


def add(conn, name: str, tier: str = "watch", aliases: list[str] | None = None) -> int:
    """Add an artist by hand (or take over a seeded one). Returns its id."""
    name = (name or "").strip()
    key = listening.artist_key(name)
    if not key or len(name) > 100:
        raise WatchlistError("give an artist name")
    if tier not in MATCHED_TIERS:
        raise WatchlistError(f"tier must be one of {', '.join(MATCHED_TIERS)}")
    aliases = _clean_aliases(aliases or [])
    row = conn.execute("SELECT id FROM artist_identities WHERE key=?", (key,)).fetchone()
    if row:
        conn.execute("UPDATE artist_identities SET tier=?, aliases=?, muted=0, user_edited=1 WHERE id=?",
                     (tier, json.dumps(aliases), row["id"]))
        artist_id = row["id"]
    else:
        artist_id = conn.execute("""INSERT INTO artist_identities (key, name, aliases, tier, origin, user_edited, created_at)
                                    VALUES (?, ?, ?, ?, 'manual', 1, ?)""",
                                 (key, name, json.dumps(aliases), tier, _now())).lastrowid
    link_attractions(conn)
    conn.commit()
    rematch(conn)
    return artist_id


def update(conn, artist_id: int, fields: dict) -> dict:
    """Change tier, muted, aliases or common_word_override (None = automatic). Marks the artist as yours."""
    row = conn.execute("SELECT * FROM artist_identities WHERE id=?", (artist_id,)).fetchone()
    if row is None:
        raise KeyError(artist_id)
    sets: dict = {}
    if "tier" in fields:
        if fields["tier"] not in TIERS:
            raise WatchlistError(f"tier must be one of {', '.join(TIERS)}")
        sets["tier"] = fields["tier"]
    if "muted" in fields:
        sets["muted"] = int(bool(fields["muted"]))
    if "aliases" in fields:
        sets["aliases"] = json.dumps(_clean_aliases(fields["aliases"]))
    if "common_word_override" in fields:
        v = fields["common_word_override"]
        if v not in (None, True, False, 0, 1):
            raise WatchlistError("common_word_override must be true, false or null")
        sets["common_word_override"] = None if v is None else int(bool(v))
    if sets:
        sets["user_edited"] = 1
        conn.execute(f"UPDATE artist_identities SET {', '.join(f'{k}=?' for k in sets)} WHERE id=?",
                     (*sets.values(), artist_id))
        conn.commit()
        rematch(conn)
    return dict(conn.execute("SELECT * FROM artist_identities WHERE id=?", (artist_id,)).fetchone())


# ---------- matching ----------

LOWERCASE_WORD_MIN_ARTICLES = 3


def is_common_word(name: str, texts: list[tuple[str, str]]) -> bool:
    """Also an ordinary word? `texts` is (headline, summary) for stored articles.

    - Two letters or fewer: always.
    - A capitalized name ("Tomorrow", "Kilo Moon"): if it shows up in lowercase anywhere ("tomorrow" does,
      "kilo moon" doesn't).
    - A name styled in lowercase ("softarcade", "void"): its own headlines and roundups use that spelling too, so it
      only counts if it shows up, as a word, in LOWERCASE_WORD_MIN_ARTICLES+ articles whose headline isn't about
      it. ("void" does, in "into the void"; "softarcade" doesn't.)"""
    letters = re.sub(r"\W", "", name)
    if len(letters) <= 2:
        return True
    lower = name.lower()
    word = re.compile(r"(?<![\w$])" + re.escape(lower) + r"(?![\w$])")
    if lower != name:
        return any(word.search(t) or word.search(e) for t, e in texts)
    about = re.compile(r"(?<![\w$])" + re.escape(lower) + r"(?![\w$])", re.I)
    hits = sum(1 for t, e in texts if not about.search(t) and word.search(e))
    return hits >= LOWERCASE_WORD_MIN_ARTICLES


def refresh_common_words(conn) -> None:
    texts = [(_norm(r["title"]), _norm(r["excerpt"])) for r in conn.execute("SELECT title, excerpt FROM articles")]
    for r in conn.execute("SELECT id, name FROM artist_identities").fetchall():
        conn.execute("UPDATE artist_identities SET common_word=? WHERE id=?",
                     (int(is_common_word(_norm(r["name"]), texts)), r["id"]))
    conn.commit()


class Matcher:
    """One regex over every matchable name (longest first, so "Lil Baby" wins over a "Baby")."""

    def __init__(self, artists):
        self.forms: dict[str, list[tuple[dict, str]]] = {}  # casefolded name -> [(artist, exact|alias)]
        for a in artists:
            for form, kind in [(a["name"], "exact")] + [(x, "alias") for x in json.loads(a["aliases"] or "[]")]:
                form = _norm(form).strip()
                if len(re.sub(r"\W", "", form)) >= 2:
                    self.forms.setdefault(form.casefold(), []).append((a, kind))
        alts = sorted(self.forms, key=len, reverse=True)
        self.rx = re.compile(r"(?<![\w$])(" + "|".join(re.escape(f) for f in alts) + r")(?![\w$])", re.I) if alts else None

    def _found(self, text: str) -> dict[int, tuple[dict, str]]:
        out = {}
        for m in self.rx.finditer(_norm(text)) if self.rx and text else ():
            for artist, kind in self.forms.get(m.group(1).casefold(), []):
                if artist["id"] not in out or kind == "exact":
                    out[artist["id"]] = (artist, kind)
        return out

    def match(self, title: str, excerpt: str | None) -> list[dict]:
        in_title, in_excerpt = self._found(title), self._found(excerpt or "")
        music_word = MUSIC_WORDS.search(title) is not None
        hits = []
        for artist_id, (artist, kind) in in_title.items():
            if _common(artist):
                if music_word:
                    hits.append({"artist_id": artist_id, "match": kind, "location": "title", "confidence": "low"})
            else:
                hits.append({"artist_id": artist_id, "match": kind, "location": "title", "confidence": "high"})
        for artist_id, (artist, kind) in in_excerpt.items():
            if artist_id in in_title or _common(artist) or artist["ambiguous_name"]:
                continue
            hits.append({"artist_id": artist_id, "match": kind, "location": "excerpt", "confidence": "medium"})
        return hits


def _common(artist) -> bool:
    override = artist["common_word_override"]
    return bool(artist["common_word"] if override is None else override)


def _matchable(conn):
    marks = ",".join("?" * len(MATCHED_TIERS))
    return conn.execute(f"SELECT * FROM artist_identities WHERE muted=0 AND tier IN ({marks})", MATCHED_TIERS).fetchall()


def rematch(conn, article_ids: list[int] | None = None) -> int:
    """Rebuild article_artists for these articles (all of them when None). Returns how many links were made."""
    matcher = Matcher(_matchable(conn))
    if article_ids is None:
        conn.execute("DELETE FROM article_artists")
        rows = conn.execute("SELECT id, title, excerpt FROM articles").fetchall()
    else:
        if not article_ids:
            return 0
        marks = ",".join("?" * len(article_ids))
        conn.execute(f"DELETE FROM article_artists WHERE article_id IN ({marks})", article_ids)
        rows = conn.execute(f"SELECT id, title, excerpt FROM articles WHERE id IN ({marks})", article_ids).fetchall()
    links = 0
    for r in rows:
        for h in matcher.match(r["title"], r["excerpt"]):
            conn.execute("INSERT INTO article_artists (article_id, artist_id, match, location, confidence) VALUES (?,?,?,?,?)",
                         (r["id"], h["artist_id"], h["match"], h["location"], h["confidence"]))
            links += 1
    conn.commit()
    return links


def refresh(conn) -> dict:
    """The full pass the 6:30 and 4:30 runs make: re-seed, re-check common words, rematch everything."""
    seeded = seed(conn)
    refresh_common_words(conn)
    return {**seeded, "links": rematch(conn)}


# ---------- ids ----------

def resolve_ids(conn, limit: int = 40, deezer_client=None, mb_client=None) -> int:
    """Deezer and MusicBrainz ids for artists not checked in IDS_REFRESH_DAYS (new ones first). Stops quietly
    on a rate limit or network error; the rest wait for the next run."""
    from .fetchers import deezer, musicbrainz
    deezer_client = deezer_client or deezer.DeezerClient()
    mb_client = mb_client or musicbrainz.MusicBrainzClient()
    cutoff = (datetime.now(timezone.utc) - timedelta(days=IDS_REFRESH_DAYS)).isoformat()
    marks = ",".join("?" * len(MATCHED_TIERS))
    rows = conn.execute(f"""SELECT id, name FROM artist_identities
                            WHERE muted=0 AND tier IN ({marks}) AND (ids_checked_at IS NULL OR ids_checked_at < ?)
                            ORDER BY ids_checked_at IS NOT NULL, ids_checked_at, id LIMIT ?""",
                        (*MATCHED_TIERS, cutoff, limit)).fetchall()
    done = 0
    for r in rows:
        try:
            found = deezer_client.find_artist(r["name"])
            mbid, ambiguous = mb_client.find_artist(r["name"])
        except (requests.RequestException, ValueError) as exc:
            log.warning("Artist id lookups stopped after %d: %s", done, exc)
            break
        conn.execute("""UPDATE artist_identities SET deezer_id=?, mbid=?, ambiguous_name=?, ids_checked_at=?
                        WHERE id=?""", (found["id"] if found else None, mbid, int(ambiguous), _now(), r["id"]))
        conn.commit()
        done += 1
    if done and conn.execute("SELECT 1 FROM artist_identities WHERE ambiguous_name=1 LIMIT 1").fetchone():
        rematch(conn)  # an ambiguous name loses its summary-only matches
    return done


# ---------- dashboard ----------

def listing(conn, taste: listening.Taste | None = None) -> list[dict]:
    """Everyone on the watchlist with live play counts and how many articles mention them in the last 30 days."""
    taste = taste if taste is not None else listening.load()
    since = (datetime.now(timezone.utc) - timedelta(days=30)).replace(microsecond=0).isoformat()
    counts = {r[0]: r[1] for r in conn.execute(
        """SELECT aa.artist_id, COUNT(*) FROM article_artists aa JOIN articles a ON a.id = aa.article_id
           WHERE COALESCE(a.published_at, a.fetched_at) >= ? GROUP BY aa.artist_id""", (since,))}
    out = []
    for r in conn.execute("SELECT * FROM artist_identities"):
        d = dict(r)
        d.update(aliases=json.loads(d["aliases"] or "[]"), muted=bool(d["muted"]), user_edited=bool(d["user_edited"]),
                 common_word=_common(r), common_word_auto=bool(r["common_word"]),
                 ambiguous_name=bool(d["ambiguous_name"]), plays=taste.plays.get(r["key"], 0),
                 articles_30d=counts.get(r["id"], 0), bucket=taste.buckets.get(r["key"]))
        out.append(d)
    out.sort(key=lambda d: (d["tier"] == "dropped", -d["articles_30d"], -d["plays"], d["name"].casefold()))
    return out
