"""Two to four ways to frame each video idea, built from the facts with no LLM call.

Each angle is a hook (correct by construction: only names, dates and counts that are in the facts),
a one-line creative brief, a suggested video style and a call to action that fits the format. You pick
one in the dashboard; for templated formats (presale, status, games) that produces a full draft with no
model call at all, and for the rest the model writes the script around the chosen hook.

Angle keys: "news" (what's new, why now), "fan" (why fans care), "utility" (what to do, when),
"comeback" (only when setlist.fm history supports it). Opinion angles are left to you on purpose.
"""
from __future__ import annotations

from . import config, templates

_DAY = {"Mon": "Monday", "Tue": "Tuesday", "Wed": "Wednesday", "Thu": "Thursday", "Fri": "Friday",
        "Sat": "Saturday", "Sun": "Sunday"}


def _day(formatted: str) -> str:
    """'Thu, Oct 22 at 10:00 AM ET' -> 'Thursday'."""
    return _DAY.get(formatted.split(",")[0], formatted.split(",")[0])


def _clock(formatted: str) -> str:
    """'Thu, Oct 22 at 10:00 AM ET' -> '10 AM'."""
    t = formatted.split(" at ")[-1].replace(" ET", "")
    return t.replace(":00", "")


def _angle(key, label, hook, pitch, style, cta) -> dict:
    return {"key": key, "label": label, "hook": hook, "pitch": pitch, "style": style, "cta": cta}


