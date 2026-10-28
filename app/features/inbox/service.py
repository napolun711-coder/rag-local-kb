"""Safe, opt-in folder scanning into the local ingestion review queue."""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from app.features.ingest import service as ingest_service
from app.features.ingest import storage
from app.features.ingest.config import get_ingest_settings
from app.features.inbox.config import InboxSettings, get_inbox_settings
from app.platform.config import get_settings
from app.platform.paths import BASE_DIR

STATE_VERSION = 1
EVENT_LIMIT = 30
IGNORED_SUFFIXES = frozenset(
    {
        ".tmp",
        ".temp",
        ".part",
        ".partial",
        ".crdownload",
        ".download",
        ".lock",
        ".swp",
    }
)
_LOCK = threading.RLock()
_WORKER_LOCK = threading.RLock()
_WORKER_STOP: Optional[threading.Event] = None
_WORKER_THREAD: Optional[threading.Thread] = None
_LAST_ERROR = ""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _empty_state() -> Dict[str, Any]:
    return {
        "version": STATE_VERSION,
        "directory": "",
        "candidates": {},
        "content_hashes": {},
        "events": [],
        "last_scan_at": "",
    }


def _load_state(path: Path) -> Dict[str, Any]:
    if not path.is_file():
        return _empty_state()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError):
        return _empty_state()
    if not isinstance(payload, dict) or int(payload.get("version") or 0) != STATE_VERSION:
        return _empty_state()
    for key, default in (
        ("candidates", {}),
        ("content_hashes", {}),
        ("events", []),
    ):
        if not isinstance(payload.get(key), type(default)):
            payload[key] = default
    return payload


