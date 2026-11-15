"""The agent team. Each agent exposes `skills`; the bus calls them by name with JSON payloads."""
import hashlib
import math
import os
import re
import time
from collections import Counter
from datetime import datetime

import httpx

from . import boat_vm, cognee_store, sie
from .fixtures import MODE, NOW, afixture, fixture
from .merge import merge_events
from .models import BrowserSession, Clash, Event, Profile, Ranked

PLATFORMS = ("luma", "meetup", "eventbrite")


def dump(xs):
    return [x.model_dump(mode="json") for x in xs]


AVOID_WEIGHT = float(os.getenv("TASTE_AVOID_WEIGHT", "2"))  # a topic you skip hurts more than a liked one helps


def taste(e: Event, profile: dict) -> float:
    ws = [profile.get("topics", {}).get(t, 0) for t in e.tags]
    return sum(w if w > 0 else AVOID_WEIGHT * w for w in ws)


class Agent:
    name = ""
    skills: tuple[str, ...] = ()

    def __init__(self, bus):
        self.bus = bus

    async def handle(self, skill, payload):
        if skill not in self.skills:  # messages can arrive over the network via BAND
            raise ValueError(f"{self.name} has no skill {skill!r}")
        return await getattr(self, skill)(**payload)


class Scout(Agent):
    name = "scout"
    skills = ("my_events", "history", "catalog", "get", "set_status")

    def __init__(self, bus):
        super().__init__(bus)
        self.overrides: dict[str, str] = {}  # event id -> status after an approved RSVP/cancel (local only)
        self._merged = 0
        self._live_luma: list[Event] = []
        self._live_luma_ts: float = 0.0

    async def _fetch_luma_discovery(self) -> list[Event]:
        if self._live_luma and (time.time() - self._live_luma_ts) < 60:
            return self._live_luma
        async with httpx.AsyncClient(timeout=10.0) as c:
            r = await c.get("https://api.luma.com/discover/get-paginated-events", params={"pagination_limit": 25})
            r.raise_for_status()
            entries = r.json().get("entries", [])
        events: list[Event] = []
        for entry in entries:
            ev = entry.get("event") or {}
            eid = ev.get("api_id")
            if not eid or not ev.get("start_at") or not ev.get("end_at"):
                continue
            geo = ev.get("geo_address_info") or {}
            coord = ev.get("coordinate") or geo.get("place_coordinate") or {}
            slug = ev.get("url") or eid
            url = slug if slug.startswith("http") else f"https://lu.ma/{slug}"
            tags = [t.get("name", "").lower() for t in (entry.get("tags") or []) if t.get("name")]
            if not tags and ev.get("event_type"):
                tags = [str(ev["event_type"]).lower()]
            events.append(Event(
                id=f"luma:{eid}",
                title=ev.get("name", "Untitled Event"),
                start=ev["start_at"],
                end=ev["end_at"],
                venue=geo.get("short_address") or geo.get("address") or ("Online" if ev.get("location_type") == "online" else "TBA"),
                city=geo.get("city") or ("Online" if ev.get("location_type") == "online" else ""),
                lat=coord.get("latitude"),
                lon=coord.get("longitude"),
                description=geo.get("description") or "",
                tags=tags,
                status="none",
                platforms=["luma"],
                urls={"luma": url},
                source_ids=[f"luma:{eid}"],
                attendees=entry.get("guest_count"),
            ))
        self._live_luma = events
        self._live_luma_ts = time.time()
        self.bus.trace.emit(self.name, "luma", "discover", f"Fetched {len(events)} live events from Luma Discovery API")
        return events

    def _all(self) -> list[Event]:
        out = []
        for p in PLATFORMS:
            for raw in fixture(p):
                out.append(Event(**{**raw, "id": f"{p}:{raw['id']}", "platforms": [p], "urls": {p: raw.get("url", "")}}))
        merged = merge_events(out)
        if len(out) - len(merged) != self._merged:  # report merges once, not on every call
            self._merged = len(out) - len(merged)
            self.bus.trace.emit(self.name, self.name, "merge", f"Merged {len(out) - len(merged)} duplicate listings across platforms")
        all_evs = merged + [e for e in self._live_luma]
        return [e.model_copy(update={"status": self.overrides[e.id]}) if e.id in self.overrides else e for e in all_evs]

    async def my_events(self):
        return dump(e for e in self._all() if e.start >= NOW and e.status in ("going", "waitlist"))

    async def history(self):  # went / RSVP'd (going) / skipped / waitlisted, all in the past
        return dump(e for e in self._all() if e.start < NOW and e.status != "none")

    async def catalog(self):
        if MODE != "fixtures":
            live = await self._fetch_luma_discovery()
            return dump(
                e.model_copy(update={"status": self.overrides[e.id]}) if e.id in self.overrides else e
                for e in live
                if self.overrides.get(e.id, e.status) == "none"
            )
        return dump(e for e in self._all() if e.start >= NOW and e.status == "none")

    async def get(self, event_id):
        for e in self._all():
            if event_id == e.id or event_id in e.source_ids:
                return e.model_dump(mode="json")
        raise ValueError(f"unknown event {event_id!r}")

    async def set_status(self, event_id, status):
        e = Event.model_validate(await self.get(event_id))
        self.overrides[e.id] = status
        self.bus.trace.emit(self.name, self.name, "status", f"{e.title} is now '{status}' in your events")
        return {"event_id": e.id, "status": status}


