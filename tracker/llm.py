"""Turn an alert's facts into a TikTok draft with Claude (when ANTHROPIC_API_KEY is set) or a local Ollama model.

Both get the same prompt and the same checks; Ollama is the fallback when Claude is over its daily cap or the
API call fails. Claude calls are logged in `llm_calls` (tokens, cost) for the Runs tab.

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
from contextlib import ExitStack, contextmanager, nullcontext
from datetime import datetime, timezone
from pathlib import Path

import requests

from . import config
from .store import now_iso

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
        "just got added' instead. If history.is_comeback is true, lead with it: 'first Philly show since "
        "<history.last_philly_show_year>' (use only that year; never say 'first time ever'). If "
        "history.last_philly_show_was_multi_act is true, that was a festival/radio show, not their own "
        "concert: don't call it a headline show. If the show's kind is \"comedy\", it's stand-up: say "
        "show, never concert or tour.\n"
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
    "experience": (
        "Video: a Philly {category} happening: {artist}. The script is already written; write the hook only. "
        "The facts don't say what's there, so don't invent vendors, food, rides, performers or crowds, and don't "
        "call it back, returning, annual or the biggest. Only say it's free if the price is \"Free\".\n"
        "Example hook: \"Your Philly weekend plans just got easier\""
    ),
    "things_to_do": (
        "Video: 'Things to do in Philly this weekend' ({weekend}). Not just concerts: markets, festivals, "
        "comedy, a game. One quick line each: what it is, the day, where. An entry with \"dates\" runs several "
        "days: say it's on through its end date, don't pin it to one day. kind \"comedy\" is stand-up, not a "
        "concert. Don't describe what's at a market or festival beyond its name, and only call something free if "
        "its price is \"Free\".\n"
        "Example hook: \"No excuse to stay home in Philly this weekend\"\n"
        "Example script: \"Here's what's on in Philly this weekend. [Market] is open at [Venue] all weekend. "
        "Saturday, [Comedian] is at [Venue]. Sunday, the [Team] are home. Which one are you doing?\""
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

class LowMemory(RuntimeError):
    """Not enough free RAM to load the local model without squeezing Homebridge and the rest of the PC."""


def ollama_up() -> bool:
    try:
        return requests.get(f"{config.OLLAMA_URL}/api/version", timeout=2).ok
    except requests.RequestException:
        return False


def free_memory_mb() -> int | None:
    """Available physical RAM in MB, or None if it can't be read (then the guard doesn't block)."""
    if os.name != "nt":
        return None
    import ctypes

    class MemoryStatus(ctypes.Structure):
        _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]

    status = MemoryStatus()
    status.dwLength = ctypes.sizeof(MemoryStatus)
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
        return None
    return int(status.ullAvailPhys // (1024 * 1024))


def _model_loaded() -> bool:
    """Is the local model already in RAM (someone else's Ollama session)? Then using it costs nothing more."""
    try:
        resp = requests.get(f"{config.OLLAMA_URL}/api/ps", timeout=2)
        return resp.ok and any(m.get("name", "").startswith(config.OLLAMA_MODEL)
                               for m in resp.json().get("models", []))
    except (requests.RequestException, ValueError):
        return False


def check_memory(up: bool | None = None) -> None:
    """Raise LowMemory if loading the local model now would leave too little RAM for everything else."""
    up = ollama_up() if up is None else up
    if up and _model_loaded():
        return
    free = free_memory_mb()
    if free is not None and free < config.OLLAMA_MIN_FREE_MB:
        raise LowMemory(f"Only {free / 1000:.1f} GB of memory free; the local model needs "
                        f"{config.OLLAMA_MIN_FREE_MB / 1000:.1f} GB, so it wasn't started")


def _ollama_exe() -> str | None:
    found = shutil.which("ollama")
    if found:
        return found
    default = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Ollama" / "ollama.exe"
    return str(default) if default.exists() else None


@contextmanager
def ollama_session():
    """Make sure Ollama is reachable for the duration; stop it after if we started it. Raises LowMemory
    (before starting anything) when the PC is short on RAM."""
    proc = None
    up = ollama_up()
    check_memory(up)
    if not up:
        exe = _ollama_exe()
        if not exe:
            raise RuntimeError("Ollama isn't running and ollama.exe wasn't found")
        _kill_orphan_runners()
        log.info("Starting Ollama (%s)", exe)
        flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        proc = subprocess.Popen([exe, "serve"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                creationflags=flags)
        for _ in range(60):
            if ollama_up():
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


def uses_claude() -> bool:
    return config.DRAFT_PROVIDER == "claude" and bool(config.ANTHROPIC_API_KEY)


# While a Claude model_session() is open: once a call fails, the rest of the batch goes straight to Ollama,
# which is started once and kept up until the session ends (not started and stopped per draft). One batch runs
# at a time per process (the dashboard's job lock, the pipeline's run lock), so module state is enough.
_batch: dict | None = None


@contextmanager
def model_session():
    """What a batch of drafts needs running: nothing for Claude (Ollama is started only if a call falls back),
    Ollama otherwise. If the PC is short on memory, the batch still runs: each call that needs Ollama raises
    LowMemory (templated formats then draft from their template), without retrying the start per draft."""
    global _batch
    with ExitStack() as stack:
        _batch = {"stack": stack, "claude_failed": False, "ollama": False, "low_memory": None}
        try:
            if not uses_claude():
                try:
                    stack.enter_context(ollama_session())
                    _batch["ollama"] = True
                except LowMemory as exc:
                    log.warning("%s", exc)
                    _batch["low_memory"] = exc
            yield
        finally:
            _batch = None


def _fallback_ollama():
    """Ollama for a Claude fallback: kept up for the rest of the batch, or just for this call outside one."""
    if _batch is None:
        return ollama_session()
    if _batch["low_memory"]:
        raise _batch["low_memory"]
    if not _batch["ollama"]:
        try:
            _batch["stack"].enter_context(ollama_session())
        except LowMemory as exc:
            log.warning("%s", exc)
            _batch["low_memory"] = exc
            raise
        _batch["ollama"] = True
    return nullcontext()


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
    # your_plays is personal and for the dashboard only; the script shouldn't talk about your listening
    # picks duplicate shows (a "picks" draft gets them as its shows instead: angles.scoped_facts)
    out = {k: v for k, v in facts.items() if k not in ("format", "your_plays", "picks", "pick_count")}
    if "shows" in out:
        out["shows"] = [slim(s) for s in out["shows"]]
    if "show" in out:
        out["show"] = slim(out["show"])
    return out


def _music(facts: dict):
    """tracker/music_formats.py for music ideas (roadmap M6), else None. Imported late: it imports the detectors."""
    from . import config as _c
    if facts.get("format") in _c.MUSIC_KINDS:
        from . import music_formats
        return music_formats
    return None


def build_prompt(facts: dict) -> str:
    mf = _music(facts)
    if mf:
        return (f"{mf.guide(facts)}\n\nFACTS (the only things you may state):\n"
                f"{json.dumps(mf.prompt_facts(facts), indent=2, ensure_ascii=False)}\n\n"
                'Return JSON: {"hook": str, "script": str, "closer": str, "caption": str, "hashtags": [str]}')
    fmt = facts.get("format", "")
    guide = FORMAT_GUIDES.get(fmt, "Format: quick update on Philly concerts.")
    try:
        guide = guide.format(**{k: v for k, v in facts.items() if isinstance(v, (str, int))})
    except (KeyError, IndexError):
        pass  # leave placeholders; the facts are still below
    if facts.get("picked_from"):
        guide += (f"\nThis is a 'my picks' video: only the {len(facts.get('shows', []))} shows below, best first "
                  f"(picked from {facts['picked_from']}). Mention no other shows.")
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
    items = facts.get("releases") or facts.get("albums") or facts.get("artists_to_watch") or []  # music roundups
    names = ([facts.get("artist")] + [s.get("artist") for s in shows] + [s.get("venue") for s in shows]
             + list(facts.get("other_artists") or []) + [i.get("artist") for i in items])
    return [n for n in dict.fromkeys(names) if n]


# The page's usual tags, so a typo like #philllyevents (Haiku, 2026-10-22) is fixed like a misspelled artist
HOUSE_TAGS = ["phillyevents", "phillyconcerts", "phillymusic", "philadelphia", "phillyweekend", "thingstodoinphilly"]


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
            elif low not in HOUSE_TAGS:
                # stricter for these short ones: 0.82 turned #phillypop into #phillyhiphop
                close = difflib.get_close_matches(low, HOUSE_TAGS, n=1, cutoff=0.92)
                if close:
                    body = close[0]
        tag = "#" + body
        if tag.lower() not in (x.lower() for x in tags):
            tags.append(tag)
    if _music(facts):  # national music news: #philly only if the model chose it
        return tags
    tags = [t for t in tags if t.lower() != "#philly"]
    return ["#philly"] + tags  # always present, always first


HOOK_ONLY_SCHEMA = {
    "type": "object",
    "properties": {k: DRAFT_SCHEMA["properties"][k] for k in ("hook", "hashtags")},
    "required": ["hook", "hashtags"],
}


def _ollama_chat(model: str, prompt: str, schema: dict, system: str = SYSTEM_PROMPT) -> dict:
    resp = requests.post(f"{config.OLLAMA_URL}/api/chat", timeout=600, json={
        "model": model,
        "stream": False,
        "format": schema,
        "keep_alive": "2m",
        "options": {"temperature": 0.8, "num_ctx": 4096, "num_predict": 700},
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ],
    })
    resp.raise_for_status()
    return json.loads(resp.json()["message"]["content"])


def claude_calls_today(conn, now: datetime | None = None) -> int:
    from .context import _et_day_utc
    return conn.execute("SELECT COUNT(*) FROM llm_calls WHERE at >= ? AND at < ?",
                        _et_day_utc(now or datetime.now(timezone.utc))).fetchone()[0]


def claude_usage(conn, now: datetime | None = None) -> dict:
    """Calls today against the cap, and this month's cost, for the Runs tab."""
    now = now or datetime.now(timezone.utc)
    month = now.strftime("%Y-%m")
    row = conn.execute("SELECT COUNT(*) AS n, COALESCE(SUM(cost_usd), 0) AS cost FROM llm_calls WHERE at LIKE ?",
                       (month + "%",)).fetchone()
    return {"enabled": uses_claude(), "model": config.CLAUDE_MODEL, "today": claude_calls_today(conn, now),
            "cap": config.CLAUDE_DAILY_CAP, "month_calls": row["n"], "month_cost_usd": round(row["cost"], 4)}


def _claude_chat(conn, model: str, prompt: str, schema: dict, system: str = SYSTEM_PROMPT) -> dict:
    """One Claude call with the reply constrained to the draft schema. Logged (tokens, cost) even on failure."""
    import anthropic
    client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY, timeout=60.0)
    usage, error = None, None
    try:
        resp = client.messages.create(
            model=model,
            max_tokens=2000,
            system=system,
            output_config={"effort": config.CLAUDE_EFFORT, "format": {"type": "json_schema",
                                                        "schema": {**schema, "additionalProperties": False}}},
            messages=[{"role": "user", "content": prompt}],
        )
        usage = resp.usage
        if resp.stop_reason != "end_turn":
            raise RuntimeError(f"Claude stopped early ({resp.stop_reason})")
        return json.loads(next(b.text for b in resp.content if b.type == "text"))
    except Exception as exc:
        error = str(exc)[:500]
        raise
    finally:
        if conn is not None:
            price_in, price_out = config.CLAUDE_PRICES.get(model, (0.0, 0.0))
            tin, tout = (usage.input_tokens, usage.output_tokens) if usage else (None, None)
            cost = (tin * price_in + tout * price_out) / 1e6 if usage else None
            conn.execute("INSERT INTO llm_calls (at, model, input_tokens, output_tokens, cost_usd, error) "
                         "VALUES (?,?,?,?,?,?)", (now_iso(), model, tin, tout, cost, error))
            conn.commit()


