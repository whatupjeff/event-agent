"""Record live partner calls into fixtures/ so the demo replays them offline.

    DEMO_MODE=record python -m app.record memory               # cognee: store history, recall profile (~2 min)
    DEMO_MODE=record python -m app.record ask "agent hackathons next week" ...   # Superlinked ranking
"""
import asyncio
import json
import sys

from . import fixtures
from .agents import ALL
from .bus import Bus, Trace

DEMO_PROMPTS = [
    "agent hackathons next week",
    "something design-related this week",
    "AI events in Palo Alto",
    "outdoor stuff this weekend",
]


async def main(what: str, prompts: list[str]):
    assert fixtures.MODE == "record", "run with DEMO_MODE=record"
    bus = Bus(Trace())
    for A in ALL:
        bus.register(A(bus))
    hist = await bus.ask("record", "scout", "history")
    if what == "memory":
        prof = await bus.ask("record", "memory", "learn", events=hist)
        print("cognee insights:", json.dumps(prof["insights"], indent=1))
        return
    fixtures.MODE = "fixtures"  # reuse the recorded profile instead of re-running cognee
    prof = await bus.ask("record", "memory", "learn", events=hist)
    fixtures.MODE = "record"
    cat = await bus.ask("record", "scout", "catalog")
    for p in prompts or DEMO_PROMPTS:
        res = await bus.ask("record", "ranker", "rank", prompt=p, profile=prof, events=cat)
        print(f"\n## {p}")
        for r in res[:4]:
            print(f"{r['score']:>3}  {r['event']['title']}  ({r['reason']})")


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1], sys.argv[2:]))
