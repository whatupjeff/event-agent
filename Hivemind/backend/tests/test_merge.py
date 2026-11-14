from app.agents import Scout
from app.bus import Bus, Trace
from app.merge import sim

# Ground truth: each group must collapse into one event...
DUPES = [
    {"luma:l-2", "eventbrite:b-4"},  # Agents Hack Morning
    {"luma:l-3", "meetup:m-4"},  # LLM Evals Workshop
    {"luma:l-6", "meetup:m-6", "eventbrite:b-5"},  # Agent Builders Hackathon
]
# ...and these look alike but must stay separate.
NOT_DUPES = [
    {"luma:l-4", "luma:l-5"},  # same title + time, SF vs New York
    {"luma:l-h3", "luma:l-7"},  # AI Tinkerers SF, Oct vs Dec
]


def test_merge_matches_ground_truth():
    merged = Scout(Bus(Trace()))._all()
    groups = [set(e.source_ids or [e.id]) for e in merged]
    for d in DUPES:
        assert d in groups, d
    for nd in NOT_DUPES:
        assert not any(nd <= g for g in groups), nd
    assert sum(len(g) for g in groups) == 33  # nothing dropped
    builders = next(e for e in merged if "meetup:m-6" in e.source_ids)
    assert builders.platforms == ["luma", "meetup", "eventbrite"]
    hack = next(e for e in merged if "eventbrite:b-4" in e.source_ids)
    assert hack.status == "going" and hack.id == "luma:l-2"  # your RSVP wins over the untouched copy


def test_title_sim():
    assert sim("Agents Hack Morning", "Agents Hack Morning @ Pebblebed") == 1
    assert sim("Agent Builders Hackathon 2026", "AGENT BUILDERS HACKATHON") == 1
    assert sim("Agents Hack Morning", "SF Bouldering Session") < 0.5
    assert sim("Meetup", "Python Meetup") < 0.8  # one-word names don't get containment
