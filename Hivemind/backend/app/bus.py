import asyncio
import os
from collections import deque

from .models import TraceEvent


class Trace:
    """Every hand-off lands here; /api/trace streams it over SSE."""

    def __init__(self):
        self.history: deque[TraceEvent] = deque(maxlen=500)
        self.subs: set[asyncio.Queue] = set()

    def emit(self, frm, to, kind, summary, data=None) -> TraceEvent:
        ev = TraceEvent(frm=frm, to=to, kind=kind, summary=summary, data=data)
        self.history.append(ev)
        for q in self.subs:
            q.put_nowait(ev)
        return ev

    async def stream(self):
        q: asyncio.Queue = asyncio.Queue()
        self.subs.add(q)
        try:
            for ev in list(self.history):
                yield ev
            while True:
                yield await q.get()
        finally:
            self.subs.discard(q)


def connect_band():
    # ponytail: BAND SDK not wired yet. Return a client with `async request(to, kind, payload) -> json`
    # and the bus uses it; any failure falls back to in-process delivery.
    return None


def _summary(reply) -> str:
    if isinstance(reply, list):
        return f"{len(reply)} items"
    if isinstance(reply, dict):
        return ", ".join(list(reply)[:4])
    return str(reply)[:80]


class Bus:
    """Agent-to-agent messages. Payloads and replies are plain JSON so BAND can carry them."""

    def __init__(self, trace: Trace):
        self.trace = trace
        self.agents = {}
        self.band = connect_band() if os.getenv("BUS", "band") == "band" else None

    @property
    def transport(self) -> str:
        return "band" if self.band else "in-process"

    def register(self, agent):
        self.agents[agent.name] = agent

    async def ask(self, frm, to, skill, **payload):
        self.trace.emit(frm, to, skill, f"{frm} → {to}: {skill}", {"transport": self.transport})
        if self.band:
            try:
                reply = await self.band.request(to, skill, payload)
            except Exception as e:
                self.band = None
                self.trace.emit("bus", to, "fallback", f"BAND failed ({e}); switching to in-process")
        if not self.band:
            reply = await self.agents[to].handle(skill, payload)
        self.trace.emit(to, frm, f"{skill}.reply", f"{to} → {frm}: {_summary(reply)}")
        return reply
