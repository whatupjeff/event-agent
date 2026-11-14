"""Ranking replays recorded Superlinked calls (fixtures/superlinked/), so these run offline."""
from fastapi.testclient import TestClient

from app.main import app


def ask(c, prompt):
    return c.post("/api/ask", json={"prompt": prompt}).json()["results"]


def test_recorded_prompts_rank_sensibly():
    with TestClient(app) as c:
        res = ask(c, "agent hackathons next week")
        titles = [r["event"]["title"] for r in res]
        top3 = set(titles[:3])
        assert {"Voice Agents Hack Night", "Agent Builders Hackathon 2026", "Crypto x AI Agents Hackathon"} == top3
        assert titles.index("Crypto x AI Agents Hackathon") == 2  # taste: you skip crypto
        robo = next(r for r in res if r["event"]["title"] == "Robotics + Agents Hackathon")
        assert robo["signals"]["in_dates"] == 0 and robo["score"] < 50  # December is not next week
        assert "skip crypto" in res[2]["reason"]

        assert ask(c, "something design-related this week")[0]["event"]["title"] == "UX Research Roundtable"
        assert ask(c, "AI events in Palo Alto")[0]["event"]["city"] == "Palo Alto"


def test_unrecorded_prompt_still_works_offline():
    with TestClient(app) as c:
        res = ask(c, "robotics hackathon")
        assert res[0]["event"]["title"] == "Robotics + Agents Hackathon"
        assert all(0 <= r["score"] <= 100 and r["reason"] for r in res)
