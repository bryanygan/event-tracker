"""Researched vinyl drafts (vinyl.py's ideas): gather sources, write only from them, check every claim.

1. Sources: the record's Discogs data (pressing, notes, credits, owners), the album's first release date from
   MusicBrainz, and the Wikipedia pages for the album and the artist (free APIs, cached in vinyl_sources).
   "Dig deeper" adds a web-search pass: Claude searches, and only the passages it cites are kept as sources.
2. Writing: Claude (VINYL_STORY_MODEL) writes the script from FACTS and the numbered SOURCES, and lists every
   factual claim with the sources it came from. Nothing else may be stated.
3. Checking: a cheap model (VINYL_CHECK_MODEL) reads each claim against the source text it cites; anything not
   backed becomes a warning on the draft. Numbers in the script that appear in no source are flagged too.

Your opinion is never written for you: the script ends with a "YOUR TAKE:" prompt, as music drafts do.
Drafts only happen when you click (a story is a few cents; Dig deeper about 10-20 cents).
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timedelta, timezone

import requests

from . import config, llm, vinyl
from .search import fold
from .store import now_iso

log = logging.getLogger(__name__)

WIKI_API = "https://en.wikipedia.org/w/api.php"
UA = "PhillyEventsTracker/0.1 ( https://github.com/bryanygan/event-tracker )"
SOURCE_DAYS = 60            # cached sources older than this are fetched again
ALBUM_CHARS = 12_000        # a story's album page (the whole article, minus references, for most albums)
ARTIST_CHARS = 5_000
LIST_INTRO_CHARS = 1_500    # list formats: each album's opening paragraphs only
TAKE = "YOUR TAKE:"
_MUSIC_WORDS = re.compile(r"\b(?:album|mixtape|EP|extended play|single|record|soundtrack|compilation)\b", re.I)
_ARTIST_WORDS = re.compile(r"\b(?:rapper|singer|songwriter|musician|band|producer|DJ|duo|group|artist|composer)\b", re.I)
_CUT = re.compile(r"\n==+\s*(?:References|Notes|Footnotes|See also|External links|Citations|Sources)\s*==+.*", re.S)


# ---------- sources ----------

def _wiki(params: dict) -> dict:
    r = requests.get(WIKI_API, params={**params, "format": "json", "formatversion": 2},
                     headers={"User-Agent": UA}, timeout=20)
    r.raise_for_status()
    return r.json()


def wiki_search(query: str, limit: int = 5) -> list[str]:
    return [h["title"] for h in _wiki({"action": "query", "list": "search", "srsearch": query,
                                       "srlimit": limit}).get("query", {}).get("search", [])]


def wiki_extract(title: str) -> tuple[str, str, str] | None:
    pages = _wiki({"action": "query", "prop": "extracts", "explaintext": 1, "redirects": 1,
                   "titles": title}).get("query", {}).get("pages", [])
    if not pages or pages[0].get("missing") or not pages[0].get("extract"):
        return None
    t = pages[0]["title"]
    return t, _CUT.sub("", pages[0]["extract"]).strip(), "https://en.wikipedia.org/wiki/" + t.replace(" ", "_")


def _base(title: str) -> str:
    return fold(re.sub(r"\s*\([^)]*\)$", "", title))


def find_album_page(artist: str, album: str) -> tuple[str, str, str] | None:
    """The album's Wikipedia article (`artist`: the main credited artist). The title must match, ignoring a
    "(… album)" tag; or one title contains the other (Discogs' "Night Ferry Deluxe Tapes", Wikipedia's "Night Ferry")
    and the article's opening uses Discogs' full title. The text must name the artist and call it an album,
    mixtape or EP near the top. Else None: a wrong page is worse than none."""
    want, tried = fold(album), set()
    for q in (f'"{album}" {artist} album', f"{album} {artist}"):
        for t in wiki_search(q):
            base = _base(t)
            if t in tried or not base or not (base == want or base in want or want in base):
                continue
            tried.add(t)
            page = wiki_extract(t)
            if not page or fold(artist) not in fold(page[1][:4000]) or not _MUSIC_WORDS.search(page[1][:800]):
                continue
            if base == want or want in fold(page[1][:1500]):
                return page
    return None


def find_artist_page(artist: str) -> tuple[str, str, str] | None:
    main = artist
    for t in wiki_search(f"{main} musician", 6):
        if _base(t) != fold(main):
            continue
        page = wiki_extract(t)
        if page and _ARTIST_WORDS.search(page[1][:800]):
            return page
    return None


def discogs_source(row) -> dict:
    """The record's own Discogs data as a source text (already in the database; no call)."""
    labels = json.loads(row["labels"] or "[]")
    lines = [f"Release: {row['artist']} – {row['title']}",
             f"Format: {vinyl.pressing(json.loads(row['formats'] or '[]'))}",
             f"Label: {', '.join(f'{l['name']} ({l['catno']})' if l.get('catno') else l['name'] for l in labels)}"]
    for k, label in (("released", "This pressing released"), ("country", "Country")):
        if row[k]:
            lines.append(f"{label}: {row[k]}")
    if row["have"] is not None:
        lines.append(f"Discogs users who have this pressing: {row['have']}; who want it: {row['want']}")
    if row["rating_count"]:
        lines.append(f"Discogs rating: {row['rating_avg']:.2f} out of 5 from {row['rating_count']} ratings")
    price, grade = vinyl.suggested_price(row)
    if price:
        lines.append(f"Discogs suggested price ({grade}), from recent sales: ${price:.2f}")
    if row["lowest_price"]:
        lines.append(f"Cheapest copy for sale on Discogs now: ${row['lowest_price']:.2f} ({row['num_for_sale']} for sale)")
    credits = json.loads(row["credits"] or "[]")
    if credits:
        lines.append("Credits: " + "; ".join(f"{c['role']}: {c['name']}" for c in credits[:25]))
    if row["notes"]:
        lines.append(f"Release notes: {row['notes']}")
    return {"kind": "discogs", "title": f"Discogs: {row['artist']} – {row['title']}",
            "url": f"https://www.discogs.com/release/{row['release_id']}", "text": "\n".join(lines)}


