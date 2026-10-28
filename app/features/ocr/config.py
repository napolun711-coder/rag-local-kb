"""Environment-backed configuration for local OCR."""

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


def _bool(name: str, values: dict[str, str], default: bool) -> bool:
    raw = _value(name, values, "true" if default else "false").lower()
    return raw in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class OcrSettings:
    mode: str
    provider: str
    image_ocr: bool
    language: str
    max_file_mb: int
    timeout_seconds: int

    @property
    def enabled(self) -> bool:
        return self.mode in {"manual", "auto"} and self.provider == "local"


def _build_settings() -> OcrSettings:
    values = _read_env(ENV_FILE)
    mode = _value("RAGKB_OCR_MODE", values, "off").lower()
    if mode not in {"off", "manual", "auto"}:
        mode = "off"
    provider = _value("RAGKB_OCR_PROVIDER", values, "local").lower()
    if provider not in {"none", "local"}:
        provider = "none"
    language = _value("RAGKB_OCR_LANG", values, "zh-Hans-CN") or "zh-Hans-CN"
    return OcrSettings(
        mode=mode,
        provider=provider,
        image_ocr=_bool("RAGKB_OCR_IMAGE_OCR", values, True),
        language=language,
        max_file_mb=_int("RAGKB_OCR_MAX_FILE_MB", values, 50, 1, 500),
        timeout_seconds=_int("RAGKB_OCR_TIMEOUT_SECONDS", values, 30, 5, 300),
    )


@lru_cache(maxsize=1)
def get_ocr_settings() -> OcrSettings:
    return _build_settings()


def reload_ocr_settings() -> OcrSettings:
    get_ocr_settings.cache_clear()
    return get_ocr_settings()


__all__ = ["OcrSettings", "get_ocr_settings", "reload_ocr_settings"]