# ponytail: hand-set signal strengths; learn them (or let the user tune them in onboarding) later
WEIGHT = {"went": 1.0, "going": 0.6, "waitlist": 0.4, "skipped": -0.5}
VERB = {"went": "I attended", "going": "I RSVP'd to", "waitlist": "I was waitlisted for", "skipped": "I skipped"}
PROFILE_Q = "What kinds of events does this person attend, and which do they avoid? Answer in short bullet points."


def as_memory(e: Event) -> str:
    """One history event as a sentence for cognee's graph."""
    return (f"{VERB.get(e.status, 'I saw')} '{e.title}' on {e.start:%Y-%m-%d} at {e.venue}, {e.city} "
            f"(on {', '.join(e.platforms)}). Topics: {', '.join(e.tags)}. {e.description}")


def _key(text: str) -> str:
    return hashlib.sha1(text.encode()).hexdigest()[:10]


class Memory(Agent):
    """Learns taste from history. Numbers stay local for ranking; the story goes to cognee."""

    name = "memory"
    skills = ("learn", "profile", "recall", "remember")

    def __init__(self, bus):
        super().__init__(bus)
        self.p = Profile()

    async def learn(self, events):
        evs = list(map(Event.model_validate, events))
        topics, cities = Counter(), Counter()
        for e in evs:
            w = WEIGHT.get(e.status, 0)
            for t in e.tags:
                topics[t] += w
            if e.city and w > 0:
                cities[e.city] += w
        likes = [t for t, w in topics.most_common() if w > 0][:3]
        avoids = [t for t, w in topics.items() if w < 0]
        facts = [f"You keep showing up to {', '.join(likes)} events"] if likes else []
        facts += [f"You tend to skip {t} events" for t in avoids]

        docs = [as_memory(e) for e in evs]
        stored = await afixture("cognee/remember_history", lambda: cognee_store.remember(docs),
                                default={"stored": 0, "note": "not recorded yet; profile is local only"})
        self.bus.trace.emit(self.name, "cognee", "remember", f"Stored {stored['stored']} past events in cognee", stored)
        insights = await self.recall(PROFILE_Q)
        self.p = Profile(topics=dict(topics), cities=dict(cities), facts=facts, insights=insights, notes=self.p.notes)
        return self.p.model_dump()

    async def recall(self, question):
        ans = await afixture(f"cognee/recall_{_key(question)}", lambda: cognee_store.recall(question), default=[])
        self.bus.trace.emit("cognee", self.name, "recall", f"cognee answered with {len(ans)} memories", {"question": question})
        return ans

    async def remember(self, text):
        """Something the user tells us directly (onboarding, 'I hate morning events')."""
        self.p.notes.append(text)
        if MODE != "fixtures":
            await cognee_store.remember([f"The user said: {text}"])
        return self.p.model_dump()

    async def profile(self):
        return self.p.model_dump()


