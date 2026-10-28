"""Normalize LightRAG NDJSON into the existing frontend chat events."""

from __future__ import annotations

import json
import time
from typing import Any, Dict, Iterable, Iterator, List, Optional


def _json_lines(chunks: Iterable[bytes]) -> Iterator[Dict[str, Any]]:
    buffer = ""
    for chunk in chunks:
        if not chunk:
            continue
        if isinstance(chunk, bytes):
            buffer += chunk.decode("utf-8", errors="replace")
        else:
            buffer += str(chunk)
        while "\n" in buffer:
            line, buffer = buffer.split("\n", 1)
            line = line.strip()
            if not line:
                continue
            try:
                value = json.loads(line)
            except Exception:
                continue
            if isinstance(value, dict):
                yield value
    line = buffer.strip()
    if line:
        try:
            value = json.loads(line)
        except Exception:
            value = None
        if isinstance(value, dict):
            yield value


def frontend_events(chunks: Iterable[bytes], *, started_at: Optional[float] = None) -> Iterator[Dict[str, Any]]:
    started = started_at if started_at is not None else time.perf_counter()
    references: List[Dict[str, Any]] = []
    saw_done = False
    for item in _json_lines(chunks):
        if "references" in item:
            value = item.get("references")
            if isinstance(value, list):
                references = [ref for ref in value if isinstance(ref, dict)]
                yield {"type": "references", "items": references}
        response = item.get("response")
        if isinstance(response, str) and response:
            yield {"type": "chunk", "text": response}
        error = item.get("error")
        if error:
            yield {"type": "error", "message": str(error)}
        response_time = item.get("response_time")
        if isinstance(response_time, (int, float)):
            saw_done = True
            yield {"type": "done", "response_time": float(response_time)}
    if not saw_done:
        yield {"type": "done", "response_time": round(time.perf_counter() - started, 3)}
    yield {"type": "end"}


__all__ = ["frontend_events"]