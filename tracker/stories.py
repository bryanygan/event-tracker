"""Music stories (docs/agent-notes/music-roadmap.md M3): classify watchlist articles, group the ones about the same
thing into one story, and track a release's status. Transparent rules, no LLM: each story says which rule fired.

- Types, first rule that matches wins (order matters: "accused of stealing a hook" is legal, not a release).
  Release-family types share stories: an announcement, its tracklist and the release can be one card.
- Release status only moves forward (rumored < teased < announced < scheduled < released). Two different release
  dates from different articles mark the story `conflicting`; the first stated date is kept, the other is evidence.
- Clustering: same primary artist (the watchlist artist named first in the headline, or the same headline from
  another outlet), compatible type, and the same release title when both have one, within STORY_WINDOW_HOURS
  (longer for a release cycle).
- Sensitive stories (deaths, arrests, lawsuits, health) are flagged and never auto-promoted.

Stories are rebuilt from the last STORY_DAYS of articles after every news check (well under a second). A story
you acted on keeps its id, feedback and alert link, because its key comes from its first article.
"""
from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta, timezone

STORY_DAYS = 30
STORY_WINDOW_HOURS = 72
RELEASE_WINDOW_DAYS = 21  # an announcement, singles and the album itself, if they name the same release

RELEASE_FAMILY = {"album_announcement", "album_release", "single_release", "tracklist", "tease", "collaboration"}
STATUS_ORDER = ["rumored", "teased", "announced", "scheduled", "released"]
# Within a release story, the card shows the most advanced kind of news
TYPE_RANK = ["tease", "collaboration", "single_release", "album_announcement", "tracklist", "album_release"]

