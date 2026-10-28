"""Local/LAN web-session state and delayed lifecycle shutdown."""

from __future__ import annotations

import hmac
import json
import os
import secrets
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict

from fastapi import Request

from app.features.access.service import is_local_request
from app.platform.config import get_settings

ROOT = Path(__file__).resolve().parents[3]
_TOKEN = secrets.token_urlsafe(32)
_STOP_LOCK = threading.RLock()
_STOP_TIMER: threading.Timer | None = None
_STOP_AT: float | None = None
_STOP_REASON = ""


def token_matches(value: Any) -> bool:
    return hmac.compare_digest(_TOKEN, str(value or ""))


def _lifecycle_state_path() -> Path:
    return get_settings().log_dir / "runtime" / "services.json"


def _lifecycle_managed() -> bool:
    path = _lifecycle_state_path()
    if not path.is_file():
        return False
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        app = (payload.get("services") or {}).get("app") or {}
        return int(app.get("pid") or 0) == os.getpid()
    except (OSError, TypeError, ValueError):
        return False


def _stop_enabled() -> bool:
    raw = os.environ.get("RAGKB_WEB_STOP_ENABLED", "1").strip().lower()
    return raw not in {"0", "false", "off", "no"} and _lifecycle_managed()


def _pending_seconds() -> int | None:
    with _STOP_LOCK:
        if _STOP_AT is None:
            return None
        return max(0, int(round(_STOP_AT - time.monotonic())))


def _cancel_stop_locked() -> None:
    global _STOP_TIMER, _STOP_AT, _STOP_REASON
    if _STOP_TIMER:
        _STOP_TIMER.cancel()
    _STOP_TIMER = None
    _STOP_AT = None
    _STOP_REASON = ""


def cancel_stop() -> None:
    with _STOP_LOCK:
        _cancel_stop_locked()


def _launch_lifecycle_stop() -> None:
    creationflags = 0
    if os.name == "nt":
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
    subprocess.Popen(
        [sys.executable, str(ROOT / "scripts" / "lifecycle.py"), "stop"],
        cwd=str(ROOT),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=creationflags,
    )


def schedule_stop(seconds: int, reason: str) -> Dict[str, Any]:
    global _STOP_TIMER, _STOP_AT, _STOP_REASON
    if not _stop_enabled():
        raise RuntimeError("Web stop is unavailable for this process")
    delay = max(1, int(seconds))
    with _STOP_LOCK:
        _cancel_stop_locked()
        _STOP_AT = time.monotonic() + delay
        _STOP_REASON = str(reason or "")

        def fire() -> None:
            _launch_lifecycle_stop()

        _STOP_TIMER = threading.Timer(delay, fire)
        _STOP_TIMER.daemon = True
        _STOP_TIMER.start()
    return {"ok": True, "pending_seconds": delay}


def session_payload(request: Request) -> Dict[str, Any]:
    local = is_local_request(request)
    enabled = bool(local and _stop_enabled())
    return {
        "mode": "local" if local else "lan",
        "enabled": enabled,
        "can_stop": enabled,
        "keepalive": True,
        "delay_seconds": 25,
        "lan_delay_seconds": 180,
        "busy_delay_seconds": 300,
        "busy": False,
        "pending_seconds": _pending_seconds(),
        "stop_reason": _STOP_REASON if not local else _STOP_REASON,
    }


def keepalive(request: Request) -> Dict[str, Any]:
    local = is_local_request(request)
    with _STOP_LOCK:
        pending = _pending_seconds()
        if local and pending is not None:
            cancel_stop()
        elif not local and pending is not None:
            schedule_stop(180, "lan-keepalive")
    return session_payload(request)


def local_control(request: Request) -> Dict[str, Any]:
    if not is_local_request(request):
        raise PermissionError("Control token is local-only")
    return {"token": _TOKEN, "pending_seconds": _pending_seconds()}


def leave(request: Request, token: Any) -> Dict[str, Any]:
    if not is_local_request(request):
        raise PermissionError("Leave scheduling is local-only")
    if not token_matches(token):
        raise PermissionError("Invalid control token")
    if not _stop_enabled():
        return {"ok": True, "enabled": False, "pending_seconds": None}
    return schedule_stop(25, "tab-close")


def stop_now(request: Request, token: Any, delay: Any = 3) -> Dict[str, Any]:
    if not is_local_request(request):
        raise PermissionError("Stop is local-only")
    if not token_matches(token):
        raise PermissionError("Invalid control token")
    try:
        seconds = max(1, min(30, int(delay)))
    except (TypeError, ValueError):
        seconds = 3
    return schedule_stop(seconds, "web-stop")