def _chat(conn, model: str | None, prompt: str, schema: dict, system: str = SYSTEM_PROMPT) -> tuple[dict, str]:
    """The model's JSON reply and which model wrote it. Claude when configured and under today's cap (the cap
    needs `conn`); Ollama otherwise, or when the Claude call fails. An explicit `model` picks Ollama."""
    if model is None and uses_claude() and conn is not None:
        if _batch and _batch["claude_failed"]:
            pass  # already failed this batch; don't wait out another timeout per draft
        elif claude_calls_today(conn) < config.CLAUDE_DAILY_CAP:
            try:
                return _claude_chat(conn, config.CLAUDE_MODEL, prompt, schema, system), config.CLAUDE_MODEL
            except Exception as exc:
                log.warning("Claude draft failed, falling back to Ollama: %s", exc)
                if _batch:
                    _batch["claude_failed"] = True
        else:
            log.warning("Claude daily cap (%d calls) reached; drafting with Ollama", config.CLAUDE_DAILY_CAP)
        with _fallback_ollama():  # model_session() didn't start it on the Claude path
            return _ollama_chat(config.OLLAMA_MODEL, prompt, schema, system), config.OLLAMA_MODEL
    if _batch and _batch["low_memory"]:
        raise _batch["low_memory"]
    model = model or config.OLLAMA_MODEL
    return _ollama_chat(model, prompt, schema, system), model


