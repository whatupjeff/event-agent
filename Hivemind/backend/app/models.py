import time
import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

Platform = Literal["luma", "meetup", "eventbrite"]
# going/waitlist = upcoming and mine; went/skipped = history; none = discoverable
Status = Literal["going", "waitlist", "went", "skipped", "none"]


def _id() -> str:
    return uuid.uuid4().hex


class Event(BaseModel):
    id: str  # "<platform>:<native id>"; after merge, the id of the copy whose status won
    title: str
    start: datetime
    end: datetime
    venue: str = ""
    city: str = ""
    lat: float | None = None  # venue coordinates, for travel time
    lon: float | None = None
    description: str = ""
    tags: list[str] = []
    status: Status = "none"
    platforms: list[Platform]
    urls: dict[str, str] = {}  # platform -> event page
    source_ids: list[str] = []  # every platform listing merged into this one


class Profile(BaseModel):
    topics: dict[str, float] = {}  # tag -> learned weight (negative = avoid)
    cities: dict[str, float] = {}
    facts: list[str] = []  # "what I learned about you", from local counts
    insights: list[str] = []  # same question answered by cognee from the memory graph
    notes: list[str] = []  # things the user told us directly


class Clash(BaseModel):
    a_id: str
    b_id: str
    kind: Literal["overlap", "travel"]
    keep_id: str
    reason: str
    gap_min: int | None = None  # travel clashes: free time between them
    travel_min: int | None = None  # ...and the estimated trip


class Ranked(BaseModel):
    event: Event
    score: int  # 0-100
    reason: str
    signals: dict[str, float] = {}  # relevance, taste, in_dates, in_city: the "why" behind the score


class BrowserSession(BaseModel):
    sandbox_id: str
    status: Literal["provisioning", "ready", "submitted", "stopped", "failed"] = "provisioning"
    viewer_url: str = ""
    error: str = ""


class Action(BaseModel):
    id: str = Field(default_factory=_id)
    kind: Literal["rsvp", "cancel"]
    event_id: str
    event_title: str = ""
    platform: Platform
    url: str = ""
    fields: dict[str, str] = {}  # exactly what will be filled in, shown to the user before approval
    warnings: list[str] = []  # e.g. "this overlaps with X"
    status: Literal["pending", "submitted", "rejected", "expired", "failed"] = "pending"
    expires_at: float = 0
    result: dict[str, Any] | None = None
    browser: BrowserSession | None = None


class TraceEvent(BaseModel):
    id: str = Field(default_factory=_id)
    ts: float = Field(default_factory=time.time)
    frm: str
    to: str
    kind: str
    summary: str
    data: dict[str, Any] | None = None