def _cached(conn, subject: str) -> list[dict]:
    cut = (datetime.now(timezone.utc) - timedelta(days=SOURCE_DAYS)).isoformat()
    return [dict(r) for r in conn.execute(
        "SELECT kind, title, url, text FROM vinyl_sources WHERE subject=? AND fetched_at >= ? ORDER BY id", (subject, cut))]


def _store(conn, subject: str, src: dict) -> None:
    conn.execute("""INSERT INTO vinyl_sources (subject, kind, title, url, text, fetched_at) VALUES (?,?,?,?,?,?)
                    ON CONFLICT(subject, url) DO UPDATE SET title=excluded.title, text=excluded.text,
                    fetched_at=excluded.fetched_at""",
                 (subject, src["kind"], src["title"], src["url"], src["text"], now_iso()))
    conn.commit()


def gather(conn, row, intro_only: bool = False) -> list[dict]:
    """Sources for one record: Discogs (always fresh from the database), MusicBrainz's date, Wikipedia (cached)."""
    subject = f"release:{row['release_id']}"
    out = [discogs_source(row)]
    if row["original_date"]:
        out.append({"kind": "musicbrainz", "title": "MusicBrainz: first release",
                    "url": f"https://musicbrainz.org/release-group/{row['mb_release_group']}",
                    "text": f"{row['artist']} – {vinyl.album_title(row['title'])} was first released {row['original_date']}."})
    looked = {s["url"] for s in _cached(conn, subject) if s["kind"] == "checked"}
    album = vinyl.album_title(row["title"])
    # (marker, finder, kind): a marker row remembers a lookup that found nothing, so it isn't repeated daily
    main = vinyl.main_artist(row)
    lookups = [("checked:album", lambda: find_album_page(main, album), "wikipedia_album")]
    if not intro_only:
        lookups.append(("checked:artist", lambda: find_artist_page(main), "wikipedia_artist"))
    for marker, finder, kind in lookups:
        if marker in looked:
            continue
        try:
            found = finder()
        except requests.RequestException as exc:  # try again next time; the draft goes ahead without it
            log.warning("Wikipedia lookup failed for %s: %s", subject, exc)
            continue
        if found:
            limit = ALBUM_CHARS if kind == "wikipedia_album" else ARTIST_CHARS
            _store(conn, subject, {"kind": kind, "title": f"Wikipedia: {found[0]}", "url": found[2],
                                   "text": found[1][:limit]})
        _store(conn, subject, {"kind": "checked", "title": "looked up", "url": marker, "text": ""})
    for s in _cached(conn, subject):
        if s["kind"] == "wikipedia_album":
            out.append({**s, "text": s["text"][:LIST_INTRO_CHARS] if intro_only else s["text"]})
        elif s["kind"] == "wikipedia_artist" and not intro_only:
            out.append(s)
    return out


