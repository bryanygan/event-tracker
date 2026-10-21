"""Builds raw Ticketmaster-shaped event dicts for tests (same shape as the Discovery API)."""
import itertools

_ids = itertools.count(1)


def tm_event(name="Artist", date="2026-11-08", time="20:00:00", venue="The Fillmore Philadelphia",
             venue_id=None, artist=None, artist_id=None, genre="Rock", status="onsale",
             price=(45.0, 85.0), onsale="2026-09-01T14:00:00Z", presales=(), upcoming=40,
             event_id=None, support=()):
    artist = artist or name
    attractions = [{
        "id": artist_id or f"K8-{artist.replace(' ', '')}",
        "name": artist,
        "classifications": [{"genre": {"name": genre}, "subGenre": {"name": "Alternative"}}],
        "upcomingEvents": {"_total": upcoming},
    }] + [{"id": f"K8-{s.replace(' ', '')}", "name": s} for s in support]
    ev = {
        "id": event_id or f"G5v{next(_ids)}",
        "name": name,
        "url": "https://www.ticketmaster.com/event/x",
        "images": [{"ratio": "16_9", "url": "https://img/x.jpg", "width": 1024}],
        "dates": {"start": {"localDate": date, "localTime": time}, "status": {"code": status}},
        "classifications": [{"segment": {"name": "Music"}, "genre": {"name": genre},
                             "subGenre": {"name": "Alternative"}}],
        "sales": {
            "public": {"startDateTime": onsale, "endDateTime": "2026-12-31T00:00:00Z"},
            "presales": [{"name": n, "startDateTime": s, "endDateTime": e} for n, s, e in presales],
        },
        "_embedded": {
            "venues": [{"id": venue_id or f"KovZ-{venue.replace(' ', '')}", "name": venue,
                        "city": {"name": "Philadelphia"}, "state": {"stateCode": "PA"}}],
            "attractions": attractions,
        },
    }
    if price:
        ev["priceRanges"] = [{"type": "standard", "currency": "USD", "min": price[0], "max": price[1]}]
    return ev
