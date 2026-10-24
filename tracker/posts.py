"""Videos you published and how they did, entered by hand.

Nothing here posts or reads TikTok. After publishing a video you log it (ideally from the draft it came
from), then copy numbers from TikTok's analytics into metric snapshots, about 24 hours and 7 days after
posting. Comparisons only use snapshots taken at the same post age, so a day-old video is never
compared with a week-old one, and unrecorded numbers stay NULL instead of counting as zero.
"""
from __future__ import annotations

import json
import statistics
from collections import defaultdict
from datetime import datetime, timezone

from . import config
from .store import now_iso

VIDEO_STYLES = ("voiceover", "slideshow", "footage", "face-to-camera", "other")
COUNT_METRICS = ("views", "likes", "comments", "shares", "saves", "followers_gained")
METRICS = (*COUNT_METRICS, "avg_watch_seconds", "completion_rate")
# Engagement shown per 1,000 views, so a big video and a small one can be compared.
RATE_METRICS = ("likes", "comments", "shares", "saves", "followers_gained")
# Post ages to compare at: name -> (target hours, earliest, latest). The snapshot closest to the target
# inside the window counts; outside every window it's kept but not used for comparisons.
CHECKPOINTS = {"24h": (24, 18, 36), "7d": (168, 120, 240)}
POST_FIELDS = ("url", "title", "posted_at", "format", "video_style", "hook", "length_seconds", "notes", "series")


class PostError(ValueError):
    pass


def parse_iso(value: str) -> datetime:
    """An ISO timestamp with a timezone, as UTC. A bare '2026-10-21T19:00' is refused: which zone?"""
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, ValueError):
        raise PostError(f"not a timestamp: {value!r}") from None
    if dt.tzinfo is None:
        raise PostError(f"timestamp needs a timezone: {value!r}")
    if not 2000 <= dt.year <= 2100:  # also keeps astimezone() away from year-9999 overflow
        raise PostError(f"timestamp out of range: {value!r}")
    return dt.astimezone(timezone.utc)


def _utc(value: str) -> str:
    return parse_iso(value).replace(microsecond=0).isoformat()


def _check_fields(fields: dict) -> dict:
    out = dict(fields)
    if "posted_at" in out:
        out["posted_at"] = _utc(out["posted_at"])
    if out.get("video_style") not in (None, *VIDEO_STYLES):
        raise PostError(f"video_style must be one of {', '.join(VIDEO_STYLES)}")
    if out.get("url") and not out["url"].startswith(("https://", "http://")):
        raise PostError("url should be the video's link (https://...)")
    if out.get("length_seconds") is not None and out["length_seconds"] <= 0:
        raise PostError("length_seconds must be positive")
    if "title" in out and not (out["title"] or "").strip():
        raise PostError("title can't be empty")
    if "series" in out:
        out["series"] = (out["series"] or "").strip()[:60] or None
    return out


