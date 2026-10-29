"""Persistent LAN device authorization and short-lived pairing requests."""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import os
import secrets
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import Request

from app.platform.config import get_settings

COOKIE_NAME = "ragkb_access"
CSRF_COOKIE_NAME = "ragkb_csrf"
CSRF_HEADER_NAME = "X-RAGKB-CSRF"
COOKIE_MAX_AGE = 90 * 24 * 60 * 60
REQUEST_TTL_SECONDS = 5 * 60
MAX_PENDING_TOTAL = 64
MAX_PENDING_PER_IP = 5
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})

_LOCK = threading.RLock()
_PENDING: Dict[str, Dict[str, Any]] = {}
_DEVICES: Optional[Dict[str, Dict[str, Any]]] = None


class AccessError(Exception):
    pass


class RequestLimitError(AccessError):
    pass


class RequestGone(AccessError):
    pass


class InvalidPairingSecret(AccessError):
    pass


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize_host(value: str) -> str:
    host = str(value or "").strip().lower()
    if host.startswith("::ffff:"):
        host = host[7:]
    return host


def is_loopback_host(value: str) -> bool:
    host = _normalize_host(value)
    if host in {"localhost", "0.0.0.0"}:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def is_local_request(request: Request) -> bool:
    host = request.client.host if request.client else ""
    return is_loopback_host(host)


def _client_ip(request: Request) -> str:
    return _normalize_host(request.client.host if request.client else "") or "unknown"


def _device_path() -> Path:
    return get_settings().data_dir / "access" / "devices.json"


def _hash_secret(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _sanitize_device_name(value: Any) -> str:
    name = " ".join(str(value or "").split())
    return (name or "局域网设备")[:80]


def _load_devices_locked() -> Dict[str, Dict[str, Any]]:
    global _DEVICES
    if _DEVICES is not None:
        return _DEVICES
    path = _device_path()
    if not path.is_file():
        _DEVICES = {}
        return _DEVICES
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        _DEVICES = {}
        return _DEVICES
    devices = payload.get("devices") if isinstance(payload, dict) else None
    _DEVICES = devices if isinstance(devices, dict) else {}
    return _DEVICES


def _save_devices_locked() -> None:
    path = _device_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"version": 1, "devices": _load_devices_locked()}
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _prune_requests_locked() -> None:
    now = time.time()
    expired = [
        request_id
        for request_id, item in _PENDING.items()
        if float(item.get("expires_at") or 0) <= now
    ]
    for request_id in expired:
        _PENDING.pop(request_id, None)
    if len(_PENDING) > MAX_PENDING_TOTAL:
        for request_id, _ in sorted(
            _PENDING.items(), key=lambda pair: float(pair[1].get("created_ts") or 0)
        )[: len(_PENDING) - MAX_PENDING_TOTAL]:
            _PENDING.pop(request_id, None)


def create_request(name: Any, request_ip: str) -> Dict[str, Any]:
    with _LOCK:
        _prune_requests_locked()
        same_ip = [
            item
            for item in _PENDING.values()
            if item.get("client_ip") == request_ip and item.get("status") == "pending"
        ]
        if len(same_ip) >= MAX_PENDING_PER_IP:
            raise RequestLimitError("Too many pending requests from this device")
        request_id = secrets.token_hex(12)
        request_secret = secrets.token_urlsafe(32)
        now = time.time()
        item = {
            "id": request_id,
            "name": _sanitize_device_name(name),
            "client_ip": request_ip,
            "created_at": _utc_now(),
            "created_ts": now,
            "expires_at": now + REQUEST_TTL_SECONDS,
            "secret_hash": _hash_secret(request_secret),
            "status": "pending",
        }
        _PENDING[request_id] = item
        return {
            "request_id": request_id,
            "request_secret": request_secret,
            "name": item["name"],
            "expires_in": REQUEST_TTL_SECONDS,
        }


def list_requests() -> list[Dict[str, Any]]:
    with _LOCK:
        _prune_requests_locked()
        return [
            {
                "id": item["id"],
                "name": item["name"],
                "created_at": item["created_at"],
                "client_ip": item["client_ip"],
                "status": item["status"],
            }
            for item in sorted(
                _PENDING.values(), key=lambda item: float(item.get("created_ts") or 0)
            )
        ]


def approve_request(request_id: str) -> Dict[str, Any]:
    with _LOCK:
        _prune_requests_locked()
        item = _PENDING.get(request_id)
        if not item:
            raise RequestGone("Pairing request expired")
        item["status"] = "approved"
        item["approved_at"] = _utc_now()
        return {"id": item["id"], "status": "approved", "name": item["name"]}