def generate(facts: dict, model: str | None = None, script: str | None = None,
             caption: str | None = None, angle: dict | None = None, conn=None) -> dict:
    """Call the model once and return the parsed draft dict. Raises on failure.

    With a templated `script` and `caption` (presale/status formats), the model only writes the
    hook and hashtags. `conn` lets Claude be used (cap + cost log); without it, or with an explicit Ollama
    `model`, it's Ollama.
    """
    prompt = build_prompt(facts)
    if angle:  # the creator picked a framing; the hook is fixed and the model writes around it
        prompt += (f"\n\nANGLE: {angle['label']}. {angle['pitch']}\nThe hook is already chosen: \"{angle['hook']}\". "
                   f"Start the script right after it (don't repeat it). End the caption with: {angle['cta']}")
    if script:
        prompt += ("\n\nThe voiceover script is ALREADY WRITTEN (below). Do not rewrite it. Write only a hook "
                   "(the first line on screen, leading into this script) and hashtags.\n"
                   f"SCRIPT: {script}\n"
                   'Return JSON: {"hook": str, "hashtags": [str]}')
    mf = _music(facts)
    schema = HOOK_ONLY_SCHEMA if script else mf.STORY_SCHEMA if mf else DRAFT_SCHEMA
    out, model = _chat(conn, model, prompt, schema, mf.SYSTEM_PROMPT if mf else SYSTEM_PROMPT)
    for key in ("hook", "script", "caption"):
        out[key] = fill_placeholders(str(out.get(key, "")), facts)
    if mf and not script:  # music: the model's news, then your take, then its closing question
        out["script"] = mf.assemble(spoken(out["script"]), spoken(str(out.get("closer", ""))), facts)
    # Captions often arrive as "text #tag #tag" or only tags: move tags to the hashtag list.
    caption_tags = re.findall(r"(?<!\w)#\w+", out["caption"])
    tags = clean_hashtags(list(out.get("hashtags", [])) + caption_tags, facts)
    return {
        "hook": angle["hook"] if angle else spoken(out["hook"]),
        "script": script or spoken(out["script"]),
        "caption": caption or re.sub(r"[ \t]*(?<!\w)#\w+", "", out["caption"]).strip(),
        "hashtags": tags[:8],
        "model": f"template + {model}" if script else model,
    }


# ---------- deterministic on-screen text ----------

def onscreen_lines(facts: dict) -> list[str]:
    """Text overlays built straight from facts, so dates/venues on screen are always right."""
    if _music(facts):
        return _music(facts).onscreen(facts)
    lines = []
    fmt = facts.get("format")
    if facts.get("picked_from"):  # a "my N picks" draft (angles.scoped_facts)
        lines.append(f"MY {len(facts.get('shows', []))} PICKS")
    if fmt == "weekend":
        lines.append(f"{facts['lane'].upper() + ' · ' if facts.get('lane') else ''}PHILLY THIS WEEKEND · {facts['weekend']}")
    elif fmt == "stacked_month":
        lines.append(f"{facts['month'].upper()} IN PHILLY: {facts['genre'].upper()}")
    elif fmt == "presale":
        if (facts.get("history") or {}).get("is_comeback"):
            lines.append(f"FIRST PHILLY SHOW SINCE {facts['history']['last_philly_show_year']}")
        for sale in facts.get("all_sales") or [{"name": facts["sale_name"], "opens": facts["sale_opens"]}]:
            lines.append(f"{sale['name'].upper()}: {sale['opens']}")
    elif fmt == "new_tour":
        lines.append(f"{'NEW DATE' if facts.get('already_had_philly_dates') else 'JUST ANNOUNCED'}: {facts['artist']}")
        if (facts.get("history") or {}).get("is_comeback"):
            lines.append(f"FIRST PHILLY SHOW SINCE {facts['history']['last_philly_show_year']}")
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
    # "free" / "cheap" are prices too (P2-14): only when a listing or you said so ("feel free to..." is fine)
    for m in re.finditer(r"(?<!feel )\bfree\b(?! to\b)", text, re.I):
        near = [" ".join(text[:m.start()].split()[-1:] + ["free"]), " ".join(["free"] + text[m.end():].split()[:1])]
        # a price of "Free" in the facts, or a name with the word in it ("Free Nationals")
        if '"free"' not in fact_text and not any(len(p.split()) == 2 and _canon(p.strip(".,!?")) in fact_text
                                                 for p in near):
            warnings.append(m.group(0))
            break
    for m in re.finditer(r"\b(cheap|budget|affordable)\b|\bunder \$\d+", text, re.I):
        if _canon(m.group(0)) not in fact_text:  # "Cheap Trick" in the facts is a band, not a claim
            warnings.append(m.group(0))
    warnings += re.findall(r"\[[A-Za-z_ ]+\]", text)  # "[Artist]" copied from a prompt example
    out = [f"Not in facts: {w}" for w in dict.fromkeys(warnings)]
    if _music(facts):  # status words, quoted titles, "trending" (music_formats.check)
        out += [w for w in _music(facts).check(draft, facts) if w not in out]
    return out