# ---------- dig deeper: a web-search pass ----------

def _log_call(conn, model: str, usage, error: str | None) -> None:
    price_in, price_out = config.CLAUDE_PRICES.get(model, (0.0, 0.0))
    tin, tout = (usage.input_tokens, usage.output_tokens) if usage else (None, None)
    searches = getattr(getattr(usage, "server_tool_use", None), "web_search_requests", 0) or 0 if usage else 0
    cost = ((tin * price_in + tout * price_out) / 1e6 + searches * config.WEB_SEARCH_PRICE) if usage else None
    conn.execute("INSERT INTO llm_calls (at, model, input_tokens, output_tokens, cost_usd, error) VALUES (?,?,?,?,?,?)",
                 (now_iso(), model, tin, tout, cost, error))
    conn.commit()


WEB_PROMPT = """Research the vinyl record below for a short storytelling video. Find well-sourced facts about:
how the album was made (recording, producers, collaborators, samples, what the artist said about it), its release
and reception, anything notable about this specific pressing or its packaging, and any lore fans tell about it.

Prefer interviews, reputable music press, the label's or artist's own pages, and Wikipedia's cited sources. Skip
fan wikis, forums, lyric sites and social posts unless nothing else covers a point, and say when a story is a
rumor. Write 8-15 short factual sentences, each supported by your search results. If you find nothing reliable
on a point, leave it out. Don't write the script.

RECORD:
{record}"""


def web_research(conn, row) -> list[dict]:
    """One Claude call with web search; the cited passages become sources (kind "web"). Returns them."""
    import anthropic
    client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY, timeout=240.0)
    record = json.dumps(vinyl.record_facts(row, ("counts",)), ensure_ascii=False, indent=2)
    messages = [{"role": "user", "content": WEB_PROMPT.format(record=record)}]
    cited: dict[str, dict] = {}
    for _ in range(3):  # a long search turn can pause; resend to let it finish
        usage, error = None, None
        try:
            resp = client.messages.create(
                model=config.VINYL_STORY_MODEL, max_tokens=8000,
                # The basic search tool, on purpose: with web_search_20260209 the results are filtered through
                # server-side code and the text came back with no citations (checked 2026-10-28), so there was
                # nothing to check claims against. This one cites the passage behind each sentence.
                tools=[{"type": "web_search_20250305", "name": "web_search", "max_uses": config.VINYL_WEB_SEARCHES}],
                messages=messages)
            usage = resp.usage
        except Exception as exc:
            error = str(exc)[:500]
            raise
        finally:
            _log_call(conn, config.VINYL_STORY_MODEL, usage, error)
        for block in resp.content:
            for c in getattr(block, "citations", None) or []:
                url = getattr(c, "url", None)
                if not url or not getattr(c, "cited_text", None):
                    continue
                entry = cited.setdefault(url, {"kind": "web", "title": getattr(c, "title", None) or url, "url": url,
                                               "text": ""})
                if c.cited_text not in entry["text"]:
                    entry["text"] += c.cited_text.strip() + "\n"
        if resp.stop_reason != "pause_turn":
            break
        messages = [messages[0], {"role": "assistant", "content": resp.content}]
    subject = f"release:{row['release_id']}"
    for src in cited.values():
        _store(conn, subject, src)
    return list(cited.values())


# ---------- writing ----------

