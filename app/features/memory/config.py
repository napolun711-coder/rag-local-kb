"""Independent configuration for the memory subsystem."""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from app.platform.config import ENV_FILE

_TRUE_VALUES = frozenset({"1", "true", "yes", "on"})


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


def _value(name: str, file_values: dict[str, str], default: str) -> str:
    value = os.environ.get(name)
    if value is None:
        value = file_values.get(name, default)
    return str(value).strip()


def _bool(name: str, file_values: dict[str, str], default: bool = False) -> bool:
    raw = _value(name, file_values, "true" if default else "false").lower()
    return raw in _TRUE_VALUES


def _integer(
    name: str,
    file_values: dict[str, str],
    default: int,
    *,
    minimum: int,
    maximum: int,
) -> int:
    try:
        result = int(_value(name, file_values, str(default)))
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if not minimum <= result <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return result


def _choice(
    name: str,
    file_values: dict[str, str],
    default: str,
    allowed: tuple[str, ...],
) -> str:
    result = _value(name, file_values, default).lower()
    return result if result in allowed else default


@dataclass(frozen=True)
class MemorySettings:
    memory_enabled: bool = True
    memory_db_path: str = ""
    memory_context_enabled: bool = True
    memory_fast_memory: bool = True
    memory_context_budget: int = 1200
    memory_recent_history_messages: int = 12
    memory_recent_history_tokens: int = 2000
    memory_upstream_history: str = "on"
    memory_upstream_memory: str = "on"
    chat_topic_filter: bool = False
    memory_recall_enabled: bool = True
    memory_recall_budget: int = 600
    memory_recall_max_items: int = 6
    memory_auto_extract: bool = True
    memory_worker: bool = True
    memory_extract_every_turns: int = 6
    memory_extract_max_messages: int = 20
    memory_extract_max_input_tokens: int = 4000
    memory_extract_backend: str = "off"
    memory_cloud_extract: bool = False
    memory_extract_debounce_seconds: int = 300
    memory_backup_keep: int = 14
    llm_enabled: bool = False
    llm_timeout: int = 300


def _build_memory_settings() -> MemorySettings:
    file_values = _read_env_file(ENV_FILE)
    return MemorySettings(
        memory_enabled=_bool("RAGKB_MEMORY_ENABLED", file_values, True),
        memory_db_path=_value("RAGKB_MEMORY_DB_PATH", file_values, ""),
        memory_context_enabled=_bool("RAGKB_MEMORY_CONTEXT_ENABLED", file_values, True),
        memory_fast_memory=_bool("RAGKB_MEMORY_FAST_MEMORY", file_values, True),
        memory_context_budget=_integer(
            "RAGKB_MEMORY_CONTEXT_BUDGET", file_values, 1200, minimum=100, maximum=8000
        ),
        memory_recent_history_messages=_integer(
            "RAGKB_MEMORY_RECENT_HISTORY_MESSAGES",
            file_values,
            12,
            minimum=0,
            maximum=40,
        ),
        memory_recent_history_tokens=_integer(
            "RAGKB_MEMORY_RECENT_HISTORY_TOKENS",
            file_values,
            2000,
            minimum=0,
            maximum=16000,
        ),
        memory_upstream_history=_choice(
            "RAGKB_MEMORY_UPSTREAM_HISTORY",
            file_values,
            "on",
            ("auto", "on", "off"),
        ),
        memory_upstream_memory=_choice(
            "RAGKB_MEMORY_UPSTREAM_MEMORY",
            file_values,
            "on",
            ("auto", "on", "off"),
        ),
        chat_topic_filter=_bool("RAGKB_CHAT_TOPIC_FILTER", file_values),
        memory_recall_enabled=_bool("RAGKB_MEMORY_RECALL_ENABLED", file_values, True),
        memory_recall_budget=_integer(
            "RAGKB_MEMORY_RECALL_BUDGET", file_values, 600, minimum=80, maximum=2000
        ),
        memory_recall_max_items=_integer(
            "RAGKB_MEMORY_RECALL_MAX_ITEMS", file_values, 6, minimum=1, maximum=20
        ),
        memory_auto_extract=_bool("RAGKB_MEMORY_AUTO_EXTRACT", file_values, True),
        memory_worker=_bool("RAGKB_MEMORY_WORKER", file_values, True),
        memory_extract_every_turns=_integer(
            "RAGKB_MEMORY_EXTRACT_EVERY_TURNS",
            file_values,
            6,
            minimum=1,
            maximum=100,
        ),
        memory_extract_max_messages=_integer(
            "RAGKB_MEMORY_EXTRACT_MAX_MESSAGES",
            file_values,
            20,
            minimum=1,
            maximum=200,
        ),
        memory_extract_max_input_tokens=_integer(
            "RAGKB_MEMORY_EXTRACT_MAX_INPUT_TOKENS",
            file_values,
            4000,
            minimum=200,
            maximum=32000,
        ),
        memory_extract_backend=_choice(
            "RAGKB_MEMORY_EXTRACT_BACKEND",
            file_values,
            "off",
            ("auto", "local", "upstream", "off"),
        ),
        memory_cloud_extract=_bool("RAGKB_MEMORY_CLOUD_EXTRACT", file_values),
        memory_extract_debounce_seconds=_integer(
            "RAGKB_MEMORY_EXTRACT_DEBOUNCE_SECONDS",
            file_values,
            300,
            minimum=0,
            maximum=86400,
        ),
        memory_backup_keep=_integer(
            "RAGKB_MEMORY_BACKUP_KEEP", file_values, 14, minimum=1, maximum=90
        ),
    )


@lru_cache(maxsize=1)
def get_memory_settings() -> MemorySettings:
    return _build_memory_settings()


def reload_memory_settings() -> MemorySettings:
    get_memory_settings.cache_clear()
    return get_memory_settings()


__all__ = [
    "MemorySettings",
    "get_memory_settings",
    "reload_memory_settings",
]