# Score = relevance x (base + taste bonus), then misses on dates/city multiply it down.
W_RELEVANCE = float(os.getenv("RANK_W_RELEVANCE", "0.65"))
W_TASTE = float(os.getenv("RANK_W_TASTE", "0.35"))
MISS_PENALTY = float(os.getenv("RANK_MISS_PENALTY", "0.4"))
# Reranker scores run low on broad asks ("design-related"), so scale against the best match,
# but never by more than 1/REL_FLOOR, so a prompt with no good match stays low.
REL_FLOOR = float(os.getenv("RANK_REL_FLOOR", "0.3"))


def _keyword_relevance(topic: str, e: Event) -> float:
    """Offline fallback for prompts nobody recorded: share of query words found in tags/title."""
    words = [w for w in re.findall(r"[a-z]+", topic.lower()) if len(w) >= 4]
    terms = e.tags + re.findall(r"[a-z]+", e.title.lower())
    return sum(any(t[:5] == w[:5] for t in terms) for w in words) / len(words) if words else 0.0


def as_doc(e: Event) -> str:
    return f"{e.title}. {e.description} Topics: {', '.join(e.tags)}. {e.venue}, {e.city}."


class Ranker(Agent):
    """Scores events 0-100 for a prompt: Superlinked relevance + learned taste + date/city fit."""

    name = "ranker"
    skills = ("rank",)

    async def rank(self, prompt, profile, events):
        evs = list(map(Event.model_validate, events))
        if not evs:
            return []
        default_intent = {"topic": prompt, "date_from": None, "date_to": None, "city": None}
        it = await afixture(f"superlinked/intent_{_key(prompt + NOW.date().isoformat())}",
                            lambda: sie.intent(prompt, NOW), default=None)
        self.bus.trace.emit(self.name, "superlinked", "intent", f"Understood the ask as {it}" if it else "Prompt not recorded; using it as-is")
        it = it or default_intent

        docs = [as_doc(e) for e in evs]
        rel = await afixture(f"superlinked/rerank_{_key(it['topic'] + '|' + '|'.join(docs))}",
                             lambda: sie.rerank(it["topic"], docs), default=None)
        self.bus.trace.emit("superlinked", self.name, "rerank",
                            f"Superlinked scored {len(evs)} events" if rel else "Not recorded; keyword fallback")
        rel = rel or [_keyword_relevance(it["topic"], e) for e in evs]
        top = max(max(rel), REL_FLOOR)
        rel = [min(1.0, r / top) for r in rel]

        lo = it["date_from"] and datetime.fromisoformat(it["date_from"]).date()
        hi = it["date_to"] and datetime.fromisoformat(it["date_to"]).date()
        out = []
        for e, r in zip(evs, rel):
            t = math.tanh(taste(e, profile) / 2)  # -1 avoid .. +1 love
            in_dates = (not lo or e.start.date() >= lo) and (not hi or e.start.date() <= hi)
            in_city = not it["city"] or it["city"].lower() in e.city.lower()
            score = 100 * r * (W_RELEVANCE + W_TASTE * (t + 1) / 2)  # taste boosts matches, can't rescue misses
            score *= (1 if in_dates else MISS_PENALTY) * (1 if in_city else MISS_PENALTY)

            why = [f"strong match for “{it['topic']}”" if r >= 0.5 else f"partly about {it['topic']}" if r >= 0.2
                   else f"not really about {it['topic']}"]
            if lo or hi:
                why.append(f"{e.start:%a %b} {e.start.day}, {'within' if in_dates else 'outside'} your dates")
            if it["city"] and not in_city:
                why.append(f"in {e.city}, not {it['city']}")
            liked = [x for x in e.tags if profile["topics"].get(x, 0) > 0]
            avoided = [x for x in e.tags if profile["topics"].get(x, 0) < 0]
            if avoided:
                why.append(f"but you usually skip {', '.join(avoided)} events")
            elif liked:
                why.append(f"you often go to {', '.join(liked)} events")
            reason = "; ".join(why)
            out.append(Ranked(event=e, score=round(score), reason=reason[0].upper() + reason[1:],
                              signals={"relevance": round(r, 3), "taste": round(t, 3),
                                       "in_dates": float(in_dates), "in_city": float(in_city)}))
        return dump(sorted(out, key=lambda x: -x.score))