SYSTEM_PROMPT = """You write TikTok scripts for a vinyl collector's page. The creator collects rap, hyperpop, pop,
R&B, EDM and indie pop records, and lives in Philadelphia.
Voice: a storyteller who loves records, talking to a friend. Casual, specific, never corporate.
Rules:
- Every factual statement must come from FACTS or the numbered SOURCES. Never invent dates, numbers, sales, chart
  positions, quotes, names, places or behind-the-scenes events. Never fill a gap from memory.
- If the sources are thin, tell a shorter story. A short true story beats a long padded one.
- If two sources disagree, leave the point out or say they disagree.
- Opinions belong to someone: quote a critic only if a source says it, and say who. Don't call the music good or
  bad yourself, and never write the creator's feelings ("what I love", "for me", "my favorite"): their take is
  added after the script.
- The creator owns the record in FACTS: you can say "I've got the ... pressing" using FACTS exactly.
- Just tell the story. Don't talk about your sources or research in the script ("Wikipedia says", "I can't
  confirm"): leave out what you can't back up, silently. Name a source only to credit an opinion (a critic) or
  for Discogs' numbers and prices.
- Prices are Discogs' suggested prices from recent sales, or the cheapest copy for sale: say which.
- Refer to artists by name or "they"; don't guess he/she.
- No links or [placeholders]: the caption is posted as-is.
- The hook is the first 1-2 seconds: under 12 words, specific, makes people stop scrolling. Don't call anything
  viral or trending.
- List every factual claim the script, hook and caption make in "claims", each with the numbers of the sources
  it comes from (0 = FACTS). One claim per fact. A claim you can't source must not be in the script."""

GUIDES = {
    "vinyl_lore": "The story behind this record, 60-90 seconds spoken. Open on the most surprising true detail. Then "
                  "how the album came together and what happened when it came out, in order, with specifics from the "
                  "sources. Then the pressing the creator owns. End on a question for the comments.",
    "vinyl_underappreciated": "Why this album deserves more attention, 45-75 seconds. What it is and where it sits in "
                              "the artist's story, the details that make it interesting, then the pressing. The "
                              "'underappreciated' framing is the creator's view: the evidence is FACTS (few Discogs "
                              "owners, a high rating). Don't say it flopped or was overlooked unless a source does.",
    "vinyl_underrated": "Underrated vinyl of the day, 20-35 seconds: one quick, specific reason to know this album, "
                        "then the pressing and how few Discogs users own it.",
    "vinyl_anniversary": "An anniversary video, 45-75 seconds: '{years} years ago ...' using FACTS.anniversary exactly. "
                         "Then the album's story from the sources, then the pressing the creator owns.",
    "vinyl_unique": "A countdown of the most unique pressings, #{n} to #1, 45-75 seconds. For each: what makes the "
                    "pressing unusual (FACTS.what_makes_it_unusual, exact) and one short true detail about the album "
                    "from the sources if there is one.",
    "vinyl_value": "A countdown of the most valuable records, #{n} to #1, 45-75 seconds. Say each price as Discogs' "
                   "suggested price from recent sales (FACTS.price_condition), rounded to the dollar. Mention the "
                   "whole collection's Discogs estimate if FACTS has it.",
    "vinyl_coveted": "The records in the collection the most Discogs users want, #{n} to #1, 45-75 seconds, with the "
                     "want counts from FACTS and one short true detail per album from the sources if there is one.",
    "vinyl_variants": "The creator owns more than one pressing of the same album: compare them, 30-60 seconds. "
                      "What's different about each (FACTS), then ask which one viewers would keep.",
    "vinyl_haul": "A vinyl haul, 30-60 seconds: the new records one by one, each with its pressing and one short "
                  "true detail from the sources if there is one.",
    "vinyl_numbers": "The collection by the numbers, 30-60 seconds, using FACTS only: count, most collected artists, "
                     "top styles, colored vinyl, the oldest album, the Discogs estimated value.",
    "vinyl_look_into": "Three records worth looking into (the creator doesn't own them yet), 30-60 seconds: each "
                       "album with one true detail from the sources and how many Discogs users have or want it.",
}
TAKE_PROMPTS = {
    "vinyl_lore": "when you got this record and what it means to you",
    "vinyl_underappreciated": "why you think this album deserves more love",
    "vinyl_underrated": "your favorite track on it",
    "vinyl_anniversary": "where you were when this came out, or when it clicked for you",
    "vinyl_unique": "which of these you'd grab in a fire",
    "vinyl_value": "which one you'd never sell, whatever it's worth",
    "vinyl_coveted": "how you got your copy of the top one",
    "vinyl_variants": "which pressing is your keeper and why",
    "vinyl_haul": "which pickup you're most excited to spin",
    "vinyl_numbers": "what surprised you about your own numbers",
    "vinyl_look_into": "why these are on your list",
}
DRAFT_SCHEMA = {
    "type": "object",
    "properties": {
        "hook": {"type": "string"}, "script": {"type": "string"},
        "closer": {"type": "string", "description": "the closing question for the comments"},
        "caption": {"type": "string"}, "hashtags": {"type": "array", "items": {"type": "string"}},
        "claims": {"type": "array", "items": {"type": "object", "properties": {
            "text": {"type": "string"}, "sources": {"type": "array", "items": {"type": "integer"}}},
            "required": ["text", "sources"], "additionalProperties": False}},
    },
    "required": ["hook", "script", "closer", "caption", "hashtags", "claims"],
}
CHECK_SCHEMA = {
    "type": "object",
    "properties": {"checks": {"type": "array", "items": {"type": "object", "properties": {
        "claim": {"type": "integer"}, "verdict": {"type": "string", "enum": ["supported", "partly", "unsupported"]},
        "note": {"type": "string"}}, "required": ["claim", "verdict", "note"], "additionalProperties": False}}},
    "required": ["checks"],
}
CHECK_SYSTEM = """You check a video script's factual claims against its sources. For each claim, read the source
text it cites and answer: supported (the sources say this), partly (some of it is there, some isn't, or it's
stretched), or unsupported (not in the cited sources). Be strict about numbers, dates, names and quotes. Judge only
against the text given, never your own knowledge. The creator owns the record described in S0 (FACTS): a claim
that they own it, or have that pressing, is supported when the pressing matches S0. Keep each note under 20 words."""


