"""Environment-backed configuration for the inbox scanner."""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Tuple

from app.features.ingest.config import get_ingest_settings
from app.platform.config import ENV_FILE, get_settings
from app.platform.paths import BASE_DIR


def _read_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    try:
        content = path.read_text(encoding="utf-8-sig")
    except OSError:
        return values
    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        values[key.strip()] = value
    return values


def _value(name: str, values: dict[str, str], default: str) -> str:
    raw = os.environ.get(name)
    if raw is None:
        raw = values.get(name, default)
    return str(raw).strip()


def _bool(name: str, values: dict[str, str], default: bool) -> bool:
    raw = _value(name, values, "true" if default else "false").lower()
    return raw in {"1", "true", "yes", "on"}


def _int(
    name: str,
    values: dict[str, str],
    default: int,
    low: int,
    high: int,
) -> int:
    try:
        value = int(_value(name, values, str(default)))
    except ValueError:
        value = default
    return max(low, min(high, value))


def _path(name: str, values: dict[str, str], default: Path) -> Path:
    raw = _value(name, values, "")
    path = Path(raw).expanduser() if raw else default
    if not path.is_absolute():
        path = BASE_DIR / path
    return path.resolve(strict=False)


@dataclass(frozen=True)
class InboxSettings:
    enabled: bool
    directory: Path
    state_path: Path
    poll_seconds: int
    settle_seconds: int
    allowed_extensions: Tuple[str, ...]
    max_file_size: int


def _build_settings() -> InboxSettings:
    values = _read_env(ENV_FILE)
    app = get_settings()
    ingest = get_ingest_settings()
    return InboxSettings(
        enabled=_bool("RAGKB_INBOX_ENABLED", values, False),
        directory=_path("RAGKB_INBOX_DIR", values, app.data_dir / "inbox"),
        state_path=app.data_dir / "inbox-state.json",
        poll_seconds=_int("RAGKB_INBOX_POLL_SECONDS", values, 10, 3, 600),
        settle_seconds=_int("RAGKB_INBOX_SETTLE_SECONDS", values, 5, 0, 300),
        allowed_extensions=tuple(ingest.allowed_extensions),
        max_file_size=ingest.max_upload_size,
    )


@lru_cache(maxsize=1)
def get_inbox_settings() -> InboxSettings:
    return _build_settings()


def reload_inbox_settings() -> InboxSettings:
    get_inbox_settings.cache_clear()
    return get_inbox_settings()


__all__ = ["InboxSettings", "get_inbox_settings", "reload_inbox_settings"]
