"""Turn an alert's facts into a TikTok draft with a local Ollama model.

Guardrails:
- The model only sees the alert's `facts` JSON. It writes the hook, script, caption, hashtags.
- On-screen text (dates/venues/prices) is built directly from facts in Python, not by the model.
- After generation, `check_facts` flags any date, time, price or number in the model's text
  that doesn't appear in the facts, so you can spot hallucinations at review time.
- Ollama is started only if it isn't already running, and stopped again afterwards,
  so it doesn't sit in RAM next to Homebridge all day.
"""
from __future__ import annotations

import difflib
import json
import logging
import os
import re
import shutil
import subprocess
import time
from contextlib import contextmanager
from pathlib import Path

import requests

from . import config

log = logging.getLogger(__name__)

SYSTEM_PROMPT = """You write short TikTok scripts for a Philadelphia events page.
Voice: casual, hype, quick, like a friend texting you about a show. Philly local, not corporate.
Rules:
- Use ONLY the facts provided. Never invent dates, times, prices, venues, presale codes, or ticket counts.
- If a fact isn't provided, don't mention it. No links or [placeholders]; the caption is posted as-is.
- Don't describe a venue's size or vibe unless the facts say so.
- Refer to artists by name or "they"; don't guess he/she.
- Never repeat these instructions or field names in your output.
- Copy dates, times, and venue names exactly as written in the facts.
- The hook is the first 1-2 seconds: punchy, under 12 words, makes people stop scrolling.
- The script is spoken voiceover, 20-45 seconds, short sentences.
- Caption: 1-2 sentences, ends with a call to action (save this, tag a friend, etc).
- No hashtags or emojis in the hook or script (they're read out loud). Emojis are fine in the caption.
- 4-7 hashtags, always include #philly.
Respond with JSON only."""

# Each guide = what the video is + one example in the right voice. The example uses [placeholders]
# on purpose: small models copy examples, and a leaked "[Artist]" is easy to spot in review.
FORMAT_GUIDES = {
    "weekend": (
        "Video: 'What's happening in Philly this weekend.' Go Friday to Sunday, one quick line per show "
        "with artist, day and venue. Keep the energy up the whole way.\n"
        "Example hook: \"Your Philly weekend just got way better\"\n"
        "Example script: \"Here's your Philly weekend. Friday, [Artist] is at [Venue]. Saturday you've got "
        "[Artist] at [Venue], and [Artist] at [Venue]. Sunday, [Artist] closes it out at "
        "[Venue]. Which one are you hitting?\""
    ),
    "stacked_month": (
        "Video: 'Did you realize how stacked {month} is for {genre} in Philly?' List the biggest names with "
        "their dates, building momentum. End by asking which one they're going to.\n"
        "Example hook: \"{genre} fans, {month} in Philly is unreal\"\n"
        "Example script: \"Did you realize how stacked {month} is for {genre} in Philly? [Artist] on [show_date]. "
        "[Artist] at [Venue]. Then [Artist] the same week. And that's not even all of them. "
        "Which one are you going to?\""
    ),
    "presale": (
        "Video: presale/on-sale alert for {artist}. Urgent but not spammy. Tickets are NOT on sale yet: talk "
        "about when they open, never say they're live now. Say when it opens (copy sale_opens exactly) and "
        "which show(s) it's for. If all_sales lists more windows, mention the general on-sale time too. "
        "Presale codes are not in the facts: tell people to check the artist's socials or Ticketmaster, and "
        "never make up a code.\n"
        "Example hook: \"Set an alarm. [Artist] tickets drop [day]\"\n"
        "Example script: \"Heads up Philly. The [sale name] for [Artist] opens [sale_opens]. That's for "
        "their [show_date] show at [Venue]. Codes usually go out through the artist's socials, so check there "
        "or on Ticketmaster. Don't sleep on this one.\""
    ),
    "new_tour": (
        "Video: 'JUST ANNOUNCED: {artist} is coming to Philly.' Give the date(s) and venue, then when "
        "tickets go on sale if that's in the facts. If already_had_philly_dates is true, say 'another date "
        "just got added' instead.\n"
        "Example hook: \"[Artist] just announced a Philly show\"\n"
        "Example script: \"Just announced: [Artist] is coming to [Venue] on [show_date]. Tickets go on sale "
        "[public_onsale]. Tag whoever you're going with.\""
    ),
    "team_week": (
        "Video: '{artist} home games this week.' The script is already written; write the hook only. "
        "Hype, quick, Philly sports fan voice. Mention the {artist}.\n"
        "Example hook: \"[Team] are home [number] times this week\""
    ),
    "big_game": (
        "Video: big home game, {artist} vs. the {opponent}. The script is already written; write the hook "
        "only. Rivalry energy, but don't invent records, standings, injuries or players.\n"
        "Example hook: \"[Opponent] in Philly this week. You know what that means\""
    ),
    "status_change": (
        "Video: quick news update. {artist}'s Philly show changed. Say plainly what changed and tell "
        "ticket holders to check their email or Ticketmaster.\n"
        "Example hook: \"Update on the [Artist] Philly show\"\n"
        "Example script: \"Quick update if you have [Artist] tickets. Their [show_date] show at [Venue] has been "
        "[change]. Check your email or Ticketmaster for what happens with your tickets.\""
    ),
}