def prompt_facts(facts: dict) -> dict:
    """What the writer sees: no ids, image links or the dashboard's reasons."""
    def clean(x):
        if isinstance(x, dict):
            return {k: clean(v) for k, v in x.items() if k not in ("release_id", "cover_url", "discogs_url", "why")}
        if isinstance(x, list):
            return [clean(v) for v in x]
        return x
    return clean({k: v for k, v in facts.items() if k != "format"})


def _guide(facts: dict) -> str:
    fmt = facts["format"]
    return GUIDES[fmt].format(n=len(facts.get("records") or []) or 5,
                              years=(facts.get("anniversary") or {}).get("years", ""))


def build_prompt(facts: dict, sources: list[dict], note: str | None = None) -> str:
    numbered = "\n\n".join(f"[S{i}] {s['title']} ({s['url']})\n{s['text']}" for i, s in enumerate(sources, 1))
    prompt = (f"FORMAT: {vinyl.FORMAT_LABEL[facts['format']]}. {_guide(facts)}\n\n"
              f"FACTS (source 0):\n{json.dumps(prompt_facts(facts), indent=2, ensure_ascii=False)}\n\n"
              f"SOURCES:\n{numbered or '(none found: use FACTS only and keep it short)'}")
    if note:
        prompt += ("\n\nCREATOR'S NOTES for this draft (instructions, not facts):\n" + note.strip() +
                   "\nFollow them for emphasis and tone. They never add facts: anything they ask you to say that "
                   "isn't in FACTS or SOURCES stays out.")
    return prompt + "\n\nReturn JSON with hook, script, closer, caption, hashtags and claims."


def _rows_for(conn, facts: dict) -> list:
    ids = [r["release_id"] for r in ([facts["record"]] if facts.get("record") else facts.get("records") or [])
           if r.get("release_id")]
    rows = []
    for rid in ids:
        row = conn.execute("SELECT * FROM vinyl_records WHERE release_id=? ORDER BY in_collection DESC LIMIT 1",
                           (rid,)).fetchone()
        if row:
            rows.append(row)
    return rows


