"""cognee glue. Imported lazily: fixtures mode never loads cognee (it's slow to import and needs a model).

cognee's LLM and embeddings both run on Superlinked (SIE Serverless, OpenAI-compatible API).
Any cognee env var you set yourself (LLM_*, EMBEDDING_*) wins over these defaults.
"""
import os
from pathlib import Path

DATASET = os.getenv("COGNEE_DATASET", "event_agent")
# outside the repo on purpose: ~/Desktop may sync to iCloud, and your memory graph should stay on this machine
ROOT = Path(os.getenv("COGNEE_DIR", Path.home() / ".event-agent" / "cognee"))

SL_URL = os.getenv("SUPERLINKED_URL", "https://api.superlinked.com") + "/v1"
SL_LLM = os.getenv("SUPERLINKED_LLM_MODEL", "Qwen/Qwen3.8-27B-FP8")
SL_EMBED = os.getenv("SUPERLINKED_EMBED_MODEL", "Qwen/Qwen3-Embedding-4B")
SL_EMBED_DIMS = os.getenv("SUPERLINKED_EMBED_DIMS", "2560")  # must match SL_EMBED's output size

_ready = False


def _use_superlinked():
    key = os.getenv("SUPERLINKED_API_KEY")
    if not key:
        raise RuntimeError("SUPERLINKED_API_KEY missing; add it to ~/.event-agent/.env")
    for k, v in {
        "LLM_PROVIDER": "custom", "LLM_ENDPOINT": SL_URL, "LLM_MODEL": f"openai/{SL_LLM}", "LLM_API_KEY": key,
        "EMBEDDING_PROVIDER": "openai_compatible", "EMBEDDING_ENDPOINT": SL_URL, "EMBEDDING_MODEL": SL_EMBED,
        "EMBEDDING_DIMENSIONS": SL_EMBED_DIMS, "EMBEDDING_API_KEY": key,
    }.items():
        os.environ.setdefault(k, v)


def _cognee():
    global _ready
    if not _ready:
        _use_superlinked()  # before import: cognee reads its config from env on load
    import cognee

    if not _ready:  # keep cognee stores in ROOT, not in site-packages
        cognee.config.data_root_directory(str(ROOT / "data"))
        cognee.config.system_root_directory(str(ROOT / "system"))
        _ready = True
    return cognee


async def remember(docs: list[str]) -> dict:
    await _cognee().remember(docs, dataset_name=DATASET)
    return {"stored": len(docs), "dataset": DATASET}


async def recall(question: str) -> list[str]:
    res = await _cognee().recall(question, datasets=[DATASET])
    return [t for r in res if (t := getattr(r, "text", None) or getattr(r, "answer", None) or getattr(r, "content", None))]