def create_post(conn, *, alert_id: int | None = None, draft_id: int | None = None, **fields) -> int:
    """Log a published video. With a draft or alert, the title, format and hook default to theirs and the
    alert's facts are frozen into `facts_snapshot` (what we knew at posting time)."""
    fields = {k: v for k, v in fields.items() if k in POST_FIELDS and v is not None}
    if "posted_at" not in fields:
        raise PostError("posted_at is required")
    if draft_id is not None:
        draft = conn.execute("SELECT * FROM drafts WHERE id=?", (draft_id,)).fetchone()
        if not draft:
            raise PostError(f"no draft {draft_id}")
        if alert_id is not None and alert_id != draft["alert_id"]:
            raise PostError(f"draft {draft_id} belongs to alert {draft['alert_id']}, not {alert_id}")
        alert_id = draft["alert_id"]
        fields.setdefault("hook", draft["hook"])
    snapshot = None
    if alert_id is not None:
        alert = conn.execute("SELECT * FROM alerts WHERE id=?", (alert_id,)).fetchone()
        if not alert:
            raise PostError(f"no alert {alert_id}")
        facts = json.loads(alert["facts"])
        fields.setdefault("title", alert["title"])
        fields.setdefault("format", facts.get("format") or alert["kind"])
        series = alert["series"] or config.SERIES_FOR_FORMAT.get(fields["format"])
        if series:
            fields.setdefault("series", series)
        snapshot = alert["facts"]
        from .opportunities import snapshot as queue_snapshot  # local import: opportunities reads posts too
        fields["opportunity_snapshot"] = json.dumps(queue_snapshot(conn, alert_id))
    fields = _check_fields({"title": fields.get("title", ""), **fields})
    cols = ["alert_id", "draft_id", "facts_snapshot", "created_at", *fields]
    cur = conn.execute(f"INSERT INTO posts ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                       [alert_id, draft_id, snapshot, now_iso(), *fields.values()])
    conn.commit()
    return cur.lastrowid


def update_post(conn, post_id: int, **fields) -> None:
    """Edit what you typed. Links to the alert/draft and the facts snapshot never change."""
    fields = _check_fields({k: v for k, v in fields.items() if k in POST_FIELDS})
    if not _exists(conn, post_id):
        raise LookupError(post_id)
    if fields:
        conn.execute(f"UPDATE posts SET {', '.join(f'{k}=?' for k in fields)} WHERE id=?", [*fields.values(), post_id])
        conn.commit()


def delete_post(conn, post_id: int) -> None:
    if not conn.execute("DELETE FROM posts WHERE id=?", (post_id,)).rowcount:  # metrics cascade
        raise LookupError(post_id)
    conn.commit()


def _exists(conn, post_id: int) -> bool:
    return conn.execute("SELECT 1 FROM posts WHERE id=?", (post_id,)).fetchone() is not None


def add_metrics(conn, post_id: int, observed_at: str | None = None, notes: str | None = None, **values) -> int:
    """Record numbers as seen at `observed_at` (default: now). Leave a metric out if you don't have it."""
    post = conn.execute("SELECT posted_at FROM posts WHERE id=?", (post_id,)).fetchone()
    if not post:
        raise LookupError(post_id)
    observed = _utc(observed_at) if observed_at else now_iso()
    if parse_iso(observed) < parse_iso(post["posted_at"]):
        raise PostError("those numbers are dated before the video was posted")
    values = {k: v for k, v in values.items() if k in METRICS and v is not None}
    if not values:
        raise PostError("enter at least one number")
    for k, v in values.items():
        if v < 0 or (k == "completion_rate" and v > 100):
            raise PostError(f"{k} out of range: {v}")
    cols = ["post_id", "observed_at", "notes", *values]
    cur = conn.execute(f"INSERT INTO post_metrics ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                       [post_id, observed, notes, *values.values()])
    conn.commit()
    return cur.lastrowid


def delete_metrics(conn, metrics_id: int) -> None:
    if not conn.execute("DELETE FROM post_metrics WHERE id=?", (metrics_id,)).rowcount:
        raise LookupError(metrics_id)
    conn.commit()


# ---------- reading ----------

def _age_hours(posted_at: str, at: str | datetime) -> float:
    at = at if isinstance(at, datetime) else parse_iso(at)
    return (at - parse_iso(posted_at)).total_seconds() / 3600


def checkpoint_snapshots(post: dict) -> dict[str, dict | None]:
    """For each checkpoint, the snapshot closest to its target age within its window (or None)."""
    out = {}
    for name, (target, lo, hi) in CHECKPOINTS.items():
        inside = [m for m in post["metrics"] if lo <= m["age_hours"] <= hi]
        out[name] = min(inside, key=lambda m: abs(m["age_hours"] - target)) if inside else None
    return out


def list_posts(conn, now: datetime | None = None) -> list[dict]:
    """Posts newest first, each with its snapshots (oldest first), checkpoint picks and which checks are due."""
    now = now or datetime.now(timezone.utc)
    posts = [dict(r) for r in conn.execute("SELECT * FROM posts ORDER BY posted_at DESC, id DESC")]
    metrics = defaultdict(list)
    for r in conn.execute("SELECT * FROM post_metrics ORDER BY observed_at, id"):
        metrics[r["post_id"]].append(dict(r))
    for p in posts:
        p["facts_snapshot"] = json.loads(p["facts_snapshot"]) if p["facts_snapshot"] else None
        p["opportunity_snapshot"] = json.loads(p["opportunity_snapshot"]) if p["opportunity_snapshot"] else None
        p["metrics"] = metrics[p["id"]]
        for m in p["metrics"]:
            m["age_hours"] = round(_age_hours(p["posted_at"], m["observed_at"]), 1)
        p["age_hours"] = round(_age_hours(p["posted_at"], now), 1)
        p["checkpoints"] = {k: (m["id"] if m else None) for k, m in checkpoint_snapshots(p).items()}
        # A check is due once its window opens, until a snapshot lands in it or the window closes.
        p["due"] = [k for k, (_, lo, hi) in CHECKPOINTS.items() if lo <= p["age_hours"] <= hi and not p["checkpoints"][k]]
    return posts


def posts_by_alert(conn) -> dict[int, list[dict]]:
    out = defaultdict(list)
    for r in conn.execute("SELECT id, alert_id, draft_id, url, posted_at FROM posts WHERE alert_id IS NOT NULL ORDER BY posted_at"):
        out[r["alert_id"]].append(dict(r))
    return out


def _median(values: list[float]) -> float | None:
    return round(statistics.median(values), 1) if values else None


GROUP_BY = ("format", "video_style", "pillar", "music_format")


def group_name(post: dict, group_by: str) -> str | None:
    """pillar: events or music (from the format). music_format: the music video format (a story's kind decides
    it: album announcement, collab...), music posts only."""
    from .music_formats import format_key
    music = (post["format"] or "") in config.MUSIC_KINDS
    if group_by == "pillar":
        return "music" if music else "events"
    if group_by == "music_format":
        return format_key({**(post["facts_snapshot"] or {}), "format": post["format"]}) if music else None
    return post[group_by] or "unknown"


def analytics(conn, group_by: str = "format", now: datetime | None = None) -> dict:
    """Compare groups of posts at the same age. Medians, not averages, so one breakout video doesn't
    define a format. Groups with fewer than ANALYTICS_MIN_POSTS posts at a checkpoint are marked
    `enough: False`: there, the numbers describe those posts, they don't predict anything."""
    if group_by not in GROUP_BY:
        raise PostError(f"group_by must be one of {', '.join(GROUP_BY)}")
    groups: dict[str, list[dict]] = defaultdict(list)
    for p in list_posts(conn, now):
        name = group_name(p, group_by)
        if name is not None:
            groups[name].append(p)
    rows = []
    for name, posts in sorted(groups.items()):
        row = {"group": name, "posts": len(posts), "checkpoints": {}}
        for cp in CHECKPOINTS:
            snaps = [s for p in posts if (s := checkpoint_snapshots(p)[cp]) and s["views"]]
            rates = {k: _median([s[k] * 1000 / s["views"] for s in snaps if s[k] is not None]) for k in RATE_METRICS}
            row["checkpoints"][cp] = {
                "n": len(snaps),
                "enough": len(snaps) >= config.ANALYTICS_MIN_POSTS,
                "median_views": _median([s["views"] for s in snaps]),
                "per_1k_views": rates,
            }
        rows.append(row)
    return {"group_by": group_by, "min_posts": config.ANALYTICS_MIN_POSTS,
            "checkpoints": {k: {"target_hours": t, "window_hours": [lo, hi]} for k, (t, lo, hi) in CHECKPOINTS.items()},
            "groups": rows}
