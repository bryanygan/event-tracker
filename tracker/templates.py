"""Deterministic scripts for the formulaic formats (presale, status change).

Why: on the N95 the 3B model got these wrong in ways the fact-checker can't see (it read
"Eagles Presale" as an Eagles show and swapped show and sale dates). These videos are
basically "what, when, where", so Python writes the voiceover straight from the facts and
the model only writes the hook and caption. Every date/venue in these scripts is correct
by construction.

Phrasing varies between alerts (picked by a hash of the artist name, so the same alert always
gets the same wording) so a week of presale videos doesn't sound copy-pasted.
"""
from __future__ import annotations

import hashlib
import re
from collections import OrderedDict

from . import config

TEMPLATED_FORMATS = {"presale", "status_change", "team_week", "big_game"}

_OPENERS = ["Heads up Philly.", "Philly, set an alarm.", "Quick ticket alert, Philly."]
_CLOSERS = ["Don't sleep on this one.", "Save this so you don't miss it.", "Tag who you're going with."]


def _pick(options: list[str], seed: str) -> str:
    return options[int(hashlib.sha1(seed.encode()).hexdigest(), 16) % len(options)]


def pretty_sale_name(name: str) -> str:
    """'LIVE NATION PRESALE' -> 'Live Nation Presale', 'Citi® Cardmember...' -> 'Citi Cardmember...'."""
    name = re.sub(r"[®™]", "", name).strip()
    if name.isupper():
        name = name.title()
    return re.sub(r"\bPre-Sale\b", "Presale", name, flags=re.I)


def _join(items: list[str]) -> str:
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + " and " + items[-1]


def _shows_phrase(shows: list[dict]) -> str:
    """'their Thu, Apr 8 show at Xfinity Mobile Arena' / 'their Sat, Nov 7 and Sun, Nov 8 shows at ...'."""
    venues = list(OrderedDict.fromkeys(s.get("venue") for s in shows if s.get("venue")))
    dates = [s["date"] for s in shows]
    noun = "game" if shows and shows[0].get("kind") == "game" else "show"
    if len(venues) == 1:
        return f"their {_join(dates)} {noun}{'s' if len(dates) > 1 else ''} at {venues[0]}"
    return "their " + _join([f"{s['date']} {noun} at {s.get('venue', 'TBA')}" for s in shows])


def presale_script(facts: dict) -> str:
    artist = facts["artist"]
    sales = facts.get("all_sales") or [{"name": facts["sale_name"], "opens": facts["sale_opens"]}]
    # Group windows that open at the same moment: "Eagles, Radio and Ticketmaster presales open ..."
    by_time: OrderedDict[str, list[str]] = OrderedDict()
    for s in sales:
        by_time.setdefault(s["opens"], []).append(s["name"] if s["name"] == GENERAL else pretty_sale_name(s["name"]))

    lines = [_pick(_OPENERS, artist)]
    if comeback(facts):
        h = facts["history"]
        lines.append(f"{artist} is playing Philly for the first time since {h['last_philly_show_year']}.")
        if h.get("last_philly_show_was_multi_act"):
            lines.append(f"Last time was a {h['last_philly_show_lineup_size']}-act lineup at {h['last_philly_venue']}.")
    first = True
    general_only = all(n == GENERAL for names in by_time.values() for n in names)
    for opens, names in by_time.items():
        presales = [n for n in names if n != GENERAL]
        has_general = GENERAL in names
        if presales:
            label = _presale_label(presales) + (" and the general on-sale" if has_general else "")
            verb = "opens" if len(presales) == 1 and not has_general else "open"
            who = "" if comeback(facts) else f" for {artist}"  # the comeback line already named them
            lines.append(f"The {label}{who} {verb} {opens}." if first
                         else f"Then the {label} {verb} {opens}.")
        elif has_general:
            lines.append(f"{'Tickets' if comeback(facts) else artist + ' tickets'} go on sale to everyone {opens}."
                         if first
                         else f"General on-sale is {opens}.")
        first = False
    lines.append(f"That's for {_shows_phrase(facts['shows'])}.")
    if not general_only:
        lines.append("Presale codes usually go out through the artist's socials and Ticketmaster, so check there.")
    lines.append(_pick(_CLOSERS, artist + "close"))
    return speakable(" ".join(lines))


