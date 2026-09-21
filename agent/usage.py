"""Requests per day per model against the Gemini free tier (default limit 500 per project and model, reset at midnight Pacific).
Every model call attempt, including a retried 429, counts. The counts and the models found unusable today live in a small file so
they survive restarts."""
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

FILE = Path(__file__).parent / "eval" / "results" / ".usage.json"
LIMIT = 500                                  # per model; the owner's second model may differ, a real 429 marks it unusable anyway


def _today() -> str:
    return datetime.now(ZoneInfo("America/Los_Angeles")).strftime("%Y-%m-%d")


def _load() -> dict:
    if FILE.exists():
        d = json.loads(FILE.read_text())
        if d.get("date") == _today():
            return {"date": d["date"], "counts": d.get("counts", {}), "unavailable": d.get("unavailable", {})}
    return {"date": _today(), "counts": {}, "unavailable": {}}


def _save(d: dict) -> None:
    FILE.parent.mkdir(exist_ok=True)
    FILE.write_text(json.dumps(d))


def used_today(model: str | None = None) -> int:
    counts = _load()["counts"]
    return counts.get(model, 0) if model else sum(counts.values())


def record_call(model: str) -> None:
    d = _load()
    d["counts"][model] = d["counts"].get(model, 0) + 1
    _save(d)


def mark_unavailable(model: str, reason: str) -> None:
    d = _load()
    d["unavailable"][model] = reason
    _save(d)


def available(model: str) -> bool:
    d = _load()
    return model not in d["unavailable"] and d["counts"].get(model, 0) < LIMIT


def capacity_left(models: list[str]) -> int:
    d = _load()
    return sum(max(0, LIMIT - d["counts"].get(m, 0)) for m in models if m not in d["unavailable"])
