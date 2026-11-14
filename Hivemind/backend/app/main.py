import html
import os
import secrets
import time
from contextlib import asynccontextmanager
from typing import Literal

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel

from . import cognee_store
from .agents import ALL
from .bus import Bus, Trace
from .fixtures import MODE
from .models import Action

O = "orchestrator"
trace = Trace()
bus = Bus(trace)
for A in ALL:
    bus.register(A(bus))

actions: dict[str, Action] = {}
tokens: dict[str, str] = {}  # action id -> one-time approval token
APPROVAL_TTL_S = int(os.getenv("APPROVAL_TTL_S", "600"))  # unapproved actions expire after 10 min


async def learn_profile():
    hist = await bus.ask(O, "scout", "history")
    return await bus.ask(O, "memory", "learn", events=hist)


async def _cleanup_vm(a: Action, final_status: str = "stopped"):
    if a.browser and a.browser.status not in ("stopped", "submitted"):
        await bus.ask(O, "registrar", "cleanup", action=a.model_dump(mode="json"))
        a.browser.status = final_status
        trace.emit(O, "registrar", "vm.stopped", f"Stopped browser VM {a.browser.sandbox_id}",
                   {"action_id": a.id, "sandbox_id": a.browser.sandbox_id})


@asynccontextmanager
async def lifespan(_):
    await learn_profile()
    yield
    for a in list(actions.values()):
        await _cleanup_vm(a)


app = FastAPI(title="Event Agent", lifespan=lifespan)


@app.get("/api/health")
async def health():
    return {
        "mode": MODE,
        "transport": bus.transport,
        "submit": os.getenv("REGISTRAR_SUBMIT", "simulated"),
        "boat_configured": bool(os.getenv("BOAT_API_KEY")),
        "memory_dir": str(cognee_store.ROOT),
        "approval_ttl_s": APPROVAL_TTL_S,
        "agents": list(bus.agents),
    }


@app.get("/api/events")
async def events():
    evs = await bus.ask(O, "scout", "my_events")
    prof = await bus.ask(O, "memory", "profile")
    clashes = await bus.ask(O, "conflicts", "check", events=evs, profile=prof)
    return {"events": evs, "clashes": clashes}


class AskReq(BaseModel):
    prompt: str


@app.post("/api/ask")
async def ask(req: AskReq):
    prof = await bus.ask(O, "memory", "profile")
    cat = await bus.ask(O, "scout", "catalog")
    return {"results": await bus.ask(O, "ranker", "rank", prompt=req.prompt, profile=prof, events=cat)}


@app.get("/api/profile")
async def profile():
    return await bus.ask(O, "memory", "profile")


class NoteReq(BaseModel):
    text: str


@app.post("/api/profile/notes")
async def add_note(req: NoteReq):
    return await bus.ask(O, "memory", "remember", text=req.text)


class ActionReq(BaseModel):
    event_id: str
    kind: Literal["rsvp", "cancel"]


@app.exception_handler(ValueError)
async def bad_request(_, exc: ValueError):  # agents raise ValueError for things the user can fix
    return JSONResponse({"detail": str(exc)}, status_code=400)


@app.post("/api/actions")
async def prepare(req: ActionReq):
    """Registrar fills the form; we pause and hand the user a one-time token. Nothing is submitted here."""
    a = Action(**await bus.ask(O, "registrar", "prepare", event_id=req.event_id, kind=req.kind),
               expires_at=time.time() + APPROVAL_TTL_S)
    actions[a.id] = a
    tokens[a.id] = token = secrets.token_urlsafe(16)
    if a.browser:
        trace.emit(O, "registrar", "vm.ready", f"Browser VM {a.browser.sandbox_id} ready for {a.event_title}",
                   {"action_id": a.id, "sandbox_id": a.browser.sandbox_id, "viewer_url": a.browser.viewer_url})
    trace.emit(O, "user", "approval.wait", f"Paused: {a.kind} for {a.event_title} needs your approval",
               {"action_id": a.id, "warnings": a.warnings})
    return {"action": a, "token": token}


@app.get("/api/actions")
async def list_actions():
    for a in actions.values():
        if a.status == "pending" and time.time() > a.expires_at:
            a.status = "expired"
            tokens.pop(a.id, None)
            await _cleanup_vm(a)
    return list(actions.values())


class ApproveReq(BaseModel):
    token: str


