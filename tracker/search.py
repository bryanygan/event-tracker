"""Event search: forgiving about case, accents, punctuation and word order.

"renee" finds "Renée Lark", "dr static" and "drstatic" find "Dr. Static", "nova union" finds Nova Vale at Union
Transfer. Every word must match somewhere on the listing (name, headliner, support acts, venue or genre).
dashboard/src/api.ts has a copy of this for the read-only phone snapshot; keep the two in step.
"""
from __future__ import annotations

import re
import unicodedata

FIELDS = ("name", "headliner", "support", "venue_name", "genre", "subgenre")
NICKNAMES = {"sixers": "76ers", "phils": "phillies"}  # what people type -> how Ticketmaster names the team


def fold(text: str | None) -> str:
    """Lowercase, accents off, & as "and", and everything but letters and digits dropped."""
    text = unicodedata.normalize("NFKD", (text or "").replace("&", " and ")).casefold()
    return re.sub(r"[\W_]+", "", "".join(ch for ch in text if not unicodedata.combining(ch)))


def words(q: str) -> list[str]:
    folded = (fold(part) for part in q.replace("&", " and ").split())
    return [NICKNAMES.get(w, w) for w in folded if w]


def matches(event: dict, q: str) -> bool:
    terms = words(q)
    # Fields are kept apart so a word can't match across two of them
    haystack = "|".join(fold(event.get(f)) for f in FIELDS)
    return all(t in haystack for t in terms)
