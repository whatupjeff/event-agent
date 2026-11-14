import json

import httpx
import pytest
from fastapi.testclient import TestClient

from app import boat_vm, main
from app.main import app


class FakeBoatAPI:
    """In-memory mock transport simulating Boat.dev /api/v1 endpoints."""

    def __init__(self, fail_command_on_prepare: bool = False):
        self.calls: list[tuple[str, str, dict | None]] = []
        self.poll_count = 0
        self.fail_command_on_prepare = fail_command_on_prepare
        self.transport = httpx.MockTransport(self._handler)

    def _handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path.removeprefix("/api/v1")
        query = request.url.query.decode() if request.url.query else ""
        full_path = f"{path}?{query}" if query else path
        body = json.loads(request.content.decode()) if request.content else None
        self.calls.append((request.method, full_path, body))

        if request.method == "POST" and path == "/sandboxes":
            return httpx.Response(201, json={"sandbox": {"id": "sbx_1234567890", "state": "provisioning"}})

        if request.method == "GET" and path == "/sandboxes/sbx_1234567890":
            self.poll_count += 1
            state = "ready" if self.poll_count >= 1 else "provisioning"
            return httpx.Response(200, json={"sandbox": {"id": "sbx_1234567890", "state": state}})

        if request.method == "POST" and path == "/sandboxes/sbx_1234567890/desktop":
            return httpx.Response(200, json={"viewer_url": "https://boat.dev/desktop/sbx_1234567890?token=abc"})

        if request.method == "POST" and path == "/sandboxes/sbx_1234567890/commands":
            cmd = (body or {}).get("command", "")
            if self.fail_command_on_prepare and "hivemind_action.json" in cmd:
                return httpx.Response(200, json={"result": {"exit_code": 1, "stderr": "chrome failed"}})
            return httpx.Response(200, json={"result": {"exit_code": 0, "stdout": "OK", "stderr": ""}})

        if request.method == "POST" and path == "/sandboxes/sbx_1234567890/stop":
            return httpx.Response(200, json={"sandbox": {"id": "sbx_1234567890", "state": "stopped"}})

        return httpx.Response(404, json={"error": f"unhandled {request.method} {full_path}"})


@pytest.fixture
def live_boat(monkeypatch):
    fake = FakeBoatAPI()
    monkeypatch.setenv("REGISTRAR_SUBMIT", "live")
    monkeypatch.setenv("BOAT_API_KEY", "boat_test_key")
    boat_vm._default_client = boat_vm.BoatVMClient(
        api_key="boat_test_key",
        transport=fake.transport,
        poll_interval_s=0.0,
    )
    return fake


def test_prepare_provisions_boat_vm_and_prefills(live_boat):
    with TestClient(app) as c:
        health = c.get("/api/health").json()
        assert health["submit"] == "live"
        assert health["boat_configured"] is True

        res = c.post("/api/actions", json={"event_id": "meetup:m-6", "kind": "rsvp"}).json()
        a = res["action"]
        assert a["status"] == "pending"
        assert a["browser"]["sandbox_id"] == "sbx_1234567890"
        assert a["browser"]["status"] == "ready"
        assert a["browser"]["viewer_url"] == "https://boat.dev/desktop/sbx_1234567890?token=abc"

        endpoints = [(m, p) for m, p, _ in live_boat.calls]
        assert ("POST", "/sandboxes") in endpoints
        assert ("GET", "/sandboxes/sbx_1234567890") in endpoints
        assert ("POST", "/sandboxes/sbx_1234567890/desktop?vnc=1") in endpoints
        cmd_bodies = [b["command"] for m, p, b in live_boat.calls if p == "/sandboxes/sbx_1234567890/commands"]
        assert len(cmd_bodies) == 1
        assert "google-chrome" in cmd_bodies[0]
        assert "hivemind_action.json" in cmd_bodies[0]


def test_approve_submits_and_stops_vm(live_boat):
    with TestClient(app) as c:
        prep = c.post("/api/actions", json={"event_id": "meetup:m-6", "kind": "rsvp"}).json()
        done = c.post(f"/api/actions/{prep['action']['id']}/approve", json={"token": prep["token"]}).json()

        assert done["status"] == "submitted"
        assert done["browser"]["status"] == "submitted"
        assert done["result"]["mode"] == "live"
        assert done["result"]["sandbox_id"] == "sbx_1234567890"
        assert done["result"]["confirmation"] == "BOAT-SBX_1234"

        cmd_bodies = [b["command"] for m, p, b in live_boat.calls if p == "/sandboxes/sbx_1234567890/commands"]
        assert any("hivemind_submitted.json" in cmd for cmd in cmd_bodies)
        assert ("POST", "/sandboxes/sbx_1234567890/stop") in [(m, p) for m, p, _ in live_boat.calls]

        my_titles = {e["title"] for e in c.get("/api/events").json()["events"]}
        assert "Agent Builders Hackathon 2026" in my_titles


def test_reject_stops_vm(live_boat):
    with TestClient(app) as c:
        prep = c.post("/api/actions", json={"event_id": "luma:l-3", "kind": "rsvp"}).json()
        rejected = c.post(f"/api/actions/{prep['action']['id']}/reject").json()

        assert rejected["status"] == "rejected"
        assert rejected["browser"]["status"] == "stopped"
        assert ("POST", "/sandboxes/sbx_1234567890/stop") in [(m, p) for m, p, _ in live_boat.calls]


def test_expired_action_stops_vm(live_boat):
    with TestClient(app) as c:
        main.APPROVAL_TTL_S = -1
        try:
            prep_list = c.post("/api/actions", json={"event_id": "luma:l-3", "kind": "rsvp"}).json()
            actions_resp = c.get("/api/actions").json()
            assert actions_resp[-1]["status"] == "expired"
            assert actions_resp[-1]["browser"]["status"] == "stopped"

            live_boat.calls.clear()
            prep_approve = c.post("/api/actions", json={"event_id": "luma:l-3", "kind": "rsvp"}).json()
            resp = c.post(
                f"/api/actions/{prep_approve['action']['id']}/approve",
                json={"token": prep_approve["token"]},
            )
            assert resp.status_code == 403
            assert ("POST", "/sandboxes/sbx_1234567890/stop") in [(m, p) for m, p, _ in live_boat.calls]
        finally:
            main.APPROVAL_TTL_S = 600


def test_prepare_failure_cleans_up_sandbox(monkeypatch):
    fake = FakeBoatAPI(fail_command_on_prepare=True)
    monkeypatch.setenv("REGISTRAR_SUBMIT", "live")
    monkeypatch.setenv("BOAT_API_KEY", "boat_test_key")
    boat_vm._default_client = boat_vm.BoatVMClient(
        api_key="boat_test_key",
        transport=fake.transport,
        poll_interval_s=0.0,
    )

    with TestClient(app) as c:
        with pytest.raises(RuntimeError, match="chrome failed"):
            c.post("/api/actions", json={"event_id": "meetup:m-6", "kind": "rsvp"})
        assert ("POST", "/sandboxes/sbx_1234567890/stop") in [(m, p) for m, p, _ in fake.calls]
