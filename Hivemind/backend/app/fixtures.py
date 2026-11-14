import json
import os
from datetime import datetime, timezone
from pathlib import Path

DIR = Path(__file__).parent.parent / "fixtures"

# Secrets live outside the repo (and outside iCloud). Real env vars win over the file.
_ENV = Path(os.getenv("EVENT_AGENT_ENV", Path.home() / ".event-agent" / ".env"))
if _ENV.exists():
    for line in _ENV.read_text().splitlines():
        k, sep, v = line.partition("=")
        if sep and not k.strip().startswith("#") and v.strip():
            os.environ.setdefault(k.strip(), v.strip().strip("'\""))

MODE = os.getenv("DEMO_MODE", "fixtures")
assert MODE in ("fixtures", "live", "record"), f"bad DEMO_MODE={MODE}"
# Fixture data is dated around Sat 14 Nov 2026, so fixtures mode pins the clock there.
NOW = datetime.fromisoformat(os.getenv("DEMO_NOW", "2026-11-14T12:00:00-08:00")) if MODE == "fixtures" else datetime.now(timezone.utc)

_MISSING = object()


def _replay(path: Path, default):
    if path.exists():
        return json.loads(path.read_text())
    if default is _MISSING:
        raise FileNotFoundError(f"no fixture {path.name}; run once with DEMO_MODE=record")
    return default


def _save(path: Path, data):
    if MODE == "record":
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2, default=str))
    return data


def fixture(name, live=None, default=_MISSING):
    """fixtures: replay fixtures/<name>.json (or `default` if never recorded).
    live: call live(). record: call live() and save the result."""
    path = DIR / f"{name}.json"
    if MODE == "fixtures" or live is None:  # ponytail: sources without a live connector yet replay their sample data
        return _replay(path, default)
    return _save(path, live())


async def afixture(name, live, default=_MISSING):
    """Same as fixture() for async live sources (cognee, Superlinked, BAND)."""
    path = DIR / f"{name}.json"
    if MODE == "fixtures":
        return _replay(path, default)
    return _save(path, await live())
