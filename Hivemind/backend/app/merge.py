"""Collapse the same event listed on several platforms into one, tagged with all its platforms."""
import os
import re
from datetime import timedelta
from difflib import SequenceMatcher

from .models import Event

TITLE_SIM = float(os.getenv("MERGE_TITLE_SIM", "0.8"))  # title alone is enough
WEAK_TITLE_SIM = float(os.getenv("MERGE_WEAK_TITLE_SIM", "0.5"))  # needs a venue match too
VENUE_SIM = float(os.getenv("MERGE_VENUE_SIM", "0.8"))
MAX_START_GAP = timedelta(minutes=int(os.getenv("MERGE_MAX_START_GAP_MIN", "30")))

# which copy's status wins when platforms disagree (Luma going + Eventbrite none -> going)
STATUS_RANK = {"going": 4, "waitlist": 3, "went": 2, "skipped": 1, "none": 0}


def _tokens(s: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", s.lower())


def sim(a: str, b: str) -> float:
    """1.0 when one name is the other plus extras ("X @ Venue", "X (hands-on)"), else fuzzy ratio."""
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return 0.0
    A, B = set(ta), set(tb)
    small = min(len(A), len(B))
    contained = len(A & B) / small if small >= 2 else float(A == B)  # 1-word names must match exactly
    return max(contained, SequenceMatcher(None, " ".join(ta), " ".join(tb)).ratio())


def same_event(a: Event, b: Event) -> bool:
    if a.city.lower() != b.city.lower() or abs(a.start - b.start) > MAX_START_GAP:
        return False
    t = sim(a.title, b.title)
    return t >= TITLE_SIM or (t >= WEAK_TITLE_SIM and sim(a.venue, b.venue) >= VENUE_SIM)


def _combine(group: list[Event]) -> Event:
    primary = max(group, key=lambda e: STATUS_RANK[e.status])  # first on ties
    return primary.model_copy(update={
        "platforms": list(dict.fromkeys(p for e in group for p in e.platforms)),
        "urls": {k: v for e in group for k, v in e.urls.items()},
        "tags": list(dict.fromkeys(t for e in group for t in e.tags)),
        "description": max((e.description for e in group), key=len),
        "venue": max((e.venue for e in group), key=len),
        "source_ids": [s for e in group for s in (e.source_ids or [e.id])],
    })


def merge_events(events: list[Event]) -> list[Event]:
    # ponytail: O(n²) compare, fine for a few hundred events; bucket by (city, day) if it grows
    groups: list[list[Event]] = []
    for e in sorted(events, key=lambda e: e.start):
        g = next((g for g in groups if any(same_event(e, x) for x in g)), None)
        g.append(e) if g else groups.append([e])
    return [_combine(g) for g in groups]
