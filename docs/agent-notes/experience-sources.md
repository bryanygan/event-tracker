# P2-14 source survey: Philly experiences beyond concerts (2026-10-24)

Goal (creator-roadmap P2-14): food events, markets, pop-ups, seasonal festivals, free/cheap things to do, other
sports. Rules: only sources that allow automated reading, provenance on every event, a source outage never erases
events, and "free" / "under $40" only when the source's own data says so.

Method: `robots.txt` for our user agent, one page per site (3 s apart), schema.org Event JSON-LD, The Events
Calendar's public API (`/wp-json/tribe/events/v1/events`), iCal/RSS links, then the terms of use for the useful ones.
36 sites checked.

## Usable

| Source | How | What it adds (next ~3 months) | Notes |
|---|---|---|---|
| Ticketmaster (already used) | Discovery API, `classificationName` Sports / Arts & Theatre | 82 games in 60 days (Sixers, Flyers, Eagles, Union, college); 357 comedy/theatre listings (58 comedy) | Same key, same terms; ~2 extra calls per run. Miscellaneous segment is mostly arena add-ons ("MUST HAVE TICKET TO GAME", suites): skip it. Family segment is empty. |
| Center City District | Events Calendar API (public, plugin default) | 160 listings but only 14 distinct events: Made in Philadelphia Holiday Market, Open Streets, Dilworth Park tastings, College Nights, Lunar New Year, Free in February | Recurring dates must collapse into one event with a date range. `cost` is always blank. No terms page found; robots allows. |
| Delaware River Waterfront | Event list page plus a per-event iCal file (`/apos-events/vcal?slug=`) | 4 right now, all big: RiverRink Winterfest opening, tree lighting, NYE fireworks, Fire & Ice party | Few events but high-value; iCal gives exact start/end and address. |
| South Street Headhouse District | JSON-LD on `/events/` | 19 in a week: Halloween on South Street, Record Riot, gallery nights, trivia | Mostly small business listings. `offers.price` is 0 on nearly everything, including paid workshops, so it can't support a "free" claim. |
| Bartram's Garden | Events Calendar API | 26: free bike rentals, farm stands, film nights, yoga | Community-scale; "Free Community Events" category is the source's own label. Low fit for this audience. |

## Not usable (and why)

- **Visit Philly**: terms allow automated reading only as robots.txt permits (it does), for personal/editorial use,
  max 250 words of text. But its event pages are evergreen articles with no dates in structured data, and its site
  API is deliberately locked (Kadence Security returns 401; the one open route is an internal POST for their own
  filter). Using that route would be circumventing an anti-scraping measure, which the terms forbid. Useful to read
  by hand for ideas.
- **Eventbrite**: terms ban scraping and automated extraction from the site. The API only lists events for
  organizers you name (no public search since 2020). Many food fests (Street Food Festival, night markets) sell
  there, so an organizer list via the API is a possible later add with a free token.
- **allevents.in**: an aggregator that itself scrapes other sites; no provenance.
- **No machine-readable listings**: Do215 (redirects, no JSON-LD), Fairmount Park Conservancy, Schuylkill Banks,
  Historic Philadelphia, Manayunk, East Passyunk, Italian Market, Punk Rock Flea, Clover Market, Philadelphia Museum
  of Art, Franklin Institute, Barnes, PAFA, Eastern State, Zoo, Penn Museum (an RSS of latest items, no dates).
- **Empty API**: Chinatown PCDC and University City District run The Events Calendar but list 0 events.
- **Unreachable**: phila.gov event URLs 404; Free Library 403; Reading Terminal Market, Fishtown District, Philly Fun
  Guide timed out; Mütter SSL error; phillyfleamarkets.com down.

## Takeaway

Structured data covers sports and comedy well (Ticketmaster) and a handful of big seasonal events (CCD, waterfront).
Food festivals and markets mostly live on Eventbrite, Instagram and editorial roundups, none of which we can read
automatically. A hand-entry path ("add an event from a link I paste", facts typed by the user) would cover them
without breaking any terms.
