"""Music-news feeds: one conditional GET per source, parsed into plain article dicts (tracker/news.py stores them).

RSS 2.0 and Atom, namespace-agnostic, with the standard library's XML parser (the feeds are clean; a regex
fallback reads RSS <item> blocks if one ever isn't). Only metadata and the feed's own summary are kept, never
article bodies.
"""
from __future__ import annotations

import hashlib
import html
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

import requests

from .. import config


class FeedError(Exception):
    pass


def fetch(session: requests.Session, url: str, etag: str | None = None, last_modified: str | None = None) -> dict:
    """GET the feed with If-None-Match / If-Modified-Since. Returns {"status", "body", "etag", "last_modified"};
    a 304 has no body. Raises FeedError on a network error, another status, or an oversized feed."""
    headers = {"User-Agent": config.NEWS_USER_AGENT}
    if etag:
        headers["If-None-Match"] = etag
    if last_modified:
        headers["If-Modified-Since"] = last_modified
    try:
        with session.get(url, headers=headers, timeout=config.NEWS_TIMEOUT, stream=True) as r:
            if r.status_code == 304:
                return {"status": 304, "body": b"", "etag": etag, "last_modified": last_modified}
            if r.status_code != 200:
                raise FeedError(f"HTTP {r.status_code}")
            body = b""
            for chunk in r.iter_content(65536):
                body += chunk
                if len(body) > config.NEWS_MAX_BYTES:
                    raise FeedError(f"feed larger than {config.NEWS_MAX_BYTES // 1_000_000} MB")
            return {"status": 200, "body": body, "etag": r.headers.get("ETag"),
                    "last_modified": r.headers.get("Last-Modified")}
    except requests.RequestException as exc:
        raise FeedError(type(exc).__name__) from None


def clean(s: str | None, limit: int | None = None) -> str:
    """Tags stripped, entities decoded, whitespace collapsed."""
    s = html.unescape(re.sub(r"<[^>]+>", " ", s or ""))
    s = re.sub(r"\s+", " ", s).strip()
    if limit and len(s) > limit:
        s = s[:limit].rsplit(" ", 1)[0] + "…"
    return s


def parse_date(s: str | None) -> str | None:
    """RFC 822 (RSS) or ISO 8601 (Atom) to UTC ISO. A date without a time zone is taken as UTC."""
    s = (s or "").strip()
    if not s:
        return None
    try:
        dt = parsedate_to_datetime(s)
    except (TypeError, ValueError):
        try:
            dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat()


def title_hash(title: str) -> str:
    """Same headline from two outlets → same hash (case, punctuation and quote styles ignored)."""
    norm = re.sub(r"[^a-z0-9]+", " ", clean(title).casefold()).strip()
    return hashlib.sha1(norm.encode()).hexdigest()[:16]


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def parse(body: bytes) -> list[dict]:
    """Articles as {guid, url, title, excerpt, published_at, categories}. Items without a title or any id are
    dropped. Raises FeedError if nothing in the body looks like a feed."""
    out = []
    try:
        root = ET.fromstring(body)
    except ET.ParseError:
        root = None
    if root is not None:
        if _local(root.tag) not in ("rss", "feed", "RDF"):
            raise FeedError(f"not a feed (<{_local(root.tag)}>)")
        for node in root.iter():
            if _local(node.tag) not in ("item", "entry"):
                continue
            a: dict = {"categories": []}
            for child in node:
                t = _local(child.tag)
                if t == "title":
                    a["title"] = clean("".join(child.itertext()))
                elif t == "link":
                    # Atom: <link rel="alternate" href=...>; RSS: <link>url</link>
                    href = (child.text or "").strip() or child.get("href")
                    if href and child.get("rel", "alternate") == "alternate":
                        a.setdefault("url", href)
                elif t in ("guid", "id"):
                    a["guid"] = (child.text or "").strip()
                elif t in ("pubDate", "published", "date"):
                    a["published_at"] = parse_date(child.text) or a.get("published_at")
                elif t == "updated":  # Atom: only when there's no <published>
                    a["published_at"] = a.get("published_at") or parse_date(child.text)
                elif t in ("description", "summary", "encoded", "content") and not a.get("excerpt"):
                    a["excerpt"] = clean("".join(child.itertext()), config.NEWS_EXCERPT_CHARS)
                elif t == "category":
                    a["categories"].append(clean(child.text or child.get("term")))
            out.append(a)
    else:
        text = body.decode("utf-8", "replace")
        blocks = re.findall(r"<item[\s>].*?</item>", text, re.S)
        if not blocks:
            raise FeedError("unreadable feed")

        def tag(block: str, name: str) -> str:
            m = re.search(rf"<{name}[^>]*>(.*?)</{name}>", block, re.S)
            return re.sub(r"<!\[CDATA\[|\]\]>", "", m.group(1)) if m else ""

        for block in blocks:
            out.append({"title": clean(tag(block, "title")), "url": clean(tag(block, "link")),
                        "guid": clean(tag(block, "guid")), "published_at": parse_date(clean(tag(block, "pubDate"))),
                        "excerpt": clean(tag(block, "description"), config.NEWS_EXCERPT_CHARS), "categories": []})
    articles = []
    for a in out:
        a["guid"] = a.get("guid") or a.get("url") or ""
        a["categories"] = [c for c in a["categories"] if c]
        a.setdefault("published_at", None)
        if a["guid"] and a.get("title"):
            articles.append(a)
    return articles
