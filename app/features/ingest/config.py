"""Independent configuration for document ingestion."""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Tuple

from app.platform.config import ENV_FILE
from app.platform.paths import DATA_DIR

DEFAULT_ALLOWED_EXTENSIONS = (
    ".txt", ".md", ".pdf", ".docx", ".csv", ".xlsx", ".pptx",
    ".json", ".html", ".htm", ".log", ".xml", ".yaml", ".yml",
    ".ini", ".cfg", ".toml", ".jpg", ".jpeg", ".png", ".webp",
    ".bmp",
)


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


def _int(name: str, file_values: dict[str, str], default: int, low: int, high: int) -> int:
    try:
        result = int(_value(name, file_values, str(default)))
    except ValueError:
        result = default
    return max(low, min(high, result))


def _float(name: str, file_values: dict[str, str], default: float, low: float, high: float) -> float:
    try:
        result = float(_value(name, file_values, str(default)))
    except ValueError:
        result = default
    return max(low, min(high, result))


def _extensions(name: str, file_values: dict[str, str], default: Tuple[str, ...]) -> Tuple[str, ...]:
    raw = _value(name, file_values, "")
    if not raw:
        return default
    result = []
    for item in raw.split(","):
        text = item.strip().lower()
        if not text:
            continue
        if not text.startswith("."):
            text = "." + text
        if text not in result:
            result.append(text)
    return tuple(result) or default


def _routes(name: str, file_values: dict[str, str], default: Tuple[str, ...]) -> Tuple[str, ...]:
    raw = _value(name, file_values, "")
    if not raw:
        return default
    result = []
    for item in raw.split(","):
        text = item.strip().lower()
        if text and text not in result:
            result.append(text)
    return tuple(result) or default


def _path(name: str, file_values: dict[str, str], default: Path) -> Path:
    raw = _value(name, file_values, str(default))
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = DATA_DIR.parent / path
    return path.resolve()


@dataclass(frozen=True)
class IngestSettings:
    allowed_extensions: Tuple[str, ...]
    max_upload_size: int
    lan_upload_max_files: int
    lan_upload_max_bytes: int
    lan_upload_window_seconds: int
    parse_max_chars: int
    parse_max_assets: int
    parse_max_asset_mb: int
    upload_dir: Path
    parsed_dir: Path
    quality_profile: str
    quality_min_chars: int
    quality_min_readable_ratio: float
    quality_max_garbled_ratio: float
    quality_max_repeat_ratio: float
    quality_min_chars_per_page: int
    quality_pass_score: int
    quality_max_unocr_images: int
    quality_ocr_routes: Tuple[str, ...]


def _build_settings() -> IngestSettings:
    values = _read_env_file(ENV_FILE)
    return IngestSettings(
        allowed_extensions=_extensions(
            "RAGKB_INGEST_ALLOWED_EXTENSIONS", values, DEFAULT_ALLOWED_EXTENSIONS
        ),
        max_upload_size=_int(
            "RAGKB_INGEST_MAX_UPLOAD_SIZE",
            values,
            100 * 1024 * 1024,
            1,
            2 * 1024 * 1024 * 1024,
        ),
        lan_upload_max_files=_int(
            "RAGKB_LAN_UPLOAD_MAX_FILES",
            values,
            30,
            1,
            1000,
        ),
        lan_upload_max_bytes=_int(
            "RAGKB_LAN_UPLOAD_MAX_BYTES",
            values,
            500 * 1024 * 1024,
            1,
            10 * 1024 * 1024 * 1024,
        ),
        lan_upload_window_seconds=_int(
            "RAGKB_LAN_UPLOAD_WINDOW_SECONDS",
            values,
            600,
            10,
            86400,
        ),
        parse_max_chars=_int(
            "RAGKB_INGEST_PARSE_MAX_CHARS", values, 2_000_000, 1000, 20_000_000
        ),
        parse_max_assets=_int("RAGKB_INGEST_PARSE_MAX_ASSETS", values, 50, 0, 500),
        parse_max_asset_mb=_int("RAGKB_INGEST_PARSE_MAX_ASSET_MB", values, 10, 0, 200),
        upload_dir=_path("RAGKB_INGEST_UPLOAD_DIR", values, DATA_DIR / "uploads"),
        parsed_dir=_path("RAGKB_INGEST_PARSED_DIR", values, DATA_DIR / "parsed"),
        quality_profile=_value(
            "RAGKB_INGEST_QUALITY_PROFILE", values, "standard"
        ).lower(),
        quality_min_chars=_int(
            "RAGKB_INGEST_QUALITY_MIN_CHARS", values, 50, 0, 1_000_000
        ),
        quality_min_readable_ratio=_float(
            "RAGKB_INGEST_QUALITY_MIN_READABLE_RATIO", values, 0.60, 0.0, 1.0
        ),
        quality_max_garbled_ratio=_float(
            "RAGKB_INGEST_QUALITY_MAX_GARBLED_RATIO", values, 0.05, 0.0, 1.0
        ),
        quality_max_repeat_ratio=_float(
            "RAGKB_INGEST_QUALITY_MAX_REPEAT_RATIO", values, 0.40, 0.0, 1.0
        ),
        quality_min_chars_per_page=_int(
            "RAGKB_INGEST_QUALITY_MIN_CHARS_PER_PAGE", values, 100, 0, 1_000_000
        ),
        quality_pass_score=_int(
            "RAGKB_INGEST_QUALITY_PASS_SCORE", values, 70, 0, 100
        ),
        quality_max_unocr_images=_int(
            "RAGKB_INGEST_QUALITY_MAX_UNOCR_IMAGES", values, 0, 0, 1000
        ),
        quality_ocr_routes=_routes(
            "RAGKB_INGEST_QUALITY_OCR_ROUTES", values, ("pdf", "image")
        ),
    )


@lru_cache(maxsize=1)
def get_ingest_settings() -> IngestSettings:
    return _build_settings()


def reload_ingest_settings() -> IngestSettings:
    get_ingest_settings.cache_clear()
    return get_ingest_settings()


__all__ = ["IngestSettings", "get_ingest_settings", "reload_ingest_settings"]