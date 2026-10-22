# Venue coverage beyond Ticketmaster (surveyed 2026-10-21)

**Implemented (2026-10-21):** `tracker/fetchers/venue_calendars.py` reads the JSON-LD venues listed in
`config.VENUE_CALENDARS`: Underground Arts, MilkBoy, Ardmore Music Hall, Warehouse on Watts (wowphilly.com,
Tixr), The Ave Live (theavelive.com, DICE). First live run: 215 shows read, 38 already on TM, 177 added.
Etix venues added the same day with HTML parsers (`parser` key in config): Johnny Brenda's and Kung Fu
Necktie share the "rhp" WordPress events plugin (`parse_rhp`; dates have no year, the weekday pins it);
PhilaMOCA has its own markup (`parse_philamoca`; lots of film/wrestling nights, filtered by title and
label). +71 shows. All 8 venues: ~290 calendar shows on top of Ticketmaster.
User decisions: World Cafe Live is closing (skip); Tower Theater has nothing booked (skip); NOTO is
notophl.com (no events on the homepage; TM has 8 of its shows).

Step 1 of "other ticket sellers": AXS, DICE and Tixr have no public APIs, so the plan is to read venues'
own calendar pages. This is the measured gap. One request per site, robots.txt checked first (all
reachable sites allowed our UA). Survey script lived in the session scratchpad; re-create from this table.

TM music events in the DB at the time: 492 across 41 venues.

## Big gaps (worth a reader)

| Venue | Sells via | Upcoming on site | On TM | Missing from TM | Machine-readable? |
|---|---|---|---|---|---|
| Underground Arts | Tixr | 61 | 9 | **56** | yes, schema.org Event JSON-LD |
| MilkBoy Philadelphia | Tixr | 56 | 9 | **48** | yes, JSON-LD |
| Ardmore Music Hall (ardmoremusichall.com) | Tixr | 52 | 21 | **41** | yes, JSON-LD (`@graph`) |
| PhilaMOCA | Etix | ~36 | 0 | **~36** | no: HTML parse of etix links |
| Johnny Brenda's | Etix (+1 TicketLeap) | ~27 | 0 | **~27** | no: HTML parse |
| Kung Fu Necktie | Etix | ~24 | 0 | **~24** | no: HTML parse |

≈ 230 shows missing, i.e. +47% on top of Ticketmaster's music count, mostly small/mid rooms (lower notability,
but they matter for weekend roundups and cheap-show formats). "Missing" was matched by venue + date only, so
it's approximate; real dedupe needs artist-name matching too.

## Data quirks seen in the JSON-LD

- Status lives in the event name: `** SOLD OUT **`, `* Canceled *`, `**CANCELED**`. Strip it and turn it into a
  status (sold-out is useful content: "sold out, resale watch").
- Non-shows mixed in: `CLOSED FOR PRIVATE EVENT`, kids' parties, brunches (`Pancakes & Booze`).
- HTML entities in names (`&amp;`). Some JSON-LD uses minified keys (`i`, `l`, `s`, `t`): parse defensively.

## Covered already or not worth it now

- Brooklyn Bowl, The Fillmore/Foundry: Ticketmaster (site 33-34 vs TM 42-50). Covered.
- Union Transfer: calendar page has no ticket links in the HTML (rendered by JS); TM lists 34. Probably covered.
- Silk City, South Jazz Kitchen: Resy (dinner reservations), not ticketed shows.
- 118 North: domain parked (venue closed).

## Couldn't check (follow up)

- World Cafe Live: SSL error from Python (TM shows only 5 events, so likely a real gap; check by hand).
- Ortlieb's: one DICE link; the calendar is probably a DICE JavaScript widget.
- Chris' Jazz Cafe: 120 JSON-LD events but none parsed as upcoming (date format?).
- Sellersville Theater, Mann Center, Arden Concert Gild, Cherry Street Pier: no ticket links on the page
  surveyed; the calendar is probably on another URL.
- Clef Club: HTTP 409 (bot protection). Warehouse on Watts, NOTO, Tower Theater, Dell Music Center:
  connection errors (my guessed domains are probably wrong).
