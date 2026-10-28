"""Filesystem locations owned by this application."""

from __future__ import annotations

from pathlib import Path
from typing import Iterable

BASE_DIR = Path(__file__).resolve().parents[2]
STATIC_DIR = BASE_DIR / "static"
CONFIG_DIR = BASE_DIR / "config"
DATA_DIR = BASE_DIR / "data"
MEMORY_DIR = DATA_DIR / "memory"
KNOWLEDGE_DIR = BASE_DIR / "knowledge"
LOG_DIR = BASE_DIR / "logs"


def ensure_runtime_dirs(paths: Iterable[Path] | None = None) -> None:
    """Create runtime directories without touching their contents."""
    targets = paths or (DATA_DIR, MEMORY_DIR, KNOWLEDGE_DIR, LOG_DIR)
    for path in targets:
        path.mkdir(parents=True, exist_ok=True)