_ALBUM = r"(?:albums?|lps?|eps?|mixtapes?|records?|projects?)"
# (type, label, pattern). Matched against "headline. summary" (headline first), case-insensitive.
RULES: list[tuple[str, str, re.Pattern]] = [(t, label, re.compile(p, re.I)) for t, label, p in [
    ("politics", "politics", r"\b(trump|biden|harris|white house|election|senat\w*|congress\w*|democrat\w*|"
                             r"republican\w*|politic\w*|protests?|maga|president(?:ial)?)\b"),
    ("legal", "legal", r"\b(lawsuits?|sued|sues|suing|court|trial|judge|sentenc\w*|prison|jail\w*|charged|charges|"
                       r"indict\w*|arrest\w*|plea|copyright infringement|accus\w*|allegations?|alleged(?:ly)? stole|"
                       r"kidnap\w*|snitch\w*|warden|lawyer|attorney|steal|stole|stolen|theft|plagiari\w*)\b"),
    ("sensitive", "death or health", r"\b(dies|died|death|dead at|passed away|killed|shot|shooting|hospitali[sz]ed|"
                                     r"overdose|rehab|cancer|diagnos\w*|surgery|health (?:issues?|scare)|"
                                     r"funeral|memorial|tribute to the late)\b"),
    ("roundup", "new-music roundup", r"\b(new music (?:on our radar|friday|this week)|releases you need|"
                                     r"best new (?:songs?|music|tracks?|albums?)|this week'?s (?:best |new )?"
                                     r"(?:songs|music|releases|albums)|songs of the week|weekly playlist)\b"),
    ("tracklist", "tracklist", r"\b(track ?list(?:ing)?)\b"),
    ("anniversary", "anniversary", r"\b(anniversary|\d+ years ago|turns \d+|today in [\w\s-]*history|"
                                   r"years? since)\b"),
    ("review", "review", r"^(?:review\b|.*\balbum review\b)"),
    ("tour", "tour", r"\b(tours?|tour dates|world tour|headlin\w+ (?:tour|run|dates|shows?)|residency|"
                     r"concert dates|north american dates)\b"),
    ("chart", "chart", r"\b(billboard 200|hot 100|charts?|chart-topping|no\. ?1|number one|record-breaking|"
                       r"breaks? (?:a |the )?[\w\s']*records?|streaming records?|debuts at)\b"),
    ("brand_collab", "brand or media tie-in", r"\b(lego|partnership|partners with|merch\w*|sneakers?|capsule|"
                                              r"brand|campaign|ambassador|video game|gta ?\d*|fortnite|"
                                              r"(?:clothing|fashion) line|cast (?:in|as)|acting debut|role|"
                                              r"collab(?:oration)? with (?:nike|adidas|puma)|netflix|documentary|"
                                              r"film|movie|series|soundtrack|cereal|menu|restaurant)\b"),
    # "out now" beats "new album", unless the headline is announcing ("announces new album, shares first single")
    ("album_release", "album out", rf"^(?!.*\b(?:announc\w*|reveal\w*|unveil\w*|details?|upcoming|forthcoming|"
                                   rf"will|due|set to|sets)\b)(?:.*?\b{_ALBUM}\b[^.]*?\b(out now|is (?:here|out)|"
                                   rf"arrives?|has arrived|finally (?:here|out))\b|.*?\b(releases?|released|drops?|"
                                   rf"dropped|shares?|surprise[- ]releases?|delivers?)\b[^.]*?\b(new |debut |"
                                   rf"surprise |deluxe )?{_ALBUM}\b|.*?\bdeluxe\b)"),
    ("album_announcement", "album announced", rf"\b(announc\w*|reveal\w*|unveil\w*|details?|sets?|confirms?)\b"
                                              rf"[^.]*?\b(new |debut |sophomore |third |fourth |next )?{_ALBUM}\b|"
                                              rf"\b(new|upcoming|forthcoming|next) {_ALBUM}\b|\brelease date\b|"
                                              rf"\b(pushed (?:back|to)|delayed|postponed|new date)\b"),
    ("tease", "tease", r"\b(teas\w*|hints?|hinted|cryptic|snippets?|previews?|coming soon|countdown|"
                       r"working on|in the studio)\b"),
    ("collaboration", "collaboration", r"\b(feat\.|ft\.|featuring|teams? up|link(?:s|ed)? up|collab\w*|duet|"
                                       r"recruits?|joins forces|joined by)\b"),
    ("single_release", "new song or video", r"\b(new (?:song|single|track|video|music)|shares?|drops?|"
                                            r"releases?|premieres?|unveils?|debuts?|hear|listen|watch|"
                                            r"music video|visuals?|remix|freestyle|song by)\b"),
    ("artist_development", "career news", r"\b(signs?|signed|signing|label|leaves?|left|splits?|hiatus|"
                                          r"retir\w*|breaks? up|reunion|reunit\w*|new name|joins)\b"),
    ("lifestyle", "lifestyle", r"\b(birthday|bday|wedding|engaged|engagement|pregnan\w*|baby|cars?|mansion|"
                               r"house|vacation|travel\w*|outfit|fashion show|dating|girlfriend|boyfriend|"
                               r"buys?|bought|net worth|jewelry|chain)\b"),
]]
TYPES = [r[0] for r in RULES] + ["other"]
SENSITIVE_TYPES = {"legal", "sensitive"}
RUMOR = re.compile(r"\b(reportedly|rumou?r\w*|allegedly|sources say|speculat\w*|could be|might be|is said to)\b", re.I)
RELEASED = re.compile(r"\b(out now|is (?:here|out)|arrives?|has arrived|shares?|drops?|dropped|releases?|released|"
                      r"premieres?|hear|listen|stream|song by)\b", re.I)

MONTHS = {m: i for i, m in enumerate(["january", "february", "march", "april", "may", "june", "july", "august",
                                      "september", "october", "november", "december"], 1)}
_MONTH = r"(january|february|march|april|may|june|july|august|september|october|november|december)"
DATE_RX = re.compile(rf"\b(?:out|due|arrives?|arriving|drops?|dropping|releas\w*|coming|on|for)\s+(?:on\s+)?"
                     rf"(?:\w+day,?\s+)?{_MONTH}\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?(?:,?\s+(\d{{4}}))?", re.I)
QUOTED = re.compile(r"[\"“]([^\"”]{2,80})[\"”]|‘([^’]{2,80})’")
# The keywords ignore case ("New Album"); the title itself must start with a capital or digit
NAMED = re.compile(rf"\b(?i:new|debut|sophomore|third|fourth|next|upcoming|surprise|deluxe)\s+(?i:{_ALBUM})\s+"
                   rf"([A-Z0-9][^:,;.!?\-–—(]{{1,60}}?)(?=\s*(?::|,|;|\.|!|\?|\(|$|\s+(?:out|due|on|with|via|"
                   rf"arrives|feat\.|ft\.|featuring|is|and)\b))")


SINGLE_SUFFIX = re.compile(r"\s+-\s+(single|ep)\s*$", re.I)  # iTunes: "Glass Hours - Single"
SONG_BY = re.compile(r"^(.+?)\s+[–—-]\s+(?:Song|Single|Album|EP) by\b", re.I)  # "Glass Hours – Song by Nova Vale"


