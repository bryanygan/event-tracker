# events-tracker

Philly events → TikTok draft pipeline. Read `docs/agent-notes/status.md` first, then `gotchas.md`
before editing. Update `status.md` and `worklog.md` at the end of a session.

Firm rules (from the user's brief):
- Don't disrupt Homebridge (port 8581) or anything else running on this PC.
- Keys live in `.env`; never print or commit their values.
- Facts (dates, venues, prices) come from data, never from the LLM. Nothing posts automatically.
- Git: features go on branches; `main` holds the reviewed baseline. Commit and push once verified.

Run tests with `.venv\Scripts\python -m pytest -q`. Use the Edit/Write tools, not shell heredocs, for code with
regex backslashes (see gotchas.md).
