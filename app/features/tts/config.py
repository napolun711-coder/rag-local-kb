"""Environment-backed configuration for TTS."""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlparse

from app.platform.config import ENV_FILE


def _read_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    try:
        content = path.read_text(encoding="utf-8-sig")
    except OSError:
        return values
    for raw in content.splitlines():
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


def _valid_url(value: str) -> bool:
    try:
        parsed = urlparse(value)
    except ValueError:
        return False
    return parsed.scheme in {"http", "https"} and bool(parsed.hostname)


@dataclass(frozen=True)
class TtsSettings:
    cloud_host: str
    cloud_model: str
    cloud_voice: str
    cloud_api_key: str
    cloud_timeout: int
    provider_chain: str

    @property
    def cloud_configured(self) -> bool:
        return bool(
            _valid_url(self.cloud_host)
            and self.cloud_model
            and self.cloud_api_key
        )

    @property
    def cloud_enabled(self) -> bool:
        chain = self.provider_chain.lower()
        return self.cloud_configured and chain in {"cloud", "cloud,browser"}


def _build_settings() -> TtsSettings:
    values = _read_env(ENV_FILE)
    chain = _value("RAGKB_TTS_PROVIDER_CHAIN", values, "cloud").lower()
    if chain not in {"cloud", "cloud,browser", "browser"}:
        chain = "cloud"
    return TtsSettings(
        cloud_host=_value("RAGKB_TTS_CLOUD_HOST", values, "").rstrip("/"),
        cloud_model=_value("RAGKB_TTS_CLOUD_MODEL", values, ""),
        cloud_voice=_value("RAGKB_TTS_CLOUD_VOICE", values, ""),
        cloud_api_key=_value("RAGKB_TTS_CLOUD_API_KEY", values, ""),
        cloud_timeout=_int("RAGKB_TTS_CLOUD_TIMEOUT", values, 60, 3, 600),
        provider_chain=chain,
    )


@lru_cache(maxsize=1)
def get_tts_settings() -> TtsSettings:
    return _build_settings()


def reload_tts_settings() -> TtsSettings:
    get_tts_settings.cache_clear()
    return get_tts_settings()


__all__ = ["TtsSettings", "get_tts_settings", "reload_tts_settings"]
