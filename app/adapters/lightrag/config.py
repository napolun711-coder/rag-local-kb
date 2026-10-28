"""Configuration for the LightRAG HTTP adapter."""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from app.platform.config import ENV_FILE


def _read_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        values[key.strip()] = value
    return values


def _value(name: str, values: dict[str, str], default: str) -> str:
    return str(os.environ.get(name, values.get(name, default))).strip()


def _float(name: str, values: dict[str, str], default: float, low: float, high: float) -> float:
    try:
        result = float(_value(name, values, str(default)))
    except ValueError:
        result = default
    return max(low, min(high, result))


def _bool(name: str, values: dict[str, str], default: bool) -> bool:
    raw = _value(name, values, "true" if default else "false").lower()
    return raw in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class LightRagSettings:
    url: str
    timeout_seconds: float
    connect_timeout_seconds: float
    verify_tls: bool


def _build_settings() -> LightRagSettings:
    values = _read_env(ENV_FILE)
    return LightRagSettings(
        url=_value("RAGKB_LIGHTRAG_URL", values, "http://127.0.0.1:10261").rstrip("/"),
        timeout_seconds=_float("RAGKB_LIGHTRAG_TIMEOUT", values, 120.0, 1.0, 1800.0),
        connect_timeout_seconds=_float(
            "RAGKB_LIGHTRAG_CONNECT_TIMEOUT", values, 5.0, 0.5, 60.0
        ),
        verify_tls=_bool("RAGKB_LIGHTRAG_VERIFY_TLS", values, True),
    )


@lru_cache(maxsize=1)
def get_lightrag_settings() -> LightRagSettings:
    return _build_settings()


def reload_lightrag_settings() -> LightRagSettings:
    get_lightrag_settings.cache_clear()
    return get_lightrag_settings()


__all__ = ["LightRagSettings", "get_lightrag_settings", "reload_lightrag_settings"]