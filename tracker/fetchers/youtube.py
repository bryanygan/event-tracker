"""YouTube uploads for the music-news job (tracker/news.py; roadmap M8), through the YouTube Data API v3.

youtube.com's robots.txt disallows /feeds/videos.xml, so the channel RSS feeds are off limits; the API is the
sanctioned route. A key is free (Google Cloud console → YouTube Data API v3) and goes in .env as YOUTUBE_API_KEY.
Quota: 10,000 units a day; channels.list and playlistItems.list cost 1 unit each, so reading one channel's latest
uploads is 1 unit (the uploads playlist id is looked up once and cached).

Uploads come back in the same article shape as news_rss.parse, so they're stored, tagged and clustered like any
headline. Shorts and private or deleted videos are left out.
"""
from __future__ import annotations

import re

import requests

from .. import config
from .news_rss import FeedError, clean, parse_date

API = "https://www.googleapis.com/youtube/v3"
CHANNEL_URL = re.compile(r"youtube\.com/channel/(UC[\w-]{22})")
HANDLE_URL = re.compile(r"youtube\.com/(@[\w.-]+)")
USER_URL = re.compile(r"youtube\.com/user/([\w.-]+)")
SKIP_TITLES = {"private video", "deleted video"}
SHORTS = re.compile(r"#shorts?\b", re.I)


class QuotaExceeded(FeedError):
    """The day's API quota is used up: stop reading YouTube until tomorrow."""


def channel_ref(url: str) -> dict | None:
    """What a channel link says about the channel: {"id": UC...}, {"handle": "@x"} or {"user": x}; None for
    links the API can't resolve (custom /c/ URLs, single videos)."""
    for rx, kind in ((CHANNEL_URL, "id"), (HANDLE_URL, "handle"), (USER_URL, "user")):
        m = rx.search(url or "")
        if m:
            return {kind: m.group(1)}
    return None


class YouTubeClient:
    def __init__(self, key: str | None = None, session: requests.Session | None = None):
        self.key = key if key is not None else config.YOUTUBE_API_KEY
        self.session = session or requests.Session()
        self.units = 0

    def _get(self, path: str, params: dict) -> dict:
        self.units += 1
        try:
            r = self.session.get(f"{API}/{path}", params={**params, "key": self.key}, timeout=config.NEWS_TIMEOUT,
                                 headers={"User-Agent": config.NEWS_USER_AGENT, "Accept": "application/json"})
        except requests.RequestException as exc:
            raise FeedError(type(exc).__name__) from None
        if r.status_code == 200:
            return r.json()
        try:
            err = r.json().get("error") or {}
        except ValueError:
            err = {}
        reasons = {e.get("reason") for e in err.get("errors") or []}
        if reasons & {"quotaExceeded", "dailyLimitExceeded", "rateLimitExceeded"}:
            raise QuotaExceeded("YouTube quota used up for today")
        if reasons & {"playlistNotFound", "channelNotFound"}:
            raise FeedError("channel not found or has no uploads")
        # Never echo the request URL: it carries the key
        raise FeedError(f"HTTP {r.status_code}" + (f" ({', '.join(sorted(r for r in reasons if r))})" if reasons else ""))

    def channel(self, ref: dict) -> dict | None:
        """{"id", "title", "uploads"} for a channel_ref(), or None when it doesn't exist."""
        params = {"part": "snippet,contentDetails"}
        params |= {"id": ref["id"]} if "id" in ref else {"forHandle": ref["handle"]} if "handle" in ref \
            else {"forUsername": ref["user"]}
        items = self._get("channels", params).get("items") or []
        if not items:
            return None
        c = items[0]
        return {"id": c["id"], "title": (c.get("snippet") or {}).get("title"),
                "uploads": ((c.get("contentDetails") or {}).get("relatedPlaylists") or {}).get("uploads")}

    def uploads(self, playlist_id: str, limit: int | None = None) -> list[dict]:
        """The latest uploads as articles: {guid, url, title, excerpt, published_at, categories}."""
        body = self._get("playlistItems", {"part": "snippet,contentDetails", "playlistId": playlist_id,
                                           "maxResults": limit or config.YOUTUBE_ITEMS})
        out = []
        for item in body.get("items") or []:
            snip, details = item.get("snippet") or {}, item.get("contentDetails") or {}
            video = details.get("videoId") or (snip.get("resourceId") or {}).get("videoId")
            title = clean(snip.get("title"))
            description = snip.get("description") or ""
            if not video or not title or title.casefold() in SKIP_TITLES or SHORTS.search(f"{title} {description}"):
                continue
            out.append({"guid": f"yt:{video}", "url": f"https://www.youtube.com/watch?v={video}", "title": title,
                        "excerpt": clean(description, config.NEWS_EXCERPT_CHARS) or None,
                        "published_at": parse_date(details.get("videoPublishedAt") or snip.get("publishedAt")),
                        "categories": []})
        return out
