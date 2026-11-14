"""Superlinked Inference Engine (SIE Serverless) client: reranking and intent parsing.

Only called in live/record mode; fixtures mode replays what record mode saved.
"""
import json
import os
from datetime import datetime

import httpx

URL = os.getenv("SUPERLINKED_URL", "https://api.superlinked.com")
RERANK_MODEL = os.getenv("SUPERLINKED_RERANK_MODEL", "Qwen/Qwen3-Reranker-4B")
RERANK_INSTRUCTION = os.getenv(
    "SUPERLINKED_RERANK_INSTRUCTION",
    "Given an event search, judge whether the event matches what the person is looking for",
)
LLM_MODEL = os.getenv("SUPERLINKED_LLM_MODEL", "Qwen/Qwen3.8-27B-FP8")
TIMEOUT = float(os.getenv("SUPERLINKED_TIMEOUT_S", "120"))

INTENT_SCHEMA = {
    "type": "object",
    "properties": {
        "topic": {"type": "string", "description": "what kind of event, without dates or places"},
        "date_from": {"type": ["string", "null"], "description": "YYYY-MM-DD, or null if no dates given"},
        "date_to": {"type": ["string", "null"], "description": "YYYY-MM-DD inclusive, or null"},
        "city": {"type": ["string", "null"], "description": "city name, or null if none given"},
    },
    "required": ["topic", "date_from", "date_to", "city"],
}


def _client() -> httpx.AsyncClient:
    key = os.getenv("SUPERLINKED_API_KEY")
    if not key:
        raise RuntimeError("SUPERLINKED_API_KEY missing; add it to ~/.event-agent/.env")
    return httpx.AsyncClient(base_url=URL, timeout=TIMEOUT, headers={"Authorization": f"Bearer {key}"})


async def rerank(query: str, docs: list[str]) -> list[float]:
    """Relevance 0..1 for each doc, in the same order as docs."""
    async with _client() as c:
        r = await c.post(f"/v1/score/{RERANK_MODEL}", json={"query": {"text": query}, "items": [{"text": d} for d in docs],
                                                                "instruction": RERANK_INSTRUCTION})
        r.raise_for_status()
    by_id = {s["item_id"]: s["score"] for s in r.json()["scores"]}
    return [by_id[f"item-{i}"] for i in range(len(docs))]


async def intent(prompt: str, now: datetime) -> dict:
    """'agent hackathons next week' -> {topic, date_from, date_to, city}."""
    ask = (f"Today is {now:%A %Y-%m-%d}. Extract the event search intent from: {prompt!r}. "
           "If the ask names no time, date_from and date_to must be null. "
           "Dates are never before today: 'this week' or 'this weekend' means the coming days.")
    async with _client() as c:
        r = await c.post("/v1/chat/completions", json={
            "model": LLM_MODEL, "temperature": 0, "max_tokens": 200,
            "messages": [{"role": "user", "content": ask}],
            "response_format": {"type": "json_schema", "json_schema": {"name": "intent", "schema": INTENT_SCHEMA}},
        })
        r.raise_for_status()
    return json.loads(r.json()["choices"][0]["message"]["content"])
