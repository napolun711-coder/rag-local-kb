"""Detect changes to the embedding identity that owns a vector index."""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Mapping, Optional

from app.platform.config import get_settings
from app.platform.paths import BASE_DIR

MANIFEST_VERSION = 1
CONFIRMATION = "REBUILD"
BLOCKING_STATES = frozenset({"mismatch", "unrecorded", "unreadable"})


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def manifest_path() -> Path:
    return get_settings().data_dir / "index-compatibility.json"


def _engine_env_path() -> Path:
    override = os.environ.get("RAGKB_SETTINGS_ENGINE_ENV_FILE", "").strip()
    if not override:
        override = os.environ.get("RAGKB_LIGHTRAG_ENGINE_ENV_FILE", "").strip()
    if override:
        path = Path(override).expanduser()
        return path.resolve() if path.is_absolute() else (BASE_DIR / path).resolve()
    return BASE_DIR / "services" / "lightrag_engine" / ".env"


def _read_env(path: Path) -> Dict[str, str]:
    values: Dict[str, str] = {}
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


def _text(value: Any) -> str:
    return str(value or "").strip()


def _normalized_path(value: str) -> str:
    text = _text(value)
    if not text:
        return ""
    path = Path(text).expanduser()
    if not path.is_absolute():
        path = BASE_DIR / path
    return os.path.normcase(str(path.resolve(strict=False)))


def identity_from_service(service: Mapping[str, Any]) -> Dict[str, str]:
    """Build a non-secret identity from the shell's runtime engine health."""
    engine = service.get("engine_identity")
    if not isinstance(engine, Mapping):
        return {}
    configured = _read_env(_engine_env_path())
    index_key = _text(engine.get("index_key"))
    if not index_key:
        working_directory = _normalized_path(_text(engine.get("working_directory")))
        if working_directory:
            index_key = hashlib.sha256(working_directory.encode("utf-8")).hexdigest()
    identity = {
        "index_key": index_key,
        "embedding_binding": _text(engine.get("embedding_binding")),
        "embedding_model": _text(engine.get("embedding_model")),
        "embedding_binding_host": _text(engine.get("embedding_binding_host")).rstrip("/"),
        "embedding_dim": _text(engine.get("embedding_dim"))
        or _text(configured.get("EMBEDDING_DIM"))
        or _text(os.environ.get("EMBEDDING_DIM")),
        "vector_storage": _text(engine.get("vector_storage")),
    }
    required = ("index_key", "embedding_binding", "embedding_model", "vector_storage")
    if not all(identity[key] for key in required):
        return {}
    return identity


def _fingerprint(identity: Mapping[str, str]) -> str:
    payload = {
        key: value
        for key, value in identity.items()
        if key != "index_key"
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _load(path: Path) -> tuple[Dict[str, Any], str]:
    if not path.exists():
        return {"version": MANIFEST_VERSION, "indexes": {}}, ""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError):
        return {}, "Index compatibility manifest cannot be read."
    if not isinstance(payload, dict):
        return {}, "Index compatibility manifest is invalid."
    if int(payload.get("version") or 0) != MANIFEST_VERSION:
        return {}, "Index compatibility manifest version is unsupported."
    indexes = payload.get("indexes")
    if not isinstance(indexes, dict):
        return {}, "Index compatibility manifest is invalid."
    return payload, ""


def _save(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _record(payload: Dict[str, Any], identity: Mapping[str, str], path: Path) -> Dict[str, Any]:
    indexes = payload.setdefault("indexes", {})
    indexes[identity["index_key"]] = {
        "fingerprint": _fingerprint(identity),
        "identity": dict(identity),
        "recorded_at": _utc_now(),
    }
    _save(path, payload)
    return payload


def status_from_service(
    service: Mapping[str, Any],
    *,
    path: Optional[Path] = None,
    create_baseline: bool = False,
) -> Dict[str, Any]:
    target = path or manifest_path()
    identity = identity_from_service(service)
    if not identity:
        return {
            "state": "unavailable",
            "compatible": False,
            "blocking": False,
            "message": "Runtime embedding identity is unavailable.",
            "current": {},
            "recorded": {},
        }

    payload, problem = _load(target)
    if problem:
        return {
            "state": "unreadable",
            "compatible": False,
            "blocking": True,
            "message": problem,
            "current": identity,
            "recorded": {},
        }

    indexes = payload.get("indexes") if isinstance(payload.get("indexes"), dict) else {}
    entry = indexes.get(identity["index_key"])
    if not isinstance(entry, dict):
        if create_baseline:
            _record(payload, identity, target)
            entry = indexes.get(identity["index_key"])
        else:
            return {
                "state": "unrecorded",
                "compatible": False,
                "blocking": True,
                "message": (
                    "No index compatibility baseline exists for this working "
                    "directory. Verify the index, then record the current identity."
                ),
                "current": identity,
                "recorded": {},
            }

    recorded = entry.get("identity") if isinstance(entry.get("identity"), dict) else {}
    expected = _text(entry.get("fingerprint"))
    current = _fingerprint(identity)
    compatible = bool(expected) and expected == current
    return {
        "state": "compatible" if compatible else "mismatch",
        "compatible": compatible,
        "blocking": not compatible,
        "message": (
            "The current embedding identity matches the recorded index baseline."
            if compatible
            else
            "The embedding identity differs from the recorded index baseline. "
            "Rebuild the index before answering or adding documents, then record "
            "the rebuilt identity."
        ),
        "current": identity,
        "recorded": recorded,
        "recorded_at": _text(entry.get("recorded_at")),
    }


def accept_current(
    service: Mapping[str, Any],
    confirmation: str,
    *,
    path: Optional[Path] = None,
) -> Dict[str, Any]:
    if confirmation != CONFIRMATION:
        raise ValueError(f"Type {CONFIRMATION} to confirm the rebuilt index.")
    target = path or manifest_path()
    identity = identity_from_service(service)
    if not identity:
        raise ValueError("Runtime embedding identity is unavailable.")
    payload, problem = _load(target)
    if problem:
        payload = {"version": MANIFEST_VERSION, "indexes": {}}
    _record(payload, identity, target)
    return status_from_service(service, path=target, create_baseline=False)


def require_compatible(service: Mapping[str, Any]) -> None:
    from app.adapters.lightrag.errors import KnowledgeIndexMismatch

    result = status_from_service(service)
    if result["blocking"]:
        raise KnowledgeIndexMismatch(str(result["message"]))


__all__ = [
    "BLOCKING_STATES",
    "CONFIRMATION",
    "accept_current",
    "identity_from_service",
    "manifest_path",
    "require_compatible",
    "status_from_service",
]
