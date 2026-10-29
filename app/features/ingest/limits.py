"""In-memory per-device upload quota for paired LAN clients."""

from __future__ import annotations

import math
import threading
import time
from collections import deque
from typing import Deque, Dict, Optional, Tuple

_LOCK = threading.Lock()
_EVENTS: Dict[str, Deque[Tuple[float, int]]] = {}


def _prune(events: Deque[Tuple[float, int]], now: float, window: float) -> None:
    cutoff = now - max(1.0, window)
    while events and events[0][0] < cutoff:
        events.popleft()


def reserve(
    key: str,
    size: int,
    *,
    max_files: int,
    max_bytes: int,
    window_seconds: int,
) -> Tuple[bool, int]:
    """Atomically reserve one upload slot for a device.

    Returns ``(allowed, retry_after_seconds)``. A rejected request does not
    consume quota. Quota state is process-local and intentionally conservative;
    local loopback uploads bypass this limiter.
    """
    now = time.monotonic()
    window = max(1, int(window_seconds))
    amount = max(0, int(size))
    with _LOCK:
        events = _EVENTS.setdefault(key, deque())
        _prune(events, now, window)
        used_bytes = sum(item[1] for item in events)
        if len(events) >= max(1, int(max_files)) or used_bytes + amount > max(1, int(max_bytes)):
            if events:
                retry = max(1, int(math.ceil(window - (now - events[0][0]))))
            else:
                retry = max(1, window)
            return False, retry
        events.append((now, amount))
        return True, 0


def reset(key: Optional[str] = None) -> None:
    with _LOCK:
        if key is None:
            _EVENTS.clear()
        else:
            _EVENTS.pop(key, None)


__all__ = ["reserve", "reset"]