def sources_for(conn, facts: dict, deep: bool = False, progress=lambda *_: None) -> list[dict]:
    rows = _rows_for(conn, facts)
    story = facts.get("record") is not None
    out = []
    for row in rows:
        out += gather(conn, row, intro_only=not story)
    if story and rows:
        web = [s for s in _cached(conn, f"release:{rows[0]['release_id']}") if s["kind"] == "web"]
        if deep:
            progress("Searching the web (Dig deeper)", True)
            web = web_research(conn, rows[0]) or web
        out += web
    if not story and not rows:  # "worth looking into": records you don't own yet
        for r in facts.get("records") or []:
            page = find_album_page(r["artist"], r["title"])
            if page:
                out.append({"kind": "wikipedia", "title": f"Wikipedia: {page[0]}", "url": page[2],
                            "text": page[1][:LIST_INTRO_CHARS]})
    seen, unique = set(), []
    for s in out:
        if s["url"] not in seen:
            seen.add(s["url"])
            unique.append(s)
    return unique


def check_claims(conn, claims: list[dict], sources: list[dict], facts: dict) -> list[dict]:
    """The cheap model's verdict on each claim, against the text of the sources it cites."""
    if not claims:
        return []
    owns = ("Records the creator doesn't own yet" if facts["format"] == "vinyl_look_into"
            else "The creator owns these records")
    texts = {0: f"{owns} (FACTS):\n" + json.dumps(prompt_facts(facts), ensure_ascii=False)}
    texts.update({i: s["text"] for i, s in enumerate(sources, 1)})
    cited = sorted({n for c in claims for n in c["sources"] if n in texts})
    prompt = ("SOURCES:\n" + "\n\n".join(f"[S{n}]\n{texts[n]}" for n in cited) + "\n\nCLAIMS:\n" +
              "\n".join(f"{i}. {c['text']} (cites {', '.join(f'S{n}' for n in c['sources']) or 'nothing'})"
                        for i, c in enumerate(claims)) +
              "\n\nReturn JSON: {\"checks\": [{\"claim\": index, \"verdict\", \"note\"}]} for every claim.")
    out = llm._claude_chat(conn, config.VINYL_CHECK_MODEL, prompt, CHECK_SCHEMA, CHECK_SYSTEM, max_tokens=4000)
    verdicts = {c["claim"]: c for c in out.get("checks", [])}
    return [{**c, "verdict": verdicts.get(i, {}).get("verdict", "unsupported"),
             "note": verdicts.get(i, {}).get("note", "not checked")} for i, c in enumerate(claims)]


_NUMBER = re.compile(r"(?<![\w.])\$?\d[\d,]*(?:\.\d+)?")


def number_warnings(text: str, facts: dict, sources: list[dict]) -> list[str]:
    """Numbers in the script that appear in no source or fact (a cheap second net under the claim check).
    Rounding is fine: "$153" for Discogs' 152.91 passes, "2019" for 2018 doesn't."""
    pool = re.sub(r"(?<=\d),(?=\d)", "", json.dumps(facts, ensure_ascii=False) + " ".join(s["text"] for s in sources))
    known = {float(x) for x in re.findall(r"\d+(?:\.\d+)?", pool)}
    out = []
    for m in sorted(set(_NUMBER.findall(text))):
        n = m.lstrip("$").rstrip(",.").replace(",", "")
        if len(n.replace(".", "")) < 2 or any(abs(float(n) - k) < 1 for k in known):
            continue
        out.append(f"Check the number {m.rstrip(',.')}: it isn't in the sources")
    return out


def onscreen(facts: dict) -> list[str]:
    recs = facts.get("records") or []
    if facts.get("record"):
        r = facts["record"]
        lines = [f"{r['artist']} – {vinyl.album_title(r['title'])}"]
        if facts.get("anniversary"):
            lines.append(f"{facts['anniversary']['years']} years · first released {facts['anniversary']['first_released']}")
        if r.get("pressing"):
            lines.append(r["pressing"])
        return lines
    if facts["format"] == "vinyl_numbers":
        return [f"{facts['record_count']} records"] + [f"{a['artist']} · {a['records']}" for a in
                                                      facts.get("most_collected_artists", [])[:3]]
    out = []
    for i, r in enumerate(recs):  # records are best first; a countdown shows them #5 to #1
        rank = f"#{i + 1} " if facts["format"] in ("vinyl_unique", "vinyl_value", "vinyl_coveted") else ""
        extra = (f" · ${round(r['suggested_price_usd'])}" if r.get("suggested_price_usd") else
                 f" · {r['discogs_want']:,} want it" if facts["format"] == "vinyl_coveted" and r.get("discogs_want") else "")
        out.append(f"{rank}{r['artist']} – {vinyl.album_title(r['title'])}{extra}")
    return list(reversed(out)) if facts["format"] in ("vinyl_unique", "vinyl_value", "vinyl_coveted") else out


