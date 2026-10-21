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
        "Example script: \"Did you realize how stacked {month} is for {genre} in Philly? [Artist] on [date]. "
        "[Artist] at [Venue]. Then [Artist] the same week. And that's not even all of them. "
        "Which one are you going to?\""
    ),
    "presale": (
        "Video: presale/on-sale alert for {artist}. Urgent but not spammy. Say when it opens (copy sale_opens "
        "exactly) and which show(s) it's for. Tell people to check the artist's socials or Ticketmaster for "
        "the code. Never make up a code.\n"
        "Example hook: \"Set an alarm. [Artist] tickets drop [day]\"\n"
        "Example script: \"Heads up Philly. The [sale name] for [Artist] opens [sale_opens]. That's for "
        "their [date] show at [Venue]. Codes usually go out through the artist's socials, so check there "
        "or on Ticketmaster. Don't sleep on this one.\""
    ),
    "new_tour": (
        "Video: 'JUST ANNOUNCED: {artist} is coming to Philly.' Give the date(s) and venue, then when "
        "tickets go on sale if that's in the facts. If already_had_philly_dates is true, say 'another date "
        "just got added' instead.\n"
        "Example hook: \"[Artist] just announced a Philly show\"\n"
        "Example script: \"Just announced: [Artist] is coming to [Venue] on [date]. Tickets go on sale "
        "[public_onsale]. Tag whoever you're going with.\""
    ),
    "status_change": (
        "Video: quick news update. {artist}'s Philly show changed. Say plainly what changed and tell "
        "ticket holders to check their email or Ticketmaster.\n"
        "Example hook: \"Update on the [Artist] Philly show\"\n"
        "Example script: \"Quick update if you have [Artist] tickets. Their [date] show at [Venue] has been "
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
            proc.terminate()
            try:
                proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                proc.kill()
            # `ollama serve` spawns a runner process that holds the model in RAM; make sure it goes too.
            if os.name == "nt":
                subprocess.run(["taskkill", "/F", "/IM", "ollama_llama_server.exe"],
                               capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)


# ---------- prompt + generation ----------

def _prompt_facts(facts: dict) -> dict:
    """Drop fields the model doesn't need (URLs, duplicates). Shorter prompt = faster on CPU."""
    def slim(show: dict) -> dict:
        s = {k: v for k, v in show.items() if k not in ("url", "city")}
        if s.get("status") == "onsale":
            s.pop("status")
        if s.get("event_name") == s.get("artist"):
            s.pop("event_name")
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
        value = facts.get(m.group(1))
        return value if isinstance(value, str) else m.group(0)
    return re.sub(r"\[(\w+)\]", sub, text)


def generate(facts: dict, model: str | None = None) -> dict:
    """Call Ollama once and return the parsed draft dict. Raises on failure."""
    model = model or config.OLLAMA_MODEL
    resp = requests.post(f"{config.OLLAMA_URL}/api/chat", timeout=600, json={
        "model": model,
        "stream": False,
        "format": DRAFT_SCHEMA,
        "keep_alive": "2m",
        "options": {"temperature": 0.8, "num_ctx": 4096, "num_predict": 700},
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": build_prompt(facts)},
        ],
    })
    resp.raise_for_status()
    out = json.loads(resp.json()["message"]["content"])
    for key in ("hook", "script", "caption"):
        out[key] = fill_placeholders(str(out.get(key, "")), facts)
    tags = [t.strip() for t in out.get("hashtags", []) if t.strip()]
    tags = ["#" + t.lstrip("#").replace(" ", "") for t in tags]
    if "#philly" not in [t.lower() for t in tags]:
        tags.insert(0, "#philly")
    return {
        "hook": spoken(out.get("hook", "")),
        "script": spoken(out.get("script", "")),
        "caption": out.get("caption", "").strip(),
        "hashtags": tags[:8],
        "model": model,
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
        lines.append(f"{facts['sale_name'].upper()}: {facts['sale_opens']}")
    elif fmt == "new_tour":
        lines.append(f"{'NEW DATE' if facts.get('already_had_philly_dates') else 'JUST ANNOUNCED'}: {facts['artist']}")
        if facts.get("public_onsale"):
            lines.append(f"On sale {facts['public_onsale']}")
    elif fmt == "status_change":
        s = facts["show"]
        lines.append(f"{facts['artist']} · {s['date']} · {facts['change'].upper()}")
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


def _canon(s: str) -> str:
    """Normalize so 'November 8th', 'Nov 8' and 'nov. 8' compare equal, and '8:00 PM' == '8pm'."""
    s = s.lower()
    s = re.sub(r"\b(%s)[a-z]*\.?" % "|".join(m[:3] for m in _MONTH_NAMES), r"\1", s)
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
