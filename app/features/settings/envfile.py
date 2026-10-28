"""Comment-preserving, atomic updates for configuration files."""

from __future__ import annotations

import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Mapping, Optional


class EnvFileError(RuntimeError):
    pass


def parse_env_text(text: str) -> Dict[str, str]:
    values: Dict[str, str] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not key:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        values[key] = value
    return values


def read_env_file(path: Path) -> Dict[str, str]:
    if not path.is_file():
        return {}
    try:
        return parse_env_text(path.read_text(encoding="utf-8-sig"))
    except OSError as exc:
        raise EnvFileError(f"Unable to read configuration file: {path.name}") from exc


def _format_value(value: str) -> str:
    if "\n" in value or "\r" in value:
        raise EnvFileError("Configuration values cannot contain newlines")
    if not value:
        return ""
    needs_quotes = (
        value != value.strip()
        or "#" in value
        or any(character.isspace() for character in value)
    )
    if not needs_quotes:
        return value
    if "'" not in value:
        return f"'{value}'"
    if '"' not in value:
        return f'"{value}"'
    raise EnvFileError("Configuration value contains both quote types")


def _backup(path: Path, backup_dir: Path, keep: int = 20) -> Optional[Path]:
    if not path.is_file():
        return None
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    destination = backup_dir / f"{path.parent.name}-{path.name}.{stamp}.bak"
    shutil.copy2(path, destination)
    backups = sorted(
        backup_dir.glob(f"{path.parent.name}-{path.name}.*.bak"),
        key=lambda item: item.stat().st_mtime,
        reverse=True,
    )
    for old in backups[keep:]:
        try:
            old.unlink()
        except OSError:
            pass
    return destination


def update_env_file(
    path: Path,
    updates: Mapping[str, Optional[str]],
    *,
    backup_dir: Path,
) -> Optional[Path]:
    """Apply key updates while preserving comments and unrelated entries."""
    if not updates:
        return None
    old_text = ""
    if path.is_file():
        try:
            old_text = path.read_text(encoding="utf-8-sig")
        except OSError as exc:
            raise EnvFileError(f"Unable to read configuration file: {path.name}") from exc

    newline = "\r\n" if "\r\n" in old_text else "\n"
    lines = old_text.splitlines()
    changed = False
    seen: set[str] = set()

    for index, raw_line in enumerate(lines):
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key = stripped.split("=", 1)[0].strip()
        if key not in updates:
            continue
        seen.add(key)
        value = updates[key]
        if value is None:
            if raw_line != "":
                lines[index] = ""
                changed = True
            continue
        replacement = f"{key}={_format_value(str(value))}"
        if raw_line != replacement:
            lines[index] = replacement
            changed = True

    for key, value in updates.items():
        if key in seen or value is None:
            continue
        lines.append(f"{key}={_format_value(str(value))}")
        changed = True

    if not changed:
        return None
    backup = _backup(path, backup_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    content = newline.join(lines)
    if content and not content.endswith(newline):
        content += newline
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(content, encoding="utf-8")
    os.replace(temporary, path)
    return backup
__all__ = [
    "EnvFileError",
    "parse_env_text",
    "read_env_file",
    "update_env_file",
]