# Travel estimate from venue coordinates: straight-line km x road factor at an average speed, plus a buffer.
# ponytail: crow-flies heuristic; swap in a routing API (with traffic at that hour) for live mode
TRAVEL_KMH = float(os.getenv("TRAVEL_KMH", "50"))
TRAVEL_ROAD_FACTOR = float(os.getenv("TRAVEL_ROAD_FACTOR", "1.3"))
TRAVEL_BUFFER_MIN = float(os.getenv("TRAVEL_BUFFER_MIN", "15"))  # parking, walking in, finding the room


def travel_minutes(a: Event, b: Event) -> int | None:
    """Estimated door-to-door minutes from a's venue to b's, or None if a venue has no coordinates."""
    if None in (a.lat, a.lon, b.lat, b.lon):
        return None
    lat1, lon1, lat2, lon2 = map(math.radians, (a.lat, a.lon, b.lat, b.lon))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    km = 2 * 6371 * math.asin(math.sqrt(h))
    if km < 0.2:  # same building
        return 0
    return round(TRAVEL_BUFFER_MIN + km * TRAVEL_ROAD_FACTOR / TRAVEL_KMH * 60)


class Conflicts(Agent):
    """Rules only, no LLM: overlapping events, or too little time to get from one to the next."""

    name = "conflicts"
    skills = ("check",)

    async def check(self, events, profile):
        evs = sorted(map(Event.model_validate, events), key=lambda e: e.start)
        clashes = []
        for i, a in enumerate(evs):
            for b in evs[i + 1 :]:
                gap = (b.start - a.end).total_seconds() / 60  # negative = overlap
                if gap >= 12 * 60:  # sorted by start, so every later event is even further away
                    break
                trip = travel_minutes(a, b) if gap >= 0 else None
                if gap >= 0 and (trip is None or gap >= trip):
                    continue
                keep = max((a, b), key=lambda e: (e.status == "going", taste(e, profile)))
                drop = b if keep is a else a
                why = (
                    f"you're confirmed, {drop.title} is only a waitlist"
                    if keep.status != drop.status
                    else f"closer to what you usually attend ({', '.join(keep.tags[:2])})"
                )
                if gap < 0:
                    clashes.append(Clash(a_id=a.id, b_id=b.id, kind="overlap", keep_id=keep.id, reason=f"Keep {keep.title}: {why}"))
                else:
                    where = f"{a.city} to {b.city}" if a.city != b.city else f"{a.venue} to {b.venue}"
                    clashes.append(Clash(
                        a_id=a.id, b_id=b.id, kind="travel", keep_id=keep.id, gap_min=round(gap), travel_min=trip,
                        reason=f"Only {round(gap)} min to get from {where} (~{trip} min). Keep {keep.title}: {why}",
                    ))
        return dump(clashes)


