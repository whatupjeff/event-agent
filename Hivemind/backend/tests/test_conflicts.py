import asyncio

from app.agents import Conflicts, Event, travel_minutes


def ev(id, start, end, lat, lon, city="San Francisco", status="going", tags=()):
    return Event(id=id, title=id, start=f"2026-11-19T{start}:00-08:00", end=f"2026-11-19T{end}:00-08:00",
                 lat=lat, lon=lon, city=city, venue=id, status=status, tags=list(tags), platforms=["luma"])


SF, PA = (37.7596, -122.4269), (37.402, -122.0521)  # Dolores Park, Hacker Dojo


def check(*evs):
    return asyncio.run(Conflicts(None).check(events=[e.model_dump(mode="json") for e in evs], profile={"topics": {"ai": 2}}))


def test_travel_estimate():
    assert travel_minutes(ev("a", "10:00", "11:00", *SF), ev("b", "12:00", "13:00", *SF)) == 0  # same spot
    assert 90 <= travel_minutes(ev("a", "10:00", "11:00", *SF), ev("b", "12:00", "13:00", *PA)) <= 150  # SF -> South Bay
    assert travel_minutes(ev("a", "10:00", "11:00", None, None), ev("b", "12:00", "13:00", *PA)) is None


def test_tight_gap_to_another_city_is_a_clash():
    (c,) = check(ev("coffee", "16:30", "18:00", *SF), ev("tinkerers", "18:30", "21:00", *PA, city="Palo Alto", tags=["ai"]))
    assert c["kind"] == "travel" and c["gap_min"] == 30 and c["travel_min"] > 30
    assert c["keep_id"] == "tinkerers"  # both confirmed, so taste decides


def test_no_false_alarms():
    assert check(ev("a", "16:30", "18:00", *SF), ev("b", "18:00", "20:00", *SF)) == []  # back to back, same place
    assert check(ev("a", "10:00", "11:00", *SF), ev("b", "15:00", "16:00", *PA)) == []  # 4h is plenty
    assert check(ev("a", "10:00", "11:00", None, None), ev("b", "11:30", "12:00", *PA)) == []  # unknown venue: no guess