def ignore_request(request_id: str) -> bool:
    with _LOCK:
        return _PENDING.pop(request_id, None) is not None


def claim_request(request_id: str, secret: str, request_ip: str) -> Optional[Dict[str, Any]]:
    with _LOCK:
        _prune_requests_locked()
        item = _PENDING.get(request_id)
        if not item:
            raise RequestGone("Pairing request expired")
        if not hmac.compare_digest(
            str(item.get("secret_hash") or ""), _hash_secret(str(secret or ""))
        ):
            raise InvalidPairingSecret("Invalid pairing secret")
        if item.get("status") != "approved":
            return None
        device_id = secrets.token_hex(12)
        device_token = secrets.token_urlsafe(32)
        devices = _load_devices_locked()
        devices[device_id] = {
            "id": device_id,
            "name": item["name"],
            "token_hash": _hash_secret(device_token),
            "created_at": _utc_now(),
            "last_seen_at": _utc_now(),
            "last_seen_ts": time.time(),
            "last_ip": request_ip,
        }
        _save_devices_locked()
        _PENDING.pop(request_id, None)
        return {
            "device_id": device_id,
            "device_token": device_token,
            "name": item["name"],
        }


def cookie_value(device_id: str, device_token: str) -> str:
    return f"{device_id}.{device_token}"


def _device_from_request_locked(request: Request) -> Optional[Dict[str, Any]]:
    raw = str(request.cookies.get(COOKIE_NAME) or "")
    if "." not in raw:
        return None
    device_id, token = raw.split(".", 1)
    item = _load_devices_locked().get(device_id)
    if not item:
        return None
    if not hmac.compare_digest(
        str(item.get("token_hash") or ""), _hash_secret(token)
    ):
        return None
    now = time.time()
    if now - float(item.get("last_seen_ts") or 0) >= 60:
        item["last_seen_ts"] = now
        item["last_seen_at"] = _utc_now()
        item["last_ip"] = _client_ip(request)
        _save_devices_locked()
    return item


def is_request_authorized(request: Request) -> bool:
    if is_local_request(request):
        return True
    with _LOCK:
        return _device_from_request_locked(request) is not None


def request_quota_key(request: Request) -> str:
    """Stable quota key for a paired device, falling back to the client IP."""
    raw = str(request.cookies.get(COOKIE_NAME) or "")
    if "." in raw:
        return "device:" + raw.split(".", 1)[0]
    return "ip:" + _client_ip(request)


def new_csrf_token() -> str:
    return secrets.token_urlsafe(32)


def set_csrf_cookie(response: Any, token: str = "") -> None:
    response.set_cookie(
        CSRF_COOKIE_NAME,
        token or new_csrf_token(),
        max_age=COOKIE_MAX_AGE,
        httponly=False,
        samesite="lax",
        path="/",
    )


def ensure_csrf_cookie(response: Any, request: Request) -> None:
    """Give an authorized LAN browser a readable double-submit token."""
    if is_local_request(request):
        return
    if request.cookies.get(CSRF_COOKIE_NAME):
        return
    set_csrf_cookie(response)


def csrf_valid(request: Request) -> bool:
    if is_local_request(request) or request.method in SAFE_METHODS:
        return True
    cookie = str(request.cookies.get(CSRF_COOKIE_NAME) or "")
    header = str(request.headers.get(CSRF_HEADER_NAME) or "")
    return bool(cookie and header and hmac.compare_digest(cookie, header))


def status_payload(request: Request) -> Dict[str, Any]:
    local = is_local_request(request)
    authorized = True if local else is_request_authorized(request)
    return {
        "enabled": True,
        "local": local,
        "authorized": authorized,
        "pair_url": "/pair",
    }


def list_devices() -> list[Dict[str, Any]]:
    with _LOCK:
        return [
            {
                "id": item.get("id", ""),
                "name": item.get("name", ""),
                "created_at": item.get("created_at", ""),
                "last_seen_at": item.get("last_seen_at", ""),
                "last_ip": item.get("last_ip", ""),
            }
            for item in sorted(
                _load_devices_locked().values(),
                key=lambda item: str(item.get("created_at") or ""),
                reverse=True,
            )
        ]


def revoke_device(device_id: str) -> bool:
    with _LOCK:
        devices = _load_devices_locked()
        if device_id not in devices:
            return False
        devices.pop(device_id, None)
        _save_devices_locked()
        return True