class Registrar(Agent):
    """Fills the RSVP/cancel form and hands it back for approval. Never submits on its own:
    the orchestrator only calls `submit` after the user approves with the one-time token."""

    name = "registrar"
    skills = ("prepare", "submit", "cleanup")

    async def prepare(self, event_id, kind, name=None, email=None):
        e = Event.model_validate(await self.bus.ask(self.name, "scout", "get", event_id=event_id))
        if kind == "rsvp" and e.status in ("going", "waitlist"):
            raise ValueError(f"you're already {e.status} for {e.title}")
        if kind == "cancel" and e.status not in ("going", "waitlist"):
            raise ValueError(f"nothing to cancel: you're not registered for {e.title}")
        # rsvp on the copy you picked; cancel where your registration lives (the copy whose status won)
        picked = event_id if kind == "rsvp" and event_id in e.source_ids else e.id
        platform = picked.split(":")[0]

        user = {**fixture("user")}
        if name and name.strip():
            user["name"] = name.strip()
        if email and email.strip():
            user["email"] = email.strip()
        form = fixture(f"forms/{platform}")[kind]  # ponytail: recorded form layouts; live mode reads the real page
        fields = {k: v.format_map(user) for k, v in form.items()}
        event_url = e.urls.get(platform, "")

        warnings = []
        if kind == "rsvp":  # would this new event clash with what you already have?
            mine = await self.bus.ask(self.name, "scout", "my_events")
            prof = await self.bus.ask(self.name, "memory", "profile")
            going = e.model_copy(update={"status": "going"}).model_dump(mode="json")
            clashes = await self.bus.ask(self.name, "conflicts", "check", events=mine + [going], profile=prof)
            titles = {x["id"]: x["title"] for x in mine}
            warnings = [f"Clashes with {titles[c['b_id'] if c['a_id'] == e.id else c['a_id']]} ({c['kind']}). {c['reason']}"
                        for c in clashes if e.id in (c["a_id"], c["b_id"])]

        browser = None
        if os.getenv("REGISTRAR_SUBMIT", "simulated") == "live":
            client = boat_vm.get_client()
            ttl_s = int(os.getenv("BOAT_SANDBOX_TTL_S", "900"))
            sandbox_id = await client.create_sandbox(ttl_seconds=ttl_s)
            try:
                viewer_url = await client.get_desktop_url(sandbox_id, vnc=True)
                await client.prepare_browser(sandbox_id, event_url, fields, kind=kind)
            except Exception:
                await client.stop_sandbox(sandbox_id)
                raise
            browser = BrowserSession(
                sandbox_id=sandbox_id,
                status="ready",
                viewer_url=viewer_url,
            ).model_dump(mode="json")
        else:
            sim_id = f"sbx_sim_{os.urandom(4).hex()}"
            browser = BrowserSession(
                sandbox_id=sim_id,
                status="ready",
                viewer_url=f"/api/vm/{sim_id}",
            ).model_dump(mode="json")

        return {"kind": kind, "event_id": e.id, "event_title": e.title, "platform": platform,
                "url": event_url, "fields": fields, "warnings": warnings, "browser": browser}

    async def submit(self, action):
        if os.getenv("REGISTRAR_SUBMIT", "simulated") == "live":
            browser = action.get("browser") or {}
            sandbox_id = browser.get("sandbox_id")
            if not sandbox_id:
                raise ValueError("no active browser VM session for this action")
            client = boat_vm.get_client()
            try:
                sub = await client.submit_browser(
                    sandbox_id,
                    action.get("url", ""),
                    action.get("fields", {}),
                    kind=action["kind"],
                    event_id=action.get("event_id", ""),
                )
            finally:
                await client.stop_sandbox(sandbox_id)
            await self.bus.ask(
                self.name,
                "scout",
                "set_status",
                event_id=action["event_id"],
                status="going" if action["kind"] == "rsvp" else "none",
            )
            return {
                "ok": True,
                "mode": "live",
                "sandbox_id": sandbox_id,
                "confirmation": sub.get("confirmation", f"BOAT-{sandbox_id[:8].upper()}"),
            }
        await self.bus.ask(self.name, "scout", "set_status", event_id=action["event_id"],
                           status="going" if action["kind"] == "rsvp" else "none")
        return {"ok": True, "mode": "simulated", "confirmation": f"SIM-{action['id'][:6].upper()}"}

    async def cleanup(self, action):
        """Stop an active Boat browser VM if one is attached to `action`."""
        browser = (action or {}).get("browser") or {}
        sandbox_id = browser.get("sandbox_id")
        if sandbox_id and browser.get("status") not in ("stopped", "submitted"):
            if os.getenv("REGISTRAR_SUBMIT", "simulated") == "live":
                await boat_vm.get_client().stop_sandbox(sandbox_id)
            return {"ok": True, "sandbox_id": sandbox_id, "status": "stopped"}
        return {"ok": True, "status": browser.get("status", "none")}


ALL = (Scout, Memory, Ranker, Conflicts, Registrar)
