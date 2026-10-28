from tracker import search

EV = {"name": "Nova Vale: The Glass Hours Tour", "headliner": "Nova Vale", "support": "Kilo Moon, Echo Bay",
      "venue_name": "The Fillmore Philadelphia", "genre": "R&B", "subgenre": None}


def test_forgiving_matches():
    assert search.matches(EV, "nova")
    assert search.matches(EV, "NOVA   fillmore")      # every word, any field, any order
    assert search.matches(EV, "kilo moon")           # support acts count
    assert search.matches(EV, "novavale")            # spacing doesn't matter
    assert search.matches(EV, "r&b") and search.matches(EV, "r and b")
    assert search.matches({"headliner": "Dr. Static"}, "dr static") and search.matches({"headliner": "Dr. Static"}, "drstatic")
    assert search.matches({"headliner": "Renée Lark"}, "renee")
    assert search.matches({"headliner": "Soft Arcade & Static"}, "arcade and static")
    assert search.matches({"headliner": "Philadelphia 76ers", "support": "Boston Celtics"}, "sixers celtics")


def test_misses():
    assert not search.matches(EV, "nova union")      # one word misses
    assert not search.matches(EV, "valekilo")        # no matching across two fields
    assert not search.matches({"headliner": None}, "x")