GENERAL = "General public on-sale"


def _presale_label(names: list[str]) -> str:
    """['Eagles Presale', 'Radio Presale'] -> 'Eagles and Radio presales'."""
    stems = [re.sub(r"\s+presale$", "", n, flags=re.I) for n in names]
    if len(names) > 1 and all(s != n for s, n in zip(stems, names)):
        return _join(stems) + " presales"
    return _join(names)


_DAYS = {"Mon": "Monday", "Tue": "Tuesday", "Wed": "Wednesday", "Thu": "Thursday", "Fri": "Friday",
         "Sat": "Saturday", "Sun": "Sunday"}
_MONTHS = {"Jan": "January", "Feb": "February", "Mar": "March", "Apr": "April", "Jun": "June", "Jul": "July",
           "Aug": "August", "Sep": "September", "Oct": "October", "Nov": "November", "Dec": "December"}


def speakable(text: str) -> str:
    """Voiceover-friendly dates: 'Thu, Oct 22 at 10:00 AM ET' -> 'Thursday, October 22 at 10 AM Eastern'.
    Same facts, just written the way you'd say them."""
    text = re.sub(r"\b(%s)\b(?=,)" % "|".join(_DAYS), lambda m: _DAYS[m.group(1)], text)
    text = re.sub(r"\b(%s)\b(?= \d)" % "|".join(_MONTHS), lambda m: _MONTHS[m.group(1)], text)
    text = re.sub(r"\b(\d{1,2}):00 (AM|PM)\b", r"\1 \2", text)
    return re.sub(r"\b(AM|PM) ET\b", r"\1 Eastern", text)


_CHANGE_WORDS = {"cancelled": "cancelled", "postponed": "postponed", "rescheduled": "rescheduled",
                 "date changed": "moved to a new date"}


def status_change_script(facts: dict) -> str:
    s = facts["show"]
    change = _CHANGE_WORDS.get(facts["change"], facts["change"])
    venue = f" at {s['venue']}" if s.get("venue") else ""
    lines = [f"Quick update if you have {facts['artist']} tickets.",
             f"Their {s['date']} show{venue} has been {change}."]
    if facts["change"] == "cancelled":
        lines.append("Refunds usually go back to your original payment method. Check your email and Ticketmaster.")
    else:
        lines.append("Check your email or Ticketmaster for what happens with your tickets.")
    return speakable(" ".join(lines))


def script_for(facts: dict) -> str | None:
    """The templated voiceover for this alert, or None if the LLM should write it."""
    fmt = facts.get("format")
    if fmt == "presale":
        return presale_script(facts)
    if fmt == "status_change":
        return status_change_script(facts)
    if fmt == "team_week":
        return team_week_script(facts)
    if fmt == "big_game":
        return big_game_script(facts)
    return None


def hook_names(facts: dict) -> list[str]:
    """Names a hook must contain one of: the artist, a team's aliases ("Sixers" / "76ers"), or for a big
    game the opponent ("The Bucks are coming to Philly" is a fine hook)."""
    names = [facts["artist"], *(config.SPORTS_TEAMS.get(facts.get("team"), {}).get("aliases", []))]
    if facts.get("opponent"):
        names.append(facts["opponent"].split()[-1])
    return names


MAX_HOOK_WORDS = 12  # the model once crammed a whole week's schedule into the hook


def game_hashtags(facts: dict) -> list[str]:
    """Hashtags for game videos, from the facts. The model's were unusable for sports
    (#seahawksvsjets, #ClevelandVikings, #NBAPlayoffs in October)."""
    team = facts["artist"]
    opponents = [facts.get("opponent")] if facts.get("opponent") else [g.get("opponent") for g in facts.get("shows", [])]
    tags = ["#philly", f"#{team}", "#NBA", "#PhillySports"]
    tags += [f"#{team}Vs{o.split()[-1]}" for o in opponents if o][:3]
    return list(dict.fromkeys(tags))


def comeback(facts: dict) -> bool:
    """A long enough gap since the artist's last Philly show (from setlist.fm) to mention it."""
    return bool((facts.get("history") or {}).get("is_comeback"))


def _game_when(g: dict) -> str:
    return f"{g['date']} at {g['time']}" if g.get("time") else g["date"]


