"""Environment-backed application settings."""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from app.platform.paths import BASE_DIR, DATA_DIR, KNOWLEDGE_DIR, LOG_DIR, STATIC_DIR


def _env_file() -> Path:
    override = os.environ.get("RAGKB_ENV_FILE", "").strip()
    if not override:
        return BASE_DIR / ".env"
    path = Path(override).expanduser()
    if not path.is_absolute():
        path = BASE_DIR / path
    return path.resolve()


ENV_FILE = _env_file()
_VALID_LOG_LEVELS = frozenset({"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"})


def _read_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        values[key.strip()] = value
    return values


def _env(name: str, file_values: dict[str, str], default: str) -> str:
    value = os.environ.get(name)
    if value is None:
        value = file_values.get(name, default)
    return str(value).strip()


def _port(value: str, name: str) -> int:
    try:
        port = int(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if not 1 <= port <= 65535:
        raise ValueError(f"{name} must be between 1 and 65535")
    return port


def _port_list(value: str, name: str) -> tuple[int, ...]:
    ports: list[int] = []
    for item in value.split(","):
        text = item.strip()
        if not text:
            continue
        port = _port(text, name)
        if port not in ports:
            ports.append(port)
    return tuple(ports)


def _non_negative_int(value: str, name: str, default: int) -> int:
    try:
        result = int(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if result < 0:
        raise ValueError(f"{name} must not be negative")
    return result


def _path(value: str, default: Path) -> Path:
    text = value.strip()
    if not text:
        return default
    path = Path(text).expanduser()
    if not path.is_absolute():
        path = BASE_DIR / path
    return path.resolve()


@dataclass(frozen=True)
class Settings:
    app_name: str
    host: str
    port: int
    port_candidates: tuple[int, ...]
    port_scan_limit: int
    log_level: str
    static_dir: Path
    data_dir: Path
    knowledge_dir: Path
    log_dir: Path

    def to_log_dict(self) -> dict[str, object]:
        return {
            "host": self.host,
            "preferred_port": self.port,
            "port_candidates": list(self.port_candidates),
            "port_scan_limit": self.port_scan_limit,
            "log_level": self.log_level,
            "static_dir": str(self.static_dir),
            "data_dir": str(self.data_dir),
            "knowledge_dir": str(self.knowledge_dir),
            "log_dir": str(self.log_dir),
        }


def _build_settings() -> Settings:
    file_values = _read_env_file(ENV_FILE)
    level = _env("RAGKB_LOG_LEVEL", file_values, "INFO").upper()
    if level not in _VALID_LOG_LEVELS:
        level = "INFO"
    return Settings(
        app_name=_env("RAGKB_APP_NAME", file_values, "Local RAG Knowledge Base"),
        host=_env("RAGKB_HOST", file_values, "127.0.0.1"),
        port=_port(_env("RAGKB_PORT", file_values, "18080"), "RAGKB_PORT"),
        port_candidates=_port_list(
            _env("RAGKB_PORT_CANDIDATES", file_values, ""),
            "RAGKB_PORT_CANDIDATES",
        ),
        port_scan_limit=_non_negative_int(
            _env("RAGKB_PORT_SCAN_LIMIT", file_values, "50"),
            "RAGKB_PORT_SCAN_LIMIT",
            50,
        ),
        log_level=level,
        static_dir=_path(_env("RAGKB_STATIC_DIR", file_values, str(STATIC_DIR)), STATIC_DIR),
        data_dir=_path(_env("RAGKB_DATA_DIR", file_values, str(DATA_DIR)), DATA_DIR),
        knowledge_dir=_path(
            _env("RAGKB_KNOWLEDGE_DIR", file_values, str(KNOWLEDGE_DIR)),
            KNOWLEDGE_DIR,
        ),
        log_dir=_path(_env("RAGKB_LOG_DIR", file_values, str(LOG_DIR)), LOG_DIR),
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return _build_settings()


def reload_settings() -> Settings:
    get_settings.cache_clear()
    return get_settings()