DRAFT_SCHEMA = {
    "type": "object",
    "properties": {
        "hook": {"type": "string"},
        "script": {"type": "string"},
        "caption": {"type": "string"},
        "hashtags": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["hook", "script", "caption", "hashtags"],
}


# ---------- Ollama process management ----------

def _ollama_up() -> bool:
    try:
        return requests.get(f"{config.OLLAMA_URL}/api/version", timeout=2).ok
    except requests.RequestException:
        return False


def _ollama_exe() -> str | None:
    found = shutil.which("ollama")
    if found:
        return found
    default = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Ollama" / "ollama.exe"
    return str(default) if default.exists() else None


@contextmanager
def ollama_session():
    """Make sure Ollama is reachable for the duration; stop it after if we started it."""
    proc = None
    if not _ollama_up():
        exe = _ollama_exe()
        if not exe:
            raise RuntimeError("Ollama isn't running and ollama.exe wasn't found")
        _kill_orphan_runners()
        log.info("Starting Ollama (%s)", exe)
        flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        proc = subprocess.Popen([exe, "serve"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                creationflags=flags)
        for _ in range(60):
            if _ollama_up():
                break
            time.sleep(1)
        else:
            proc.kill()
            raise RuntimeError("Ollama didn't come up within 60s")
    try:
        yield
    finally:
        if proc:
            log.info("Stopping Ollama (we started it)")
            _stop_tree(proc)


# `ollama serve` starts a model runner child that holds the model in RAM (~2.2 GB for qwen2.5:3b). Its name
# changes between Ollama versions (ollama_llama_server.exe before, llama-server.exe in 0.32), so we kill by
# process tree, not by name. Killing only `ollama serve` left one orphaned runner per run on 2026-10-21.
_RUNNER_NAMES = ("llama-server.exe", "ollama_llama_server.exe")


def _stop_tree(proc: subprocess.Popen) -> None:
    if os.name == "nt":
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                       capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
    else:
        proc.terminate()
    try:
        proc.wait(timeout=15)
    except subprocess.TimeoutExpired:
        proc.kill()


def _kill_orphan_runners() -> None:
    """Runners left behind when a previous run was killed before it could clean up (its children survive).
    Only called when Ollama isn't serving, so no live session owns these processes."""
    if os.name != "nt":
        return
    for name in _RUNNER_NAMES:
        r = subprocess.run(["taskkill", "/F", "/IM", name], capture_output=True, text=True,
                           creationflags=subprocess.CREATE_NO_WINDOW)
        if r.returncode == 0:
            log.warning("Killed orphaned Ollama runner(s) %s left by an earlier run", name)


# ---------- prompt + generation ----------

def _prompt_facts(facts: dict) -> dict:
    """Drop fields the model doesn't need (URLs, duplicates). Shorter prompt = faster on CPU."""
    def slim(show: dict) -> dict:
        s = {k: v for k, v in show.items() if k not in ("url", "city")}
        if s.get("status") == "onsale":
            s.pop("status")
        if s.get("event_name") == s.get("artist"):
            s.pop("event_name")
        # "show_date", not "date": the model confused show dates with sale dates in presale drafts.
        for old, new in (("date", "show_date"), ("time", "show_time")):
            if old in s:
                s[new] = s.pop(old)
        return s
    out = {k: v for k, v in facts.items() if k != "format"}
    if "shows" in out:
        out["shows"] = [slim(s) for s in out["shows"]]
    if "show" in out:
        out["show"] = slim(out["show"])
    return out


def build_prompt(facts: dict) -> str:
    fmt = facts.get("format", "")
    guide = FORMAT_GUIDES.get(fmt, "Format: quick update on Philly concerts.")
    try:
        guide = guide.format(**{k: v for k, v in facts.items() if isinstance(v, (str, int))})
    except (KeyError, IndexError):
        pass  # leave placeholders; the facts are still below
    if fmt == "presale" and facts.get("sale_name") == "General public on-sale":
        guide += "\nThis one is the general on-sale: anyone can buy, no code needed. Don't mention codes."
    return (
        f"{guide}\n\nFACTS (the only things you may state):\n"
        f"{json.dumps(_prompt_facts(facts), indent=2, ensure_ascii=False)}\n\n"
        'Return JSON: {"hook": str, "script": str, "caption": str, "hashtags": [str]}'
    )


_EMOJI = re.compile("[\U0001F000-\U0001FAFF☀-➿️‍]")


def spoken(text: str) -> str:
    """Hook/script get read aloud: drop hashtags and emoji the model sneaks in anyway."""
    text = _EMOJI.sub("", re.sub(r"(?<!\w)#\w+", "", text))
    text = re.sub(r"[ \t]+([!?.,])", r"\1", text)  # "better 🔥!" -> "better !" -> "better!"
    return "\n".join(re.sub(r"[ \t]{2,}", " ", line).strip() for line in text.strip().splitlines()).strip()


def fill_placeholders(text: str, facts: dict) -> str:
    """The model sometimes echoes an example's '[sale_opens]'. If it names a real fact, fill it in;
    anything else (like '[Artist]') is left for check_facts to flag."""
    def sub(m):
        key = m.group(1).strip().lower().replace(" ", "_")  # "[Artist]" / "[sale name]" -> artist / sale_name
        value = facts.get(key)
        return value if isinstance(value, str) else m.group(0)
    return re.sub(r"\[([A-Za-z_ ]+)\]", sub, text)


def _artist_names(facts: dict) -> list[str]:
    """Artist and venue names: the proper nouns a hashtag should spell exactly."""
    shows = facts.get("shows", []) + ([facts["show"]] if facts.get("show") else [])
    names = [facts.get("artist")] + [s.get("artist") for s in shows] + [s.get("venue") for s in shows]
    return [n for n in dict.fromkeys(names) if n]


def clean_hashtags(raw: list, facts: dict) -> list[str]:
    """Strip punctuation, dedupe, force #philly, and snap misspelled artist tags to the real name
    (the model wrote #calvinnharris and #elderbroom in testing)."""
    artist_tags = {re.sub(r"\W", "", n).lower(): re.sub(r"\W", "", n) for n in _artist_names(facts)}
    tags = []
    for t in raw:
        body = re.sub(r"[^\w]", "", str(t))
        if not body:
            continue
        low = body.lower()
        if low not in artist_tags:
            # 0.82: catches #aboogiewitethehoodie (0.84) but leaves #MeekMillLive (0.80) alone
            close = difflib.get_close_matches(low, list(artist_tags), n=1, cutoff=0.82)
            if close:
                body = artist_tags[close[0]]
        tag = "#" + body
        if tag.lower() not in (x.lower() for x in tags):
            tags.append(tag)
    tags = [t for t in tags if t.lower() != "#philly"]
    return ["#philly"] + tags  # always present, always first


HOOK_ONLY_SCHEMA = {
    "type": "object",
    "properties": {k: DRAFT_SCHEMA["properties"][k] for k in ("hook", "hashtags")},
    "required": ["hook", "hashtags"],
}


def generate(facts: dict, model: str | None = None, script: str | None = None,
             caption: str | None = None) -> dict:
    """Call Ollama once and return the parsed draft dict. Raises on failure.

    With a templated `script` and `caption` (presale/status formats), the model only writes the
    hook and hashtags.
    """
    model = model or config.OLLAMA_MODEL
    prompt = build_prompt(facts)
    if script:
        prompt += ("\n\nThe voiceover script is ALREADY WRITTEN (below). Do not rewrite it. Write only a hook "
                   "(the first line on screen, leading into this script) and hashtags.\n"
                   f"SCRIPT: {script}\n"
                   'Return JSON: {"hook": str, "hashtags": [str]}')
    resp = requests.post(f"{config.OLLAMA_URL}/api/chat", timeout=600, json={
        "model": model,
        "stream": False,
        "format": HOOK_ONLY_SCHEMA if script else DRAFT_SCHEMA,
        "keep_alive": "2m",
        "options": {"temperature": 0.8, "num_ctx": 4096, "num_predict": 700},
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
    })
    resp.raise_for_status()
    out = json.loads(resp.json()["message"]["content"])
    for key in ("hook", "script", "caption"):
        out[key] = fill_placeholders(str(out.get(key, "")), facts)
    # Captions often arrive as "text #tag #tag" or only tags: move tags to the hashtag list.
    caption_tags = re.findall(r"(?<!\w)#\w+", out["caption"])
    tags = clean_hashtags(list(out.get("hashtags", [])) + caption_tags, facts)
    return {
        "hook": spoken(out["hook"]),
        "script": script or spoken(out["script"]),
        "caption": caption or re.sub(r"[ \t]*(?<!\w)#\w+", "", out["caption"]).strip(),
        "hashtags": tags[:8],
        "model": f"template + {model}" if script else model,
    }


# ---------- deterministic on-screen text ----------

def onscreen_lines(facts: dict) -> list[str]:
    """Text overlays built straight from facts, so dates/venues on screen are always right."""
    lines = []
    fmt = facts.get("format")
    if fmt == "weekend":
        lines.append(f"PHILLY THIS WEEKEND · {facts['weekend']}")
    elif fmt == "stacked_month":
        lines.append(f"{facts['month'].upper()} IN PHILLY: {facts['genre'].upper()}")
    elif fmt == "presale":
        for sale in facts.get("all_sales") or [{"name": facts["sale_name"], "opens": facts["sale_opens"]}]:
            lines.append(f"{sale['name'].upper()}: {sale['opens']}")
    elif fmt == "new_tour":
        lines.append(f"{'NEW DATE' if facts.get('already_had_philly_dates') else 'JUST ANNOUNCED'}: {facts['artist']}")
        if facts.get("public_onsale"):
            lines.append(f"On sale {facts['public_onsale']}")
    elif fmt == "status_change":
        s = facts["show"]
        lines.append(f"{facts['artist']} · {s['date']} · {facts['change'].upper()}")
        return lines
    elif fmt == "team_week":
        lines.append(f"{facts['artist'].upper()} HOME GAMES · {facts['week']}")
        for g in facts["shows"]:  # venue is nearly always the same arena, so show the tip-off time instead
            lines.append(" · ".join(b for b in (g["date"], g["artist"], g.get("time")) if b))
        return lines
    elif fmt == "big_game":
        g = facts["show"]
        lines.append(f"BIG GAME: {g['artist']}")
        lines.append(" · ".join(b for b in (g["date"], g.get("time"), g.get("venue")) if b))
        return lines
    for s in facts.get("shows", []):
        bits = [s["date"], s.get("artist") or s.get("event_name"), s.get("venue")]
        if s.get("price"):
            bits.append(s["price"])
        lines.append(" · ".join(b for b in bits if b))
    return lines


# ---------- fact check ----------

_MONTH_NAMES = ["january", "february", "march", "april", "may", "june", "july", "august",
                "september", "october", "november", "december"]
_DATE_OR_NUMBER = re.compile(
    r"\$\s?\d[\d,]*(?:\.\d+)?"                                   # $45
    r"|\b\d{1,2}(?::\d{2})?\s?(?:am|pm)\b"                         # 8pm, 8:00 PM
    r"|\b(?:%s|sept)\.?\s+\d{1,2}(?:st|nd|rd|th)?\b"                 # Nov 8, November 8th
    r"|\b\d{1,2}/\d{1,2}\b"                                         # 11/8
    r"|\b\d+\b" % "|".join(m[:3] + "(?:" + m[3:] + ")?" for m in _MONTH_NAMES),  # any number
    re.I,
)


_WEEKDAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
# "Sat, Nov 21" / "Saturday November 21st": the weekday must match the date, not just appear somewhere.
_WEEKDAY_DATE = re.compile(
    r"\b(?:%s)[a-z]*\.?,?\s+(?:%s)[a-z]*\.?\s+\d{1,2}(?:st|nd|rd|th)?\b"
    % ("|".join(_WEEKDAYS), "|".join(m[:3] for m in _MONTH_NAMES)), re.I)


def _canon(s: str) -> str:
    """Normalize so 'November 8th', 'Nov 8' and 'nov. 8' compare equal, and '8:00 PM' == '8pm'."""
    s = s.lower()
    s = re.sub(r"\b(%s)[a-z]*\.?" % "|".join(m[:3] for m in _MONTH_NAMES), r"\1", s)
    s = re.sub(r"\b(%s)[a-z]*\.?" % "|".join(_WEEKDAYS), r"\1", s)  # Saturday -> sat
    s = re.sub(r"(\d)(st|nd|rd|th)\b", r"\1", s)
    s = re.sub(r"(\d):00\s?([ap]m)", r"\1\2", s)
    s = s.replace(",", "")
    return re.sub(r"\s+", " ", s)


def check_facts(draft: dict, facts: dict) -> list[str]:
    """Return warnings for dates/times/prices/numbers in the draft that aren't in the facts.

    It's a tripwire, not a proof: it catches the common hallucinations (wrong day, made-up
    price or time, invented presale code) so you know which drafts to read closely.
    """
    derived = {"show_count": len(facts.get("shows", []))}  # "3 shows" is fine to say
    fact_text = _canon(json.dumps([facts, derived], ensure_ascii=False))
    fact_squashed = fact_text.replace(" ", "")
    text = " ".join([draft.get("hook", ""), draft.get("script", ""), draft.get("caption", "")])
    warnings = []
    for m in _WEEKDAY_DATE.finditer(text):
        if _canon(m.group(0)) not in fact_text:
            warnings.append(m.group(0))
    for m in _DATE_OR_NUMBER.finditer(text):
        token = m.group(0)
        c = _canon(token).replace(" ", "")
        if re.fullmatch(r"\d+", c):
            # bare numbers: must appear as a whole number somewhere in the facts
            if not re.search(rf"(?<![\d]){c}(?![\d])", fact_text):
                warnings.append(token)
        elif c not in fact_squashed:
            warnings.append(token)
    if re.search(r"\bcode\b[^.]*\b[A-Z0-9]{4,}\b", text):
        warnings.append("possible presale code")
    warnings += re.findall(r"\[[A-Za-z_ ]+\]", text)  # "[Artist]" copied from a prompt example
    return [f"Not in facts: {w}" for w in dict.fromkeys(warnings)]