def _save_state(path: Path, state: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(state, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _display(path: Path) -> str:
    try:
        return path.resolve(strict=False).relative_to(BASE_DIR).as_posix()
    except ValueError:
        return str(path.resolve(strict=False))


def _append_event(state: Dict[str, Any], kind: str, name: str, detail: str) -> None:
    events = state.get("events") if isinstance(state.get("events"), list) else []
    events.append(
        {
            "at": _utc_now(),
            "kind": kind,
            "name": name,
            "detail": detail[:300],
        }
    )
    state["events"] = events[-EVENT_LIMIT:]


def _protected_paths() -> tuple[Path, ...]:
    app = get_settings()
    ingest = get_ingest_settings()
    return (
        app.knowledge_dir.resolve(),
        app.log_dir.resolve(),
        ingest.upload_dir.resolve(),
        ingest.parsed_dir.resolve(),
    )


def _root_problem(settings: InboxSettings) -> str:
    root = settings.directory
    if root.is_symlink():
        return "收件箱目录不能是符号链接"
    if not root.exists():
        return "收件箱目录不存在"
    if not root.is_dir():
        return "收件箱路径不是目录"
    base = BASE_DIR.resolve()
    if root == base or base.is_relative_to(root):
        return "收件箱目录不能包含项目根目录"
    for protected in _protected_paths():
        if root == protected or root.is_relative_to(protected) or protected.is_relative_to(root):
            return "收件箱目录不能与项目、知识库、上传、解析或日志目录重叠"
    return ""


def _ignored(name: str) -> bool:
    lowered = name.casefold()
    return (
        not name
        or name.startswith(".")
        or name.startswith("~$")
        or lowered.endswith(tuple(IGNORED_SUFFIXES))
    )


def _eligible(path: Path, settings: InboxSettings) -> tuple[bool, str]:
    if _ignored(path.name):
        return False, ""
    if path.suffix.casefold() not in settings.allowed_extensions:
        return False, ""
    try:
        stat = path.stat()
    except OSError:
        return False, ""
    if stat.st_size > settings.max_file_size:
        return False, "文件超过上传大小上限"
    return True, ""


def _candidate_status(
    candidate: Dict[str, Any],
    *,
    name: str,
    size: int,
    mtime_ns: int,
) -> Dict[str, Any]:
    if candidate.get("name") != name:
        return {
            "name": name,
            "size": size,
            "mtime_ns": mtime_ns,
            "first_seen": _utc_now(),
            "first_seen_epoch": time.time(),
            "status": "settling",
        }
    if (
        int(candidate.get("size") or -1) != size
        or int(candidate.get("mtime_ns") or -1) != mtime_ns
    ):
        return {
            "name": name,
            "size": size,
            "mtime_ns": mtime_ns,
            "first_seen": _utc_now(),
            "first_seen_epoch": time.time(),
            "status": "settling",
        }
    return candidate


def _base_status(settings: InboxSettings, state: Dict[str, Any]) -> Dict[str, Any]:
    same_directory = str(state.get("directory") or "") == str(settings.directory)
    candidates = (
        state.get("candidates")
        if same_directory and isinstance(state.get("candidates"), dict)
        else {}
    )
    values = [item for item in candidates.values() if isinstance(item, dict)]
    with _WORKER_LOCK:
        running = bool(_WORKER_THREAD and _WORKER_THREAD.is_alive())
    problem = _root_problem(settings)
    hint_parts = []
    if not settings.enabled:
        hint_parts.append("收件箱扫描未启用。")
    elif problem:
        hint_parts.append(problem + "。")
    else:
        hint_parts.append(
            f"源文件保留在收件箱；扫描后只生成本机复核作业，不会自动写入知识库。"
        )
    if _LAST_ERROR:
        hint_parts.append("最近错误：" + _LAST_ERROR)
    return {
        "enabled": settings.enabled,
        "running": running,
        "poll_seconds": settings.poll_seconds,
        "settle_seconds": settings.settle_seconds,
        "waiting": sum(1 for item in values if item.get("status") == "settling"),
        "settling": sum(1 for item in values if item.get("status") == "settling"),
        "accepted": sum(1 for item in values if item.get("status") == "accepted"),
        "duplicate": sum(1 for item in values if item.get("status") == "duplicate"),
        "failed": sum(1 for item in values if item.get("status") == "failed"),
        "source_files": 0,
        "last_scan_at": str(state.get("last_scan_at") or ""),
        "events": list(state.get("events") or [])[-10:] if same_directory else [],
        "dirs": {
            "inbox": _display(settings.directory),
            "state": _display(settings.state_path),
        },
        "hint": " ".join(hint_parts),
    }


def status(settings: Optional[InboxSettings] = None) -> Dict[str, Any]:
    cfg = settings or get_inbox_settings()
    state = _load_state(cfg.state_path)
    result = _base_status(cfg, state)
    if result["enabled"] and not _root_problem(cfg) and cfg.directory.is_dir():
        try:
            result["source_files"] = sum(
                1 for item in cfg.directory.iterdir() if item.is_file() and not item.is_symlink()
            )
        except OSError:
            pass
    return result


def scan_once(
    settings: Optional[InboxSettings] = None,
    *,
    reason: str = "manual",
) -> Dict[str, Any]:
    global _LAST_ERROR
    cfg = settings or get_inbox_settings()
    if not cfg.enabled:
        return {
            "ok": False,
            "message": "收件箱扫描未启用",
            "reason": reason,
            **status(cfg),
        }
    problem = _root_problem(cfg)
    if problem:
        _LAST_ERROR = problem
        return {"ok": False, "message": problem, "reason": reason, **status(cfg)}

    _LAST_ERROR = ""
    with _LOCK:
        state = _load_state(cfg.state_path)
        if str(state.get("directory") or "") != str(cfg.directory):
            state["directory"] = str(cfg.directory)
            state["candidates"] = {}
            state["events"] = []
        candidates = state["candidates"]
        hashes = state["content_hashes"]
        processed = 0
        duplicates = 0
        failed = 0
        seen: set[str] = set()

        try:
            entries = sorted(cfg.directory.iterdir(), key=lambda item: item.name.casefold())
        except OSError as exc:
            _LAST_ERROR = type(exc).__name__
            return {"ok": False, "message": _LAST_ERROR, "reason": reason, **status(cfg)}

        for path in entries:
            if path.is_symlink() or not path.is_file():
                continue
            eligible, eligibility_error = _eligible(path, cfg)
            if not eligible:
                if eligibility_error:
                    key = path.name.casefold()
                    seen.add(key)
                    candidate = candidates.get(key) if isinstance(candidates.get(key), dict) else {}
                    try:
                        signature = path.stat()
                    except OSError:
                        continue
                    candidate = _candidate_status(
                        candidate,
                        name=path.name,
                        size=signature.st_size,
                        mtime_ns=signature.st_mtime_ns,
                    )
                    if candidate.get("status") == "settling":
                        candidate["status"] = "failed"
                        candidate["message"] = eligibility_error
                        candidate["updated_at"] = _utc_now()
                        failed += 1
                        _append_event(state, "failed", path.name, eligibility_error)
                    candidates[key] = candidate
                continue

            key = path.name.casefold()
            seen.add(key)
            try:
                signature = path.stat()
            except OSError:
                continue
            candidate = candidates.get(key) if isinstance(candidates.get(key), dict) else {}
            candidate = _candidate_status(
                candidate,
                name=path.name,
                size=signature.st_size,
                mtime_ns=signature.st_mtime_ns,
            )
            candidates[key] = candidate
            if candidate.get("status") in {"accepted", "duplicate", "failed"}:
                continue
            elapsed = max(0.0, time.time() - float(candidate.get("first_seen_epoch") or 0))
            if elapsed < cfg.settle_seconds:
                continue

            try:
                content = path.read_bytes()
                after = path.stat()
            except OSError as exc:
                candidate["status"] = "failed"
                candidate["message"] = type(exc).__name__
                candidate["updated_at"] = _utc_now()
                failed += 1
                _append_event(state, "failed", path.name, type(exc).__name__)
                continue
            if after.st_size != signature.st_size or after.st_mtime_ns != signature.st_mtime_ns:
                candidate.update(
                    {
                        "size": after.st_size,
                        "mtime_ns": after.st_mtime_ns,
                        "first_seen": _utc_now(),
                        "first_seen_epoch": time.time(),
                        "status": "settling",
                    }
                )
                continue

            digest = hashlib.sha256(content).hexdigest()
            existing = hashes.get(digest) if isinstance(hashes.get(digest), dict) else None
            if existing:
                candidate.update(
                    {
                        "status": "duplicate",
                        "sha256": digest,
                        "file_id": str(existing.get("file_id") or ""),
                        "updated_at": _utc_now(),
                    }
                )
                duplicates += 1
                _append_event(
                    state,
                    "duplicate",
                    path.name,
                    "相同内容已受理，源文件保留",
                )
                continue

            try:
                result = ingest_service.process_upload(
                    path.name,
                    content,
                    "application/octet-stream",
                )
            except Exception as exc:
                candidate.update(
                    {
                        "status": "failed",
                        "sha256": digest,
                        "message": str(exc)[:300] or type(exc).__name__,
                        "updated_at": _utc_now(),
                    }
                )
                failed += 1
                _append_event(state, "failed", path.name, candidate["message"])
                continue

            file_id = str(result.get("file_id") or "")
            candidate.update(
                {
                    "status": "accepted",
                    "sha256": digest,
                    "file_id": file_id,
                    "job_state": str(result.get("state") or ""),
                    "updated_at": _utc_now(),
                }
            )
            hashes[digest] = {
                "file_id": file_id,
                "name": path.name,
                "accepted_at": _utc_now(),
            }
            processed += 1
            _append_event(state, "accepted", path.name, "已进入本机复核队列")

        for key in list(candidates):
            if key not in seen and candidates[key].get("status") == "settling":
                candidates.pop(key, None)

        state["last_scan_at"] = _utc_now()
        _save_state(cfg.state_path, state)
        return {
            "ok": True,
            "message": f"扫描完成：受理 {processed}，重复 {duplicates}，失败 {failed}",
            "processed": processed,
            "duplicates": duplicates,
            "failed": failed,
            "reason": reason,
            **_base_status(cfg, state),
        }


def start_background() -> None:
    global _WORKER_STOP, _WORKER_THREAD
    settings = get_inbox_settings()
    if not settings.enabled:
        return
    with _WORKER_LOCK:
        if _WORKER_THREAD and _WORKER_THREAD.is_alive():
            return
        stop_event = threading.Event()

        def run() -> None:
            while not stop_event.is_set():
                try:
                    scan_once(settings, reason="poll")
                except Exception:
                    global _LAST_ERROR
                    _LAST_ERROR = "后台扫描异常"
                stop_event.wait(settings.poll_seconds)

        thread = threading.Thread(
            target=run,
            name="inbox-scanner",
            daemon=True,
        )
        _WORKER_STOP = stop_event
        _WORKER_THREAD = thread
        thread.start()


def stop_background() -> None:
    global _WORKER_STOP, _WORKER_THREAD
    with _WORKER_LOCK:
        stop_event = _WORKER_STOP
        thread = _WORKER_THREAD
    if stop_event is not None:
        stop_event.set()
    if thread is not None:
        thread.join(timeout=3)
    with _WORKER_LOCK:
        _WORKER_STOP = None
        _WORKER_THREAD = None


__all__ = ["scan_once", "start_background", "status", "stop_background"]