def classify(title: str, excerpt: str | None = None, source_key: str = "") -> tuple[str, str]:
    """(story type, why). The headline decides first. The summary only if the headline matched nothing, and then
    not for release types: summaries mention songs and features in passing ("his playlist featuring...")."""
    if source_key == "pitchfork-reviews":
        return "review", "review: from Pitchfork's album reviews feed"
    for text in (title, excerpt or ""):
        for story_type, label, rx in RULES:
            if text is not title and story_type in RELEASE_FAMILY:
                continue
            m = rx.search(text)
            if m:
                where = "" if text is title else " (summary)"
                return story_type, f"{label}: '{m.group(0).strip()[:40]}'{where}"
    return "other", "other: no rule matched"


def release_status(story_type: str, title: str, excerpt: str | None) -> str | None:
    """Release-family stories only. Rumor words win over everything else."""
    if story_type not in RELEASE_FAMILY:
        return None
    text = f"{title}. {excerpt or ''}"
    if RUMOR.search(text):
        return "rumored"
    if story_type == "tease":
        return "teased"
    if story_type in ("album_announcement", "tracklist"):
        return "announced"
    if story_type == "album_release" or (story_type in ("single_release", "collaboration") and RELEASED.search(title)):
        return "released"
    return "announced"


def release_title(title: str) -> str | None:
    """A release name the headline states: a quoted title, or "New Album Glass Hours"."""
    m = NAMED.search(title) or SONG_BY.search(title)
    if m:
        return m.group(1).strip()
    m = QUOTED.search(title)
    return (m.group(1) or m.group(2)).strip() if m else None


def release_date(text: str, published: date) -> str | None:
    """"out March 6" → the next March 6 on or after the article (an explicit year wins)."""
    m = DATE_RX.search(text or "")
    if not m:
        return None
    month, day = MONTHS[m.group(1).lower()], int(m.group(2))
    try:
        if m.group(3):
            return date(int(m.group(3)), month, day).isoformat()
        d = date(published.year, month, day)
        if d < published - timedelta(days=7):
            d = date(published.year + 1, month, day)
        return d.isoformat()
    except ValueError:  # "February 30"
        return None


def _family(story_type: str) -> str:
    return "release" if story_type in RELEASE_FAMILY else story_type


def _same_release(a: str | None, b: str | None) -> bool:
    if not a or not b:
        return True
    a, b = re.sub(r"\W+", " ", a.casefold()).strip(), re.sub(r"\W+", " ", b.casefold()).strip()
    return a == b or a in b or b in a


STOPWORDS = set("""about after again against also amid announce announced announces another back been being
    before best calls comes could does during every first from gets gives have here into just like made make makes
    more most much news over really says shares should some still than that their them then there these they this
    those through under until very what when where which while will with without would year years your""".split())


def _words(title: str, names: list[str]) -> set[str]:
    """Meaningful headline words, minus the artists' own names: what two headlines about one thing share."""
    skip = {w for n in names for w in re.findall(r"\w+", n.casefold())}
    return {w for w in re.findall(r"[a-z0-9]+", title.casefold().replace("’", "'"))
            if len(w) >= 4 and w not in STOPWORDS and w not in skip}


def _position(title: str, names: list[str]) -> int:
    t = title.casefold().replace("’", "'")
    found = [t.find(n.casefold().replace("’", "'")) for n in names]
    return min([p for p in found if p >= 0], default=10_000)


def _load(conn, since: str) -> list[dict]:
    """Articles in the window that name a watchlist artist in the headline, oldest first."""
    rows = conn.execute(
        """SELECT a.id, a.title, a.excerpt, a.url, a.title_hash, s.key AS source_key, s.name AS source_name,
                  COALESCE(a.published_at, a.fetched_at) AS seen_at, a.published_at IS NULL AND a.first_read AS undated
           FROM articles a JOIN news_sources s ON s.id = a.source_id
           WHERE COALESCE(a.published_at, a.fetched_at) >= ?
             AND EXISTS (SELECT 1 FROM article_artists aa WHERE aa.article_id = a.id AND aa.location = 'title')
           ORDER BY seen_at, a.id""", (since,)).fetchall()
    tags: dict[int, list] = {}
    if rows:
        for t in conn.execute(
                """SELECT aa.article_id, w.id, w.name, w.aliases FROM article_artists aa
                   JOIN artist_identities w ON w.id = aa.artist_id WHERE aa.location = 'title'"""):
            tags.setdefault(t["article_id"], []).append((t["id"], [t["name"]] + json.loads(t["aliases"] or "[]")))
    out = []
    for r in rows:
        artists = sorted(tags.get(r["id"], []), key=lambda a: _position(r["title"], a[1]))
        if not artists:
            continue
        a = dict(r)
        a["artists"] = [x[0] for x in artists]
        a["words"] = _words(r["title"], [n for x in artists for n in x[1]])
        a["type"], a["rule"] = classify(r["title"], r["excerpt"], r["source_key"])
        a["status"] = release_status(a["type"], r["title"], r["excerpt"])
        a["release_title"] = release_title(r["title"]) if a["type"] in RELEASE_FAMILY else None
        published = datetime.fromisoformat(r["seen_at"]).date()
        a["release_date"] = (release_date(r["title"], published) or release_date(r["excerpt"] or "", published)
                             if a["type"] in RELEASE_FAMILY else None)
        out.append(a)
    return out