@app.post("/api/actions/{aid}/approve")
async def approve(aid: str, req: ApproveReq):
    expected = tokens.get(aid)
    if not expected or not secrets.compare_digest(expected, req.token):
        raise HTTPException(403, "invalid or used approval token")
    del tokens[aid]  # single use; no await between check and delete, so no race
    a = actions[aid]
    if time.time() > a.expires_at:
        a.status = "expired"
        await _cleanup_vm(a)
        raise HTTPException(403, "approval expired; prepare the action again")
    trace.emit("user", O, "approval.ok", f"Approved {a.kind} for {a.event_title}")
    try:
        a.result = await bus.ask(O, "registrar", "submit", action=a.model_dump(mode="json"))
        a.status = "submitted"
        if a.browser:
            a.browser.status = "submitted"
    except Exception as e:
        a.status, a.result = "failed", {"ok": False, "error": str(e)}
        if a.browser:
            a.browser.status = "failed"
            a.browser.error = str(e)
    return a


@app.post("/api/actions/{aid}/reject")
async def reject(aid: str):
    if tokens.pop(aid, None) is None:
        raise HTTPException(404, "no pending action")
    a = actions[aid]
    a.status = "rejected"
    await _cleanup_vm(a)
    trace.emit("user", O, "approval.no", f"Rejected {a.kind} for {a.event_title}")
    return a


@app.get("/api/vm/{sandbox_id}", response_class=HTMLResponse)
async def vm_preview(sandbox_id: str):
    a = next((x for x in actions.values() if x.browser and x.browser.sandbox_id == sandbox_id), None)
    if not a:
        raise HTTPException(404, "VM session not found")
    rows = "".join(
        f"<tr><td style='color:#6b7280;padding:6px 12px 6px 0'>{html.escape(str(k))}</td>"
        f"<td><input readonly value='{html.escape(str(v))}' style='width:100%;padding:6px 8px;border:1px solid #d1d5db;border-radius:6px;background:#f9fafb'/></td></tr>"
        for k, v in a.fields.items()
    )
    state_label = "SUBMITTED" if a.status == "submitted" else ("STOPPED" if a.browser.status == "stopped" else "PAUSED FOR APPROVAL")
    return f"""<!doctype html>
<html><head><meta charset="utf-8"><title>Boat VM {html.escape(sandbox_id)}</title></head>
<body style="margin:0;background:#0f172a;color:#f8fafc;font:13px/1.4 -apple-system,BlinkMacSystemFont,sans-serif;padding:12px">
  <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px;color:#94a3b8;font-size:12px">
    <span>Boat Ubuntu 24.04 Desktop · <b>{html.escape(sandbox_id)}</b></span>
    <span style="background:#1e293b;padding:2px 8px;border-radius:99px;color:#38bdf8">{state_label}</span>
  </div>
  <div style="background:#ffffff;color:#111827;border-radius:8px;overflow:hidden;box-shadow:0 10px 25px rgba(0,0,0,.4)">
    <div style="background:#f1f5f9;padding:8px 12px;border-bottom:1px solid #e2e8f0;display:flex;gap:8px;align-items:center">
      <span style="display:inline-flex;gap:5px"><i style="width:10px;height:10px;border-radius:50%;background:#ef4444;display:inline-block"></i><i style="width:10px;height:10px;border-radius:50%;background:#f59e0b;display:inline-block"></i><i style="width:10px;height:10px;border-radius:50%;background:#10b981;display:inline-block"></i></span>
      <div style="flex:1;background:#fff;border:1px solid #cbd5e1;border-radius:6px;padding:4px 10px;color:#475569;font-family:monospace;font-size:12px">{html.escape(a.url or f"https://{a.platform}.com/events/{a.event_id}")}</div>
    </div>
    <div style="padding:16px 20px">
      <div style="font-size:11px;text-transform:uppercase;color:#6366f1;font-weight:700;letter-spacing:.05em">{html.escape(a.platform)} Registration Form</div>
      <h3 style="margin:4px 0 12px;font-size:16px">{html.escape(a.event_title)} ({html.escape(a.kind.upper())})</h3>
      <table style="width:100%;border-collapse:collapse">{rows}</table>
      <div style="margin-top:14px;padding:8px 10px;background:#eff6ff;border:1px solid #bfdbfe;border-radius:6px;color:#1e40af;font-size:12px">
        Registrar pre-filled these fields in Chrome. Click <b>Approve</b> in Hivemind to submit.
      </div>
    </div>
  </div>
</body></html>"""


@app.get("/api/trace")
async def stream():
    async def gen():
        async for ev in trace.stream():
            yield f"data: {ev.model_dump_json()}\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream")