def hashtags(raw: list[str], facts: dict) -> list[str]:
    base = ["#vinyl", "#vinylcollection", "#vinylcommunity", "#recordcollection"]
    tags = []
    for t in list(raw) + base:
        t = "#" + re.sub(r"[^\w]", "", t.lstrip("#"))
        if len(t) > 1 and t.lower() not in {x.lower() for x in tags}:
            tags.append(t)
    return tags[:8]


def draft(conn, alert, facts: dict, progress=lambda *_: None, instructions: dict | None = None,
          deep: bool = False) -> int:
    """Research, write and check one vinyl draft. Returns the draft id. Raises when Claude isn't available."""
    if not llm.uses_claude():
        raise RuntimeError("Vinyl drafts are researched and written by Claude: ANTHROPIC_API_KEY isn't set")
    needed = 3 + (2 if deep else 0)
    if llm.claude_calls_today(conn) + needed > config.CLAUDE_DAILY_CAP:
        raise RuntimeError(f"Claude's daily cap ({config.CLAUDE_DAILY_CAP} calls) is nearly used up; try tomorrow")
    progress("Gathering sources (Discogs, MusicBrainz, Wikipedia)")
    sources = sources_for(conn, facts, deep, progress)
    note = (instructions or {}).get("note")
    progress("Writing the script from the sources")
    out = llm._claude_chat(conn, config.VINYL_STORY_MODEL, build_prompt(facts, sources, note), DRAFT_SCHEMA,
                           SYSTEM_PROMPT, max_tokens=8000, timeout=180.0)
    progress("Checking every claim against the sources")
    claims = [{"text": c["text"], "sources": [n for n in c.get("sources", []) if 0 <= n <= len(sources)]}
              for c in out.get("claims", [])]
    checked = check_claims(conn, claims, sources, facts)
    warnings = [f"Not in the sources: {c['text']}" if c["verdict"] == "unsupported"
                else f"Partly backed: {c['text']} ({c['note']})" for c in checked if c["verdict"] != "supported"]
    body = " ".join(str(out.get(k, "")) for k in ("hook", "script", "closer", "caption"))
    warnings += number_warnings(body, prompt_facts(facts), sources)
    if not sources:
        warnings.append("No sources found beyond Discogs' data: the story is short on purpose. Try Dig deeper.")
    script = (f"{llm.spoken(out['script'])}\n\n{TAKE} {TAKE_PROMPTS[facts['format']]}\n\n"
              f"{llm.spoken(out.get('closer', ''))}").strip()
    tags = re.findall(r"(?<!\w)#\w+", out["caption"])
    caption = re.sub(r"[ \t]*(?<!\w)#\w+", "", out["caption"]).strip()
    record = {"sources": [{"n": i, "title": s["title"], "url": s["url"], "kind": s["kind"]}
                          for i, s in enumerate(sources, 1)],
              "claims": checked, "deep": deep}
    progress("Saving the draft")
    model = config.VINYL_STORY_MODEL + (" + web search" if deep else "")
    cur = conn.execute(
        """INSERT INTO drafts (alert_id, format, model, hook, script, caption, hashtags, onscreen, warnings, created_at,
                               facts, instructions, sources) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (alert["id"], facts["format"], model, llm.spoken(out["hook"]), script, caption,
         " ".join(hashtags(list(out.get("hashtags", [])) + tags, facts)), json.dumps(onscreen(facts)),
         json.dumps(warnings), now_iso(), alert["facts"], json.dumps(instructions) if instructions else None,
         json.dumps(record)))
    conn.execute("UPDATE alerts SET status='drafted' WHERE id=? AND status='new'", (alert["id"],))
    conn.commit()
    return cur.lastrowid