def cluster(articles: list[dict]) -> list[dict]:
    """Group articles (oldest first) into stories. Pure: no database."""
    stories: list[dict] = []
    for a in articles:
        when = datetime.fromisoformat(a["seen_at"])
        fam = _family(a["type"])
        home = None
        for s in reversed(stories):
            window = timedelta(days=RELEASE_WINDOW_DAYS) if fam == "release" and a["release_title"] \
                and s["release_title"] else timedelta(hours=STORY_WINDOW_HOURS)
            if when - s["last"] > window:
                continue
            same_headline = a["title_hash"] in s["hashes"]
            # A release story is held together by its release title when both name one; otherwise the headlines
            # need a shared word ("Lego", "collector cars"), so one artist's partnership and film role, or their
            # album and a remix they're featured on, stay separate stories
            both_titled = fam == "release" and a["release_title"] and s["release_title"]
            same_thing = (a["artists"][0] == s["primary"] and fam == s["family"]
                          and (_same_release(a["release_title"], s["release_title"]) if both_titled
                               else bool(a["words"] & s["words"])))
            if same_headline or same_thing:
                home = s
                break
        if home is None:
            home = {"primary": a["artists"][0], "family": fam, "articles": [], "hashes": set(), "artists": [],
                    "release_title": None, "first": when, "last": when, "words": set()}
            stories.append(home)
        home["articles"].append(a)
        home["hashes"].add(a["title_hash"])
        home["words"] |= a["words"]
        home["last"] = max(home["last"], when)
        home["release_title"] = home["release_title"] or a["release_title"]
        for x in a["artists"]:
            if x not in home["artists"]:
                home["artists"].append(x)
    return [_summarize(s) for s in stories]


def _summarize(s: dict) -> dict:
    arts = s["articles"]
    first = arts[0]
    if s["family"] == "release":
        story_type = max((a["type"] for a in arts), key=TYPE_RANK.index)
    else:
        story_type = first["type"]
    rule = next(a["rule"] for a in arts if a["type"] == story_type)
    statuses = [a["status"] for a in arts if a["status"]]
    status = max(statuses, key=STATUS_ORDER.index) if statuses else None
    if status == "rumored" and len(statuses) > statuses.count("rumored"):
        status = max((x for x in statuses if x != "rumored"), key=STATUS_ORDER.index)  # a firm report beats a rumor
    dates = [(a["release_date"], a) for a in arts if a["release_date"]]
    timed = [a for a in arts if not a["undated"]] or arts
    sources = {a["source_key"] for a in arts}
    verification = "corroborated" if len(sources) >= 2 else "unverified"
    if len({d for d, _ in dates}) > 1:
        verification = "conflicting"
    evidence = []
    for a in arts:
        said = {"kind": "article", "provider": a["source_name"], "url": a["url"], "excerpt": a["title"],
                "observed_at": a["seen_at"]}
        for claim in ("release_date", "release_title", "status"):
            if a[claim]:
                evidence.append({**said, "claim": "title" if claim == "release_title" else claim, "value": a[claim]})
    return {
        "key": f"{s['primary']}:{s['family']}:{first['id']}",
        "story_type": story_type, "rule": rule, "primary_artist_id": s["primary"],
        "other_artist_ids": [x for x in s["artists"] if x != s["primary"]],
        "title": first["title"], "release_title": s["release_title"], "release_status": status,
        "release_date": dates[0][0] if dates else None, "verification": verification,
        "sensitive": int(any(a["type"] in SENSITIVE_TYPES for a in arts)),
        # Undated items from before tracking began carry the time we first read the feed; the dated ones say more
        "first_seen": min(a["seen_at"] for a in timed), "last_seen": max(a["seen_at"] for a in timed),
        "dated": int(not all(a["undated"] for a in arts)), "origin": "news", "headlines": [a["title"] for a in arts],
        "article_ids": [a["id"] for a in arts], "evidence": evidence,
    }


