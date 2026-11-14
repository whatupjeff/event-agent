from fastapi.testclient import TestClient

from app.agents import PLATFORMS
from app.fixtures import fixture
from app.main import app, trace

def all_events():
    return {f"{p}:{e['id']}": e for p in PLATFORMS for e in fixture(p)}


def test_fixtures_are_consistent():
    evs = all_events()
    assert len(evs) == sum(len(fixture(p)) for p in PLATFORMS)  # ids unique
    assert all(e["end"] > e["start"] for e in evs.values())


def test_demo_flow_offline():
    with TestClient(app) as c:
        assert c.get("/api/health").json()["transport"] == "in-process"

        prof = c.get("/api/profile").json()
        assert prof["topics"]["agents"] > 0 > prof["topics"]["crypto"]

        ev = c.get("/api/events").json()
        assert len(ev["events"]) == 6
        assert len(ev["clashes"]) == 4  # 3 overlaps on Saturday + 1 travel squeeze on Thursday
        sat = [x for x in ev["clashes"] if x["kind"] == "overlap"]
        assert len(sat) == 3  # Saturday 10 AM triple clash
        assert {x["keep_id"] for x in sat} == {"luma:l-2", "meetup:m-2"}  # never the waitlist one
        (thu,) = [x for x in ev["clashes"] if x["kind"] == "travel"]
        assert {thu["a_id"], thu["b_id"]} == {"meetup:m-1", "luma:l-1"} and thu["keep_id"] == "luma:l-1"

        res = c.post("/api/ask", json={"prompt": "agent hackathons next week"}).json()["results"]
        titles = [r["event"]["title"] for r in res]
        assert sum("agent builders" in t.lower() for t in titles) == 1  # shown once, not per platform

        r = c.post("/api/actions", json={"event_id": "meetup:m-6", "kind": "rsvp"}).json()
        aid, tok = r["action"]["id"], r["token"]
        assert r["action"]["status"] == "pending"
        assert c.post(f"/api/actions/{aid}/approve", json={"token": "wrong"}).status_code == 403
        assert c.post(f"/api/actions/{aid}/approve", json={"token": tok}).json()["status"] == "submitted"
        assert c.post(f"/api/actions/{aid}/approve", json={"token": tok}).status_code == 403  # one-time

        kinds = {t.kind for t in trace.history}
        assert {"merge", "get", "approval.wait", "approval.ok", "submit.reply"} <= kinds