def team_week_script(facts: dict) -> str:
    team, games = facts["artist"], facts["shows"]
    venues = list(OrderedDict.fromkeys(g.get("venue") for g in games if g.get("venue")))
    same_venue = len(venues) == 1
    lines = [f"{team} home games this week." if len(games) > 1 else f"One {team} home game this week."]
    for g in games:
        where = "" if same_venue else f" at {g.get('venue', 'TBA')}"
        lines.append(f"{_game_when(g)}, the {g.get('opponent') or 'TBA'}{where}.")
    if same_venue:
        lines.append(f"{'All' if len(games) > 1 else 'It'} at {venues[0]}.")
    lines.append("Which one are you going to?" if len(games) > 1 else "Who's going?")
    return speakable(" ".join(lines))


def big_game_script(facts: dict) -> str:
    g, team, opp = facts["show"], facts["artist"], facts["opponent"]
    nickname = opp.split()[-1]
    lines = [f"The {nickname} are coming to Philly.",
             f"The {team} host the {opp} {_game_when(g)} at {g.get('venue', 'TBA')}.",
             "Tickets are on Ticketmaster. Who's going?"]
    return speakable(" ".join(lines))


def fallback_hook(facts: dict) -> str:
    """Used when the model's hook still fails the fact check after a retry."""
    fmt = facts.get("format")
    if fmt == "presale":
        if comeback(facts):  # the comeback *is* the hook
            return f"{facts['artist']}'s first Philly show since {facts['history']['last_philly_show_year']}"
        day = facts["sale_opens"].split(",")[0]  # "Thu"
        return f"Set an alarm: {facts['artist']} tickets drop {day}"
    if fmt == "team_week":
        return f"{facts['artist']} home games this week"
    if fmt == "big_game":
        return f"The {facts['opponent'].split()[-1]} are coming to Philly"
    return f"Update on the {facts['artist']} Philly show"


_CTAS = ["Save this so you don't miss it.", "Tag who you're going with.", "Set your alarm 🔔"]


def caption_for(facts: dict) -> str:
    """Caption for templated formats. Also templated: in testing the 3B model either pasted the whole
    script or paired the wrong time with the wrong sale ("show opens Friday" for a Thursday presale)."""
    artist = facts["artist"]
    if facts.get("format") == "team_week":
        games = " · ".join(f"{g['date'].split(',')[0]} vs {g.get('opponent', 'TBA').split()[-1]}"
                           + (f" {g['time']}" if g.get("time") else "") for g in facts["shows"])
        return f"{artist} at home this week 🏀 {games}. {_pick(_CTAS[:2], artist + facts['week'])}"
    if facts.get("format") == "big_game":
        g = facts["show"]
        return (f"{g['artist']} 🏀 {_game_when(g)} at {g.get('venue', 'TBA')}. "
                f"{_pick(_CTAS[:2], g['artist'] + g['date'])}")
    if facts.get("format") == "presale":
        sales = facts.get("all_sales") or [{"name": facts["sale_name"], "opens": facts["sale_opens"]}]
        first = sales[0]
        parts = [f"{artist} tickets 🎟️ {pretty_sale_name(first['name'])}: {first['opens']}."]
        if comeback(facts):  # setlist.fm's API terms ask for credit where their data is shown
            h = facts["history"]
            lineup = f"a {h['last_philly_show_lineup_size']}-act lineup, " if h.get("last_philly_show_was_multi_act") else ""
            parts.insert(0, f"First Philly show since {h['last_philly_show_year']} ({lineup}per setlist.fm)!")
        general = next((s for s in sales[1:] if s["name"] == GENERAL), None)
        if general:
            parts.append(f"General on-sale: {general['opens']}.")
        show = facts["shows"][0]
        more = f" (+{len(facts['shows']) - 1} more)" if len(facts["shows"]) > 1 else ""
        parts.append(f"Show: {show['date']}{more} at {show.get('venue', 'TBA')}.")
        parts.append(_pick(_CTAS, artist + "cta"))
        return " ".join(parts)
    s = facts["show"]
    return (f"{artist} update: the {s['date']} show at {s.get('venue', 'TBA')} has been "
            f"{_CHANGE_WORDS.get(facts['change'], facts['change'])}. Check your email for details.")
