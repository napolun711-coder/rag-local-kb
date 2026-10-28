"""Configuration for the stable LightRAG shell."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _int(name: str, default: int, low: int, high: int) -> int:
    try:
        value = int(os.environ.get(name, str(default)))
    except ValueError:
        value = default
    return max(low, min(high, value))


def _float(name: str, default: float, low: float, high: float) -> float:
    try:
        value = float(os.environ.get(name, str(default)))
    except ValueError:
        value = default
    return max(low, min(high, value))


@dataclass(frozen=True)
class ShellSettings:
    host: str
    port: int
    port_scan_limit: int
    backend_url: str
    backend_timeout: float
    log_dir: Path


def get_settings() -> ShellSettings:
    root = Path(__file__).resolve().parents[2]
    return ShellSettings(
        host=os.environ.get("RAGKB_LIGHTRAG_SHELL_HOST", "127.0.0.1").strip(),
        port=_int("RAGKB_LIGHTRAG_SHELL_PORT", 10261, 1, 65535),
        port_scan_limit=_int("RAGKB_LIGHTRAG_SHELL_PORT_SCAN_LIMIT", 20, 0, 500),
        backend_url=os.environ.get("RAGKB_LIGHTRAG_BACKEND_URL", "").strip().rstrip("/"),
        backend_timeout=_float("RAGKB_LIGHTRAG_BACKEND_TIMEOUT", 120.0, 1.0, 1800.0),
        log_dir=Path(os.environ.get("RAGKB_LIGHTRAG_SHELL_LOG_DIR", str(root / "logs"))).resolve(),
    )