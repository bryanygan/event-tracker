# Agent notes

Handoff notes for AI agents (and humans) picking up this project. Read these before changing code.
The project brief is [../../event-tracker-pipeline.md](../../event-tracker-pipeline.md); the user-facing
README is [../../README.md](../../README.md).

| File | Read it for |
|---|---|
| [status.md](status.md) | What exists today, what's running on the machine, what's next. **Start here.** |
| [decisions.md](decisions.md) | Why things are the way they are. Check before "fixing" something that looks odd. |
| [gotchas.md](gotchas.md) | Mistakes already made and environment traps. Saves you from repeating them. |
| [environment.md](environment.md) | The machine, what else runs on it, external APIs and what they actually return. |
| [worklog.md](worklog.md) | Chronological log of sessions: what was done, in order. |
| [venue-coverage.md](venue-coverage.md) | Which Philly venues Ticketmaster misses, what they sell through, how to read them. |

## Keeping these useful

- Update `status.md` and append to `worklog.md` at the end of a work session.
- When you make a non-obvious choice, add it to `decisions.md` with the reason and what you rejected.
- When something bites you (a tool quirk, a wrong assumption), add it to `gotchas.md`.
- Write facts with dates ("checked 2026-10-21"); APIs and the machine change.
- Never put API keys or `.env` values in these files.
