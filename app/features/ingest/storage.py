"""Filesystem storage for ingestion jobs and review state."""

from __future__ import annotations

import json
import os
import re
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional
from uuid import uuid4

from app.features.ingest.config import IngestSettings
from app.platform.paths import BASE_DIR

FILE_ID_RE = re.compile(r"^\d{14}-[a-f0-9]{8}$")
BATCH_ID_RE = re.compile(r"^[A-Za-z0-9_-]{8,80}$")
RESULT_NAME = "result.json"
CONTENT_NAME = "content.md"
FALLBACK_FILENAME = "upload.bin"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def safe_filename(name: str) -> str:
    base = Path(str(name or FALLBACK_FILENAME)).name
    kept = [char if (char.isalnum() or char in "._-") else "_" for char in base]
    cleaned = "".join(kept).strip(" .")
    return cleaned or FALLBACK_FILENAME


def new_file_id() -> str:
    return datetime.now().strftime("%Y%m%d%H%M%S") + "-" + uuid4().hex[:8]


def new_batch_id() -> str:
    return "batch_" + datetime.now().strftime("%Y%m%d%H%M%S") + "_" + uuid4().hex[:8]


def valid_file_id(file_id: str) -> bool:
    return bool(FILE_ID_RE.fullmatch(str(file_id or "")))


def valid_batch_id(batch_id: str) -> bool:
    return bool(BATCH_ID_RE.fullmatch(str(batch_id or "")))


def job_dir(settings: IngestSettings, file_id: str) -> Path:
    if not valid_file_id(file_id):
        raise ValueError("invalid file id")
    return settings.parsed_dir / file_id


def result_path(settings: IngestSettings, file_id: str) -> Path:
    return job_dir(settings, file_id) / RESULT_NAME


def content_path(settings: IngestSettings, file_id: str) -> Path:
    return job_dir(settings, file_id) / CONTENT_NAME


def display_path(path: Path) -> str:
    try:
        return path.resolve().relative_to(BASE_DIR).as_posix()
    except ValueError:
        return str(path.resolve())


def resolve_display_path(value: str) -> Path:
    path = Path(str(value or ""))
    return path if path.is_absolute() else (BASE_DIR / path)


def archive_upload(settings: IngestSettings, filename: str, content: bytes) -> Path:
    day = datetime.now().strftime("%Y%m%d")
    target_dir = settings.upload_dir / day
    target_dir.mkdir(parents=True, exist_ok=True)
    name = safe_filename(filename)
    target = target_dir / (datetime.now().strftime("%H%M%S") + "_" + name)
    index = 1
    while target.exists():
        target = target_dir / (
            datetime.now().strftime("%H%M%S") + "_" + str(index) + "_" + name
        )
        index += 1
    target.write_bytes(content)
    return target


def save_job_source(settings: IngestSettings, file_id: str, filename: str, content: bytes) -> Path:
    source_dir = job_dir(settings, file_id) / "source"
    source_dir.mkdir(parents=True, exist_ok=True)
    target = source_dir / safe_filename(filename)
    target.write_bytes(content)
    return target


def write_content(settings: IngestSettings, file_id: str, content: str) -> Path:
    target = content_path(settings, file_id)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(str(content or ""), encoding="utf-8")
    return target


def read_content(settings: IngestSettings, file_id: str) -> str:
    target = content_path(settings, file_id)
    if not target.is_file():
        return ""
    return target.read_text(encoding="utf-8")


def write_result(settings: IngestSettings, file_id: str, payload: Dict[str, Any]) -> Path:
    target = result_path(settings, file_id)
    target.parent.mkdir(parents=True, exist_ok=True)
    # Unique temp names avoid two writers sharing one temp file. On Windows,
    # os.replace can also fail transiently while a status reader has the target
    # open, so retry briefly instead of treating a read/write race as a job error.
    temporary = target.with_name("." + target.name + "." + uuid4().hex + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    try:
        for attempt in range(6):
            try:
                os.replace(temporary, target)
                return target
            except PermissionError:
                if attempt >= 5:
                    raise
                time.sleep(0.03 * (attempt + 1))
    finally:
        if temporary.exists():
            try:
                temporary.unlink()
            except OSError:
                pass
    return target


def read_result(settings: IngestSettings, file_id: str) -> Optional[Dict[str, Any]]:
    try:
        target = result_path(settings, file_id)
    except ValueError:
        return None
    if not target.is_file():
        return None
    for attempt in range(4):
        try:
            payload = json.loads(target.read_text(encoding="utf-8"))
            return payload if isinstance(payload, dict) else None
        except PermissionError:
            if attempt >= 3:
                return None
            time.sleep(0.02 * (attempt + 1))
        except (OSError, ValueError):
            return None
    return None


def list_results(settings: IngestSettings) -> list[Dict[str, Any]]:
    if not settings.parsed_dir.is_dir():
        return []
    items: list[Dict[str, Any]] = []
    for directory in sorted(settings.parsed_dir.iterdir(), reverse=True):
        if not directory.is_dir() or not valid_file_id(directory.name):
            continue
        payload = read_result(settings, directory.name)
        if payload:
            items.append(payload)
    return items


def remove_job(settings: IngestSettings, file_id: str) -> None:
    target = job_dir(settings, file_id)
    if target.is_dir():
        shutil.rmtree(target)


__all__ = [
    "archive_upload",
    "content_path",
    "display_path",
    "job_dir",
    "list_results",
    "new_batch_id",
    "new_file_id",
    "read_content",
    "read_result",
    "remove_job",
    "resolve_display_path",
    "result_path",
    "save_job_source",
    "utc_now",
    "valid_batch_id",
    "valid_file_id",
    "write_content",
    "write_result",
]