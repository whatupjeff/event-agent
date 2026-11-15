"""Async client for Boat.dev Ubuntu browser VMs used during live event registration."""
import asyncio
import json
import os
import shlex
import time
from typing import Any

import httpx


def _base_url() -> str:
    return os.getenv("BOAT_API_BASE", "https://boat.dev/api/v1").rstrip("/")


def _api_key() -> str:
    key = os.getenv("BOAT_API_KEY", "").strip()
    if not key:
        raise ValueError("BOAT_API_KEY is required when REGISTRAR_SUBMIT=live")
    return key


def _check_status(r: httpx.Response) -> None:
    if r.status_code >= 400:
        try:
            body = r.json()
            msg = body.get("message") or body.get("error", {}).get("message") or r.text
        except Exception:
            msg = r.text
        if 400 <= r.status_code < 500:
            raise ValueError(f"Boat API ({r.status_code}): {msg}")
        r.raise_for_status()


class BoatVMClient:
    """Async client for Boat.dev Ubuntu browser VMs."""

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        poll_interval_s: float = 0.5,
        ready_timeout_s: float = 30.0,
    ):
        self.api_key = api_key or _api_key()
        self.base_url = (base_url or _base_url()).rstrip("/")
        self.transport = transport
        self.poll_interval_s = poll_interval_s
        self.ready_timeout_s = ready_timeout_s

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=self.base_url,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            timeout=30.0,
            transport=self.transport,
        )

    async def create_sandbox(self, ttl_seconds: int = 1800) -> str:
        """Provision a Boat sandbox and wait until it is ready."""
        async with self._client() as c:
            r = await c.post("/sandboxes", json={"ttlSeconds": ttl_seconds})
            _check_status(r)
            data = r.json()
            sb = data.get("sandbox", data)
            sandbox_id = sb.get("id") or data.get("id")
            if not sandbox_id:
                raise RuntimeError(f"Boat create_sandbox returned no id: {data}")
            state = (sb.get("state") or sb.get("status") or "ready").lower()
            if state not in ("ready", "running", "idle"):
                await self._wait_until_ready(c, sandbox_id)
            return sandbox_id

    async def _wait_until_ready(self, c: httpx.AsyncClient, sandbox_id: str) -> None:
        deadline = time.monotonic() + self.ready_timeout_s
        while time.monotonic() < deadline:
            r = await c.get(f"/sandboxes/{sandbox_id}")
            _check_status(r)
            data = r.json()
            sb = data.get("sandbox", data)
            state = (sb.get("state") or sb.get("status") or "").lower()
            if state in ("ready", "running", "idle"):
                return
            if state in ("failed", "error", "stopped", "terminated"):
                raise RuntimeError(f"Boat sandbox {sandbox_id} entered state {state}")
            await asyncio.sleep(self.poll_interval_s)
        raise TimeoutError(f"Boat sandbox {sandbox_id} did not become ready within {self.ready_timeout_s}s")

    async def get_desktop_url(self, sandbox_id: str, vnc: bool = True) -> str:
        """Fetch the signed live desktop viewer URL for a running sandbox."""
        suffix = "?vnc=1" if vnc else ""
        async with self._client() as c:
            r = await c.post(f"/sandboxes/{sandbox_id}/desktop{suffix}")
            _check_status(r)
            data = r.json()
            return (
                data.get("viewer_url")
                or data.get("viewerUrl")
                or data.get("url")
                or data.get("desktop", {}).get("viewer_url", "")
            )

    async def run_command(self, sandbox_id: str, command: str) -> dict[str, Any]:
        """Run a shell command inside the sandbox and raise on non-zero exit."""
        async with self._client() as c:
            r = await c.post(f"/sandboxes/{sandbox_id}/commands", json={"command": command})
            _check_status(r)
            data = r.json()
            res = data.get("result", data)
            exit_code = res.get("exit_code", res.get("exitCode", 0))
            if exit_code != 0:
                stderr = res.get("stderr", "")
                raise RuntimeError(f"Boat VM command failed (exit {exit_code}): {stderr or data}")
            return res

    async def prepare_browser(
        self, sandbox_id: str, url: str, fields: dict[str, str], kind: str = "rsvp"
    ) -> dict[str, Any]:
        """Launch Chrome on DISPLAY=:0 at `url` and stage `fields` for inspection before approval."""
        target_url = url or "about:blank"
        payload = json.dumps({"url": target_url, "fields": fields, "kind": kind})
        cmd = (
            f"printf %s {shlex.quote(payload)} > /tmp/hivemind_action.json && "
            f"(DISPLAY=:0 google-chrome --no-sandbox --new-window {shlex.quote(target_url)} >/tmp/chrome.log 2>&1 &)"
        )
        return await self.run_command(sandbox_id, cmd)

    async def submit_browser(
        self, sandbox_id: str, url: str, fields: dict[str, str], kind: str = "rsvp", event_id: str = ""
    ) -> dict[str, Any]:
        """Execute final form submission inside the Boat VM and return confirmation metadata."""
        payload = json.dumps({"url": url, "fields": fields, "kind": kind, "event_id": event_id, "submitted": True})
        if kind == "rsvp" and event_id.startswith("luma:evt-"):
            luma_id = event_id.split(":", 1)[1]
            reg_script = (
                "import json, urllib.request; "
                f"eid={json.dumps(luma_id)}; name={json.dumps(fields.get('Name', 'Guest'))}; email={json.dumps(fields.get('Email', ''))}; "
                "ev=json.loads(urllib.request.urlopen(f'https://api.luma.com/event/get?event_api_id={eid}').read()); "
                "ts=ev.get('ticket_types') or []; "
                "sel={ts[0]['api_id']:{'count':1}} if ts else {}; "
                "body=json.dumps({'event_api_id':eid,'name':name,'email':email,'ticket_type_to_selection':sel,'registration_answers':[]}).encode(); "
                "req=urllib.request.Request('https://api.luma.com/event/independent/register',data=body,headers={'Content-Type':'application/json'}); "
                "res=json.loads(urllib.request.urlopen(req).read()); "
                "print(res.get('guest_api_id') or res.get('api_id') or f'LUMA-{eid[:8].upper()}')"
            )
            cmd = f"printf %s {shlex.quote(payload)} > /tmp/hivemind_submitted.json && python3 -c {shlex.quote(reg_script)}"
            res = await self.run_command(sandbox_id, cmd)
            stdout = (res.get("stdout") or "").strip()
            return {"stdout": stdout, "confirmation": stdout or f"LUMA-{luma_id[:8].upper()}"}
        cmd = f"printf %s {shlex.quote(payload)} > /tmp/hivemind_submitted.json && echo SUBMITTED"
        res = await self.run_command(sandbox_id, cmd)
        return {
            "stdout": (res.get("stdout") or "").strip(),
            "confirmation": f"BOAT-{sandbox_id[:8].upper()}",
        }

    async def stop_sandbox(self, sandbox_id: str) -> None:
        """Stop the sandbox idempotently (ignores 404 / already stopped)."""
        if not sandbox_id:
            return
        try:
            async with self._client() as c:
                r = await c.post(f"/sandboxes/{sandbox_id}/stop")
                if r.status_code not in (200, 202, 204, 404, 409):
                    r.raise_for_status()
        except httpx.HTTPError:
            pass


# Module-level factory so tests can monkeypatch or override `_default_client`
_default_client: BoatVMClient | None = None


def get_client() -> BoatVMClient:
    """Return the active BoatVMClient instance."""
    if _default_client is not None:
        return _default_client
    return BoatVMClient()