# ---------- catalogs (M4): evidence for release stories, and releases no feed covered ----------

CATALOG_RECENT_DAYS = 7     # a catalog-only story for a release out in the last week
CATALOG_AHEAD_DAYS = 120    # or listed for the next four months (an iTunes pre-order)
DATE_TOLERANCE_DAYS = 2     # providers differ by a day or two around midnight (time zones); not a conflict
PROVIDER_NAMES = {"deezer": "Deezer", "itunes": "Apple Music (iTunes)"}


def _days_apart(a: str, b: str) -> int:
    return abs((date.fromisoformat(a) - date.fromisoformat(b)).days)


def _catalog_evidence(rows) -> list[dict]:
    out = []
    for r in rows:
        said = {"kind": "catalog", "provider": PROVIDER_NAMES.get(r["provider"], r["provider"]), "url": r["url"],
                "excerpt": r["title"], "observed_at": r["last_seen"]}
        out += [{**said, "claim": "release_date", "value": r["release_date"]},
                {**said, "claim": "title", "value": r["title"]}]
    return out


def add_catalog(conn, built: list[dict], today: date) -> list[dict]:
    """Check release stories against `releases` (in place) and return catalog-only stories.

    A story whose release title matches a catalog entry gets catalog evidence; its status moves to `scheduled`
    (listed for a later date) or `released`, and it's `confirmed`, unless an article's date and the catalog's are
    more than DATE_TOLERANCE_DAYS apart (or the catalogs disagree that much): then it's `conflicting`. No catalog
    entry changes nothing: an announcement stays announced."""
    from .catalog import FEAT, featured, title_key
    rows = conn.execute("""SELECT r.*, w.name AS artist_name FROM releases r
                           JOIN artist_identities w ON w.id = r.artist_id
                           WHERE w.muted = 0 AND w.tier IN ('core', 'adjacent', 'watch', 'breakout')""").fetchall()
    by_release: dict[tuple[int, str], list] = {}
    for r in rows:
        by_release.setdefault((r["artist_id"], r["title_key"]), []).append(r)
    matched = set()
    for s in built:
        if s["story_type"] not in RELEASE_FAMILY or not s["release_title"]:
            continue
        k = (s["primary_artist_id"], title_key(s["release_title"]))
        found = by_release.get(k)
        if not found:
            continue
        matched.add(k)
        catalog_dates = sorted({r["release_date"] for r in found})
        status = "released" if catalog_dates[0] <= today.isoformat() else "scheduled"
        if s["release_status"] is None or STATUS_ORDER.index(status) > STATUS_ORDER.index(s["release_status"]):
            s["release_status"] = status
        article_dates = {e["value"] for e in s["evidence"] if e["claim"] == "release_date"}
        disagree = _days_apart(catalog_dates[0], catalog_dates[-1]) > DATE_TOLERANCE_DAYS or any(
            min(_days_apart(d, c) for c in catalog_dates) > DATE_TOLERANCE_DAYS for d in article_dates)
        s["verification"] = "conflicting" if disagree or s["verification"] == "conflicting" else "confirmed"
        s["release_date"] = s["release_date"] or catalog_dates[0]
        s["evidence"] += _catalog_evidence(found)

    recent = (today - timedelta(days=CATALOG_RECENT_DAYS)).isoformat()
    ahead = (today + timedelta(days=CATALOG_AHEAD_DAYS)).isoformat()
    headlines = {}  # artist -> [(normalized headline, story)]: a release a headline already names isn't news on its own
    for s in built:
        for h in s.get("headlines", []):
            headlines.setdefault(s["primary_artist_id"], []).append((f" {title_key(h)} ", s))
    out = []
    for (artist_id, key), found in by_release.items():
        found = [r for r in found if r["record_type"] != "compile"]
        dates = sorted({r["release_date"] for r in found})
        if (artist_id, key) in matched or not key or not dates or not recent <= dates[0] <= ahead:
            continue
        named_in = next((s for h, s in headlines.get(artist_id, []) if f" {key} " in h), None)
        if named_in:  # e.g. the single an album announcement says to "hear": evidence there, not a second card
            named_in["evidence"] += _catalog_evidence(found)
            continue
        first = min(found, key=lambda r: (r["release_date"], r["provider"]))
        kind = {"album": "album", "ep": "EP"}.get(first["record_type"], "single")
        name = SINGLE_SUFFIX.sub("", first["title"])
        released = dates[0] <= today.isoformat()
        guest = featured(first["title"], first["artist_name"])
        if guest:  # someone else's track with them on it (catalogs list those under the guest too)
            name = FEAT.sub("", name).strip()
            title = f"{first['artist_name']} is featured on “{name}”" + ("" if released else f", out {dates[0]}")
            when = f"{dates[0]}T04:00:00+00:00" if released else min(r["first_seen"] for r in found)
            story_type = "collaboration"
        elif released:
            title = f"{first['artist_name']} released the {kind} “{name}”"
            when = f"{dates[0]}T04:00:00+00:00"  # midnight Eastern on release day
            story_type = "single_release" if kind == "single" else "album_release"
        else:
            title = f"{first['artist_name']}: {kind} “{name}” is listed for {dates[0]}"
            when = min(r["first_seen"] for r in found)  # when the pre-order showed up
            story_type = "single_release" if kind == "single" else "album_announcement"
        providers = sorted({PROVIDER_NAMES.get(r["provider"], r["provider"]) for r in found})
        out.append({
            "key": f"{artist_id}:catalog:{key}", "story_type": story_type,
            "rule": "catalog: listed on " + " and ".join(providers) + " (no feed covered it)",
            "primary_artist_id": artist_id, "other_artist_ids": [], "title": title, "release_title": name,
            "release_status": "released" if released else "scheduled", "release_date": dates[0],
            "verification": "conflicting" if _days_apart(dates[0], dates[-1]) > DATE_TOLERANCE_DAYS else "confirmed",
            "sensitive": 0, "first_seen": when, "last_seen": when, "dated": 1, "origin": "catalog",
            "article_ids": [], "evidence": _catalog_evidence(found),
        })
    return out