def angles_for(facts: dict) -> list[dict]:
    fmt = facts.get("format")
    if fmt in config.MUSIC_KINDS:  # music ideas have their own (music_formats.py, roadmap M6)
        from . import music_formats
        return music_formats.angles_for(facts)
    artist = facts.get("artist", "")
    history = facts.get("history") or {}
    out = []
    if templates.comeback(facts) and fmt in ("presale", "new_tour"):
        lineup = (f" Last time was a {history['last_philly_show_lineup_size']}-act lineup, so frame it as their own show "
                  "being rare" if history.get("last_philly_show_was_multi_act") else "")
        out.append(_angle("comeback", "Comeback", f"{artist} hasn't played Philly since {history['last_philly_show_year']}",
                          f"Lead with the gap, then the date.{lineup} Credit setlist.fm in the caption.",
                          "face-to-camera", "Comment if you've been waiting for this."))

    if fmt == "presale":
        first = (facts.get("all_sales") or [{"opens": facts["sale_opens"]}])[0]["opens"]
        out += [
            _angle("news", "Breaking", f"{artist} Philly tickets drop {_day(first)}",
                   "Straight news: who, which show, when the sale opens.", "voiceover", "Follow for Philly ticket drops."),
            _angle("fan", "For the fans", f"{artist} fans in Philly, this is your sign",
                   "Talk to the fanbase: the show, the sale, a reason not to miss it.", "face-to-camera",
                   "Tag who you're going with."),
            _angle("utility", "Set a reminder", f"Set an alarm: {_day(first)} at {_clock(first)} for {artist}",
                   "Pure utility: every sale window with times on screen. Made to be saved.", "slideshow",
                   "Save this so you don't miss it."),
        ]
    elif fmt == "new_tour":
        show = facts["shows"][0]
        added = facts.get("already_had_philly_dates")
        out += [
            _angle("news", "Breaking", f"{artist} just {'added another' if added else 'announced a'} Philly show",
                   "Speed matters: post while it's new. Date, venue, on-sale time.", "face-to-camera",
                   "Follow for Philly announcements."),
            _angle("fan", "For the fans", f"{artist} at {show.get('venue', 'TBA')}. Who's coming?",
                   "Show excitement: why this show is a big deal for fans.", "footage",
                   "Tag who you're taking."),
        ]
        if facts.get("public_onsale"):
            # Not "go on sale Friday": facts carry no year, so we can't tell whether the on-sale already passed.
            out.append(_angle("utility", "How to get tickets", f"How to get {artist} tickets",
                              "What to do and when: on-sale time and presales on screen.", "slideshow",
                              "Save this for on-sale day."))
    elif fmt == "weekend" and facts.get("lane"):  # the cut for your genres
        n, lane = len(facts.get("shows", [])), facts["lane"]
        out += [
            _angle("news", "Your lane", f"{n} {lane} shows in Philly this weekend",
                   f"Only {lane}: one line per show, day and venue on screen.", "slideshow", "Save this for the weekend."),
            _angle("fan", "For the fans", f"{lane} fans, your Philly weekend is set",
                   f"Talk to the {lane} crowd only. Lead with the biggest name.", "face-to-camera",
                   "Tag who you're going with."),
        ]
    elif fmt == "weekend":
        n = len(facts.get("shows", []))
        out += [
            _angle("news", "This weekend", f"{n} shows in Philly this weekend",
                   "Quick roundup, one line per show. Try your 3-5 favorites instead of all of them.", "slideshow",
                   "Save this for the weekend."),
            _angle("fan", "Bored in Philly", "Bored this weekend? Philly has plans for you",
                   "Broad 'things to do' energy for people who aren't following any one artist.", "footage",
                   "Send this to your group chat."),
            _angle("utility", "Pick one", "Which Philly show are you hitting this weekend?",
                   "Ask the question, list the options, let the comments argue.", "face-to-camera",
                   "Comment your pick."),
        ]
    elif fmt == "stacked_month":
        month, genre, n = facts["month"].split()[0], facts["genre"], facts.get("notable_show_count")
        out += [
            _angle("news", "Stacked month", f"{month} is stacked for {genre} in Philly",
                   "Build momentum: biggest names first, dates on screen.", "slideshow", "Save this for later."),
            _angle("fan", "For the fans", f"{genre} fans, clear your {month} calendar",
                   "Speak to the genre crowd only. A tight, focused list beats a long one.", "face-to-camera",
                   "Tag your concert friend."),
            _angle("utility", "The list", f"{n} {genre} shows in Philly this {month}",
                   "A save-worthy calendar: one slide per show with date and venue.", "slideshow",
                   "Save this so you don't miss one."),
        ]
    elif fmt == "status_change":
        out += [
            _angle("news", "Update", f"Update on the {artist} Philly show",
                   "Plain news: what changed, nothing more.", "voiceover", "Share this with anyone going."),
            _angle("utility", "Ticket holders", f"{artist} ticket holders, read this",
                   "What changed and where to check: email or Ticketmaster.", "voiceover",
                   "Share this with anyone going."),
        ]
    elif fmt == "team_week":
        n = len(facts.get("shows", []))
        out += [
            _angle("news", "Home week", f"The {artist} are home {n} times this week",
                   "Quick schedule with opponents and tip-off times.", "slideshow", "Save the schedule."),
            _angle("fan", "Pick a game", f"Which {artist} game are you going to?",
                   "Ask, list the games, let fans argue in the comments.", "face-to-camera", "Comment your pick."),
        ]
    if fmt in ("weekend", "stacked_month") and facts.get("picks"):
        k = facts["pick_count"]
        where = (f"for Philly in {facts['month'].split()[0]}" if fmt == "stacked_month" else "for Philly this weekend")
        kind = facts.get("genre") or facts.get("lane")
        hook = f"My {k} {kind + ' ' if kind else ''}picks {where}"
        if len(hook.split()) > templates.MAX_HOOK_WORDS:  # a four-genre lane label
            hook = f"My {k} picks {where}"
        out.append(_angle("picks", f"Top {k}", hook,
                          f"Only the {k} biggest shows, best first. Shorter and more saveable than the full list.",
                          "slideshow", "Save this and tell me what I missed."))

    if fmt == "big_game":
        nick = facts["opponent"].split()[-1]
        out += [
            _angle("news", "Big game", f"The {nick} are coming to Philly",
                   "Matchup, date, time, arena. No records or injuries unless you check them yourself.",
                   "face-to-camera", "Who's going?"),
            _angle("fan", "Rivalry", f"{artist} vs. {nick}. You know what that means",
                   "Rivalry energy and fan reactions; keep the facts to the matchup and date.", "footage",
                   "Tag who you're going with."),
        ]
    return out


def find(facts: dict, key: str) -> dict | None:
    return next((a for a in angles_for(facts) if a["key"] == key), None)


def scoped_facts(facts: dict, angle: dict | None) -> dict:
    """The facts a draft from this angle may use. "picks" keeps only the picked shows (best first), so the
    script, on-screen lines and fact check all stay on them; `picked_from` says how many there were."""
    if not angle or angle["key"] != "picks" or not facts.get("picks"):
        return facts
    out = {k: v for k, v in facts.items() if k not in ("picks", "shows")}
    return {**out, "shows": facts["picks"], "picked_from": len(facts.get("shows", []))}


def template_hashtags(facts: dict) -> list[str]:
    """Hashtags without the model, for templated drafts drafted from an angle."""
    if facts.get("format") in ("team_week", "big_game"):
        return templates.game_hashtags(facts)
    shows = facts.get("shows") or ([facts["show"]] if facts.get("show") else [])
    names = [facts.get("artist")] + [s.get("venue") for s in shows[:1]]
    tags = ["#philly", "#PhillyConcerts"] + ["#" + "".join(ch for ch in n if ch.isalnum()) for n in names if n]
    return list(dict.fromkeys(tags))
