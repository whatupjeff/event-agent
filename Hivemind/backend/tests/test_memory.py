import asyncio
import json
import sys

from app import agents, fixtures
from app.agents import PROFILE_Q, Memory, Scout, _key
from app.bus import Bus, Trace


def learn():
    bus = Bus(Trace())
    for A in (Scout, Memory):
        bus.register(A(bus))
    hist = asyncio.run(bus.ask("test", "scout", "history"))
    return bus, hist, asyncio.run(bus.ask("test", "memory", "learn", events=hist))


def test_history_becomes_memories_offline():
    bus, hist, prof = learn()
    assert len(hist) == 12
    assert prof["topics"]["agents"] > 0 > prof["topics"]["crypto"]
    assert "cognee" not in sys.modules  # fixtures mode never touches cognee
    assert {"remember", "recall"} <= {t.kind for t in bus.trace.history}
    assert agents.as_memory(agents.Event.model_validate(hist[0])).startswith(("I attended", "I RSVP'd", "I skipped", "I was waitlisted"))


def test_recorded_cognee_answer_is_replayed(tmp_path, monkeypatch):
    monkeypatch.setattr(fixtures, "DIR", tmp_path)
    (tmp_path / "cognee").mkdir()
    (tmp_path / "cognee" / f"recall_{_key(PROFILE_Q)}.json").write_text(json.dumps(["Loves agent hackathons"]))
    for p in agents.PLATFORMS:  # Scout still needs the event fixtures
        (tmp_path / f"{p}.json").write_text((fixtures.Path(__file__).parent.parent / "fixtures" / f"{p}.json").read_text())
    _, _, prof = learn()
    assert prof["insights"] == ["Loves agent hackathons"]


def test_user_note_is_kept():
    bus, _, _ = learn()
    prof = asyncio.run(bus.ask("test", "memory", "remember", text="No events before 9 AM"))
    assert prof["notes"] == ["No events before 9 AM"]