def rebuild(conn, now: datetime | None = None) -> int:
    """Recompute stories from the last STORY_DAYS of articles. Returns how many there are."""
    now = now or datetime.now(timezone.utc)
    since = (now - timedelta(days=STORY_DAYS)).replace(microsecond=0).isoformat()
    built = cluster(_load(conn, since))
    built += add_catalog(conn, built, now.date())
    existing = {r["key"]: r for r in conn.execute("SELECT id, key, feedback, alert_id FROM stories")}
    keep = {k for k, r in existing.items() if r["feedback"] or r["alert_id"]}
    made = set()
    for s in built:
        fields = {k: s[k] for k in ("story_type", "rule", "primary_artist_id", "title", "release_title",
                                    "release_status", "release_date", "verification", "sensitive", "dated", "origin",
                                    "first_seen", "last_seen")}
        fields["other_artist_ids"] = json.dumps(s["other_artist_ids"])
        if s["key"] in existing:
            story_id = existing[s["key"]]["id"]
            conn.execute(f"UPDATE stories SET {', '.join(f'{k}=?' for k in fields)} WHERE id=?",
                         (*fields.values(), story_id))
        else:
            story_id = conn.execute(
                f"INSERT INTO stories (key, {', '.join(fields)}, created_at) VALUES (?, {', '.join('?' * len(fields))}, ?)",
                (s["key"], *fields.values(), now.replace(microsecond=0).isoformat())).lastrowid
        made.add(s["key"])
        conn.execute("DELETE FROM story_articles WHERE story_id=?", (story_id,))
        conn.executemany("INSERT INTO story_articles (story_id, article_id) VALUES (?, ?)",
                         [(story_id, i) for i in s["article_ids"]])
        conn.execute("DELETE FROM story_evidence WHERE story_id=?", (story_id,))
        conn.executemany("""INSERT INTO story_evidence (story_id, kind, provider, url, excerpt, claim, value, observed_at)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                         [(story_id, e["kind"], e["provider"], e["url"], e["excerpt"], e["claim"], e["value"],
                           e["observed_at"]) for e in s["evidence"]])
    stale = [r["id"] for k, r in existing.items() if k not in made and k not in keep]
    if stale:
        conn.execute(f"DELETE FROM stories WHERE id IN ({','.join('?' * len(stale))})", stale)
    conn.commit()
    return len(built)
