from fastapi.testclient import TestClient

from app import main
from app.main import app


def prepare(c, event_id, kind="rsvp"):
    return c.post("/api/actions", json={"event_id": event_id, "kind": kind})


def approve(c, r):
    return c.post(f"/api/actions/{r['action']['id']}/approve", json={"token": r["token"]})


def my_titles(c):
    return {e["title"] for e in c.get("/api/events").json()["events"]}


def test_rsvp_fills_form_waits_then_shows_in_my_events():
    with TestClient(app) as c:
        r = prepare(c, "meetup:m-6").json()  # Agent Builders, picked from its Meetup listing
        a = r["action"]
        assert a["status"] == "pending" and a["platform"] == "meetup"
        assert a["fields"] == {"Member": "Demo User", "Response": "Attending", "Guests": "0"}
        assert "Agent Builders Hackathon 2026" not in my_titles(c)  # nothing happens before approval
        done = approve(c, r).json()
        assert done["status"] == "submitted" and done["result"]["mode"] == "simulated"
        assert "Agent Builders Hackathon 2026" in my_titles(c)
        assert prepare(c, "luma:l-6").status_code == 400  # already going


def test_rsvp_warns_about_new_clash():
    with TestClient(app) as c:
        approve(c, prepare(c, "meetup:m-5").json())  # Voice Agents Hack Night, Wed 18:00 in Palo Alto
        a = prepare(c, "luma:l-4").json()["action"]  # Agents Happy Hour, Wed 19:00 in SF
        assert a["warnings"] and "Voice Agents Hack Night" in a["warnings"][0]


def test_cancel_removes_event_and_its_clashes():
    with TestClient(app) as c:
        before = c.get("/api/events").json()
        r = prepare(c, "meetup:m-2", "cancel").json()  # SF Bouldering Session
        assert r["action"]["fields"]["Response"] == "Not going"
        approve(c, r)
        after = c.get("/api/events").json()
        assert "SF Bouldering Session" not in {e["title"] for e in after["events"]}
        assert len(after["clashes"]) == len(before["clashes"]) - 2  # it overlapped both Hack Morning and the Brunch
        assert prepare(c, "meetup:m-2", "cancel").status_code == 400  # nothing left to cancel


def test_gate_rejects_bad_reused_expired_and_rejected():
    with TestClient(app) as c:
        assert prepare(c, "luma:nope").status_code == 400
        r = prepare(c, "luma:l-3").json()
        aid = r["action"]["id"]
        assert c.post(f"/api/actions/{aid}/approve", json={"token": "guess"}).status_code == 403
        assert c.post(f"/api/actions/{aid}/reject").json()["status"] == "rejected"
        assert approve(c, r).status_code == 403  # rejected tokens are dead

        main.APPROVAL_TTL_S = -1
        try:
            late = prepare(c, "luma:l-3").json()
            assert approve(c, late).status_code == 403
            assert c.get("/api/actions").json()[-1]["status"] == "expired"
        finally:
            main.APPROVAL_TTL_S = 600
