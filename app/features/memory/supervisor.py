"""Read-only memory supervision: findings and suggested actions only."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import threading
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.features.memory.memories import content_key
from app.features.memory.runtime import get_store
from app.features.memory.util import json_dumps, json_loads, new_id, utc_now
from app.platform.paths import BASE_DIR

logger = logging.getLogger(__name__)

POLICY_FILE = Path(
    os.environ.get(
        "RAGKB_MEMORY_SUPERVISOR_FILE",
        str(BASE_DIR / "config" / "memory-supervisor.json"),
    )
).expanduser()

SUGGESTIONS = {
    "missing_source": "review_source_or_disable",
    "expired": "review_and_expire",
    "sensitive_active": "review_sensitivity",
    "unsafe_instruction": "review_and_disable",
    "low_confidence": "review_confidence",
    "stale": "reconfirm_or_expire",
    "duplicate_content": "merge_or_remove",
    "active_budget_exceeded": "review_active_memories",
}
_UNSAFE_RE = re.compile(
    r"(?i)\b(?:ignore|disregard|override)\b.{0,32}\b(?:previous|above|system)\b"
    r"|\bsystem prompt\b|忽略.{0,20}(?:指令|提示)|绕过.{0,20}(?:规则|限制)"
)
_SCAN_LOCK = threading.RLock()
_MONITOR_LOCK = threading.RLock()
_MONITOR_STOP = threading.Event()
_MONITOR_THREAD: Optional[threading.Thread] = None


@dataclass(frozen=True)
class SupervisorPolicy:
    enabled: bool = True
    scan_interval_seconds: int = 3600
    stale_days: int = 180
    min_confidence: float = 0.45
    max_active_memories: int = 200


def _bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return default


def _bounded_int(value: Any, default: int, low: int, high: int) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError):
        return default
    return max(low, min(high, result))


def _bounded_float(value: Any, default: float, low: float, high: float) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return max(low, min(high, result))


def load_policy() -> SupervisorPolicy:
    """Load monitoring policy; no policy option changes memory state."""
    if not POLICY_FILE.is_file():
        return SupervisorPolicy()
    try:
        raw = json.loads(POLICY_FILE.read_text(encoding="utf-8-sig"))
    except Exception:
        logger.warning("Memory supervisor policy is unreadable; using defaults")
        return SupervisorPolicy()
    if not isinstance(raw, dict):
        return SupervisorPolicy()
    return SupervisorPolicy(
        enabled=_bool(raw.get("enabled"), True),
        scan_interval_seconds=_bounded_int(
            raw.get("scan_interval_seconds"), 3600, 30, 86400
        ),
        stale_days=_bounded_int(raw.get("stale_days"), 180, 1, 3650),
        min_confidence=_bounded_float(raw.get("min_confidence"), 0.45, 0.0, 1.0),
        max_active_memories=_bounded_int(raw.get("max_active_memories"), 200, 1, 10000),
    )


def save_policy(policy: SupervisorPolicy) -> None:
    POLICY_FILE.parent.mkdir(parents=True, exist_ok=True)
    temporary = POLICY_FILE.with_name(POLICY_FILE.name + ".tmp")
    temporary.write_text(
        json.dumps(asdict(policy), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, POLICY_FILE)


def policy_dict(policy: Optional[SupervisorPolicy] = None) -> Dict[str, Any]:
    return asdict(policy or load_policy())


def update_policy(changes: Dict[str, Any]) -> SupervisorPolicy:
    values = asdict(load_policy())
    for key, value in changes.items():
        if key in values and value is not None:
            values[key] = value
    policy = SupervisorPolicy(
        enabled=_bool(values.get("enabled"), True),
        scan_interval_seconds=_bounded_int(
            values.get("scan_interval_seconds"), 3600, 30, 86400
        ),
        stale_days=_bounded_int(values.get("stale_days"), 180, 1, 3650),
        min_confidence=_bounded_float(values.get("min_confidence"), 0.45, 0.0, 1.0),
        max_active_memories=_bounded_int(values.get("max_active_memories"), 200, 1, 10000),
    )
    save_policy(policy)
    return policy


def _parse_time(value: Any) -> Optional[datetime]:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _fingerprint(memory_id: Optional[str], code: str, detail: Dict[str, Any]) -> str:
    raw = f"{memory_id or 'system'}|{code}|{json.dumps(detail, sort_keys=True, ensure_ascii=False)}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _finding(
    *,
    code: str,
    severity: str,
    message: str,
    memory: Optional[Dict[str, Any]] = None,
    detail: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    memory_id = str(memory.get("id") or "") if memory else ""
    payload = dict(detail or {})
    payload["suggested_action"] = SUGGESTIONS[code]
    return {
        "id": new_id("find"),
        "memory_id": memory_id or None,
        "code": code,
        "severity": severity,
        "message": message,
        "detail": payload,
        "fingerprint": _fingerprint(memory_id or None, code, payload),
        "status": "open",
    }


def _read_meta(store: Any, key: str) -> str:
    try:
        with store.db.connect() as conn:
            row = conn.execute(
                "SELECT value FROM memory_supervisor_meta WHERE meta_key = ?", (key,)
            ).fetchone()
        return str(row[0]) if row else ""
    except Exception:
        return ""


def _write_scan(store: Any, findings_list: List[Dict[str, Any]]) -> None:
    """Persist findings only; this function never changes a memory row."""
    now = utc_now()
    with store.db.transaction() as conn:
        for item in findings_list:
            conn.execute(
                """
                INSERT INTO memory_supervisor_findings(
                    id, memory_id, code, severity, status, message,
                    detail_json, fingerprint, detected_at, updated_at, resolved_at
                ) VALUES (?, ?, ?, ?, 'open', ?, ?, ?, ?, ?, NULL)
                ON CONFLICT(fingerprint) DO UPDATE SET
                    severity = excluded.severity,
                    status = CASE
                        WHEN memory_supervisor_findings.status = 'ignored' THEN 'ignored'
                        ELSE 'open'
                    END,
                    message = excluded.message,
                    detail_json = excluded.detail_json,
                    updated_at = excluded.updated_at,
                    resolved_at = CASE
                        WHEN memory_supervisor_findings.status = 'ignored'
                        THEN memory_supervisor_findings.resolved_at
                        ELSE NULL
                    END
                """,
                (
                    item["id"],
                    item["memory_id"],
                    item["code"],
                    item["severity"],
                    item["message"],
                    json_dumps(item["detail"]),
                    item["fingerprint"],
                    now,
                    now,
                ),
            )
        conn.execute(
            """
            INSERT INTO memory_supervisor_meta(meta_key, value, updated_at)
            VALUES ('last_scan_at', ?, ?)
            ON CONFLICT(meta_key) DO UPDATE SET
                value = excluded.value, updated_at = excluded.updated_at
            """,
            (now, now),
        )


def _row_to_finding(row: Any) -> Dict[str, Any]:
    return {
        "id": row["id"],
        "memory_id": row["memory_id"],
        "code": row["code"],
        "severity": row["severity"],
        "status": row["status"],
        "message": row["message"],
        "detail": json_loads(row["detail_json"], {}),
        "detected_at": row["detected_at"],
        "updated_at": row["updated_at"],
        "resolved_at": row["resolved_at"],
    }


def scan() -> Dict[str, Any]:
    """Inspect memories and persist advisory findings without mutating memories."""
    policy = load_policy()
    if not policy.enabled:
        return {"enabled": False, "scanned": 0, "findings": 0}
    store = get_store()
    if store is None:
        return {
            "enabled": True,
            "ready": False,
            "reason": "memory_disabled",
            "scanned": 0,
            "findings": 0,
        }

    with _SCAN_LOCK:
        memories = store.memories.list(limit=500, include_deleted=False)
        active = [item for item in memories if str(item.get("status")) == "active"]
        findings_list: List[Dict[str, Any]] = []
        now = datetime.now(timezone.utc)
        stale_before = now - timedelta(days=policy.stale_days)
        by_content: Dict[str, List[Dict[str, Any]]] = {}

        for memory in active:
            key = content_key(memory.get("content") or "")
            by_content.setdefault(key, []).append(memory)

        if len(active) > policy.max_active_memories:
            findings_list.append(
                _finding(
                    code="active_budget_exceeded",
                    severity="warning",
                    message="Active memory count exceeds the configured budget",
                    detail={"count": len(active), "limit": policy.max_active_memories},
                )
            )

        for memory in memories:
            status = str(memory.get("status") or "")
            memory_id = str(memory.get("id") or "")
            sources = store.memories.sources(memory_id) if memory_id else []
            has_quote = any(str(source.get("quote") or "").strip() for source in sources)
            content = str(memory.get("content") or "")
            detail_base = {"subject": str(memory.get("subject") or "")[:80]}

            if status == "active" and not has_quote:
                findings_list.append(
                    _finding(
                        code="missing_source",
                        severity="high",
                        message="Active memory has no quoted source; review before keeping it active",
                        memory=memory,
                        detail=detail_base,
                    )
                )
            if status == "active" and str(memory.get("sensitivity") or "normal") != "normal":
                findings_list.append(
                    _finding(
                        code="sensitive_active",
                        severity="critical",
                        message="Sensitive memory is active; request user review",
                        memory=memory,
                        detail=detail_base,
                    )
                )
            if status == "active" and float(memory.get("confidence") or 0.0) < policy.min_confidence:
                findings_list.append(
                    _finding(
                        code="low_confidence",
                        severity="warning",
                        message="Active memory has low confidence; review before keeping it active",
                        memory=memory,
                        detail={**detail_base, "confidence": memory.get("confidence")},
                    )
                )
            valid_to = _parse_time(memory.get("valid_to"))
            if status == "active" and valid_to is not None and valid_to <= now:
                findings_list.append(
                    _finding(
                        code="expired",
                        severity="high",
                        message="Active memory is past its valid-until date; review and expire it",
                        memory=memory,
                        detail={**detail_base, "valid_to": memory.get("valid_to")},
                    )
                )
            if _UNSAFE_RE.search(content):
                findings_list.append(
                    _finding(
                        code="unsafe_instruction",
                        severity="critical",
                        message="Memory contains instruction-like text; review and disable it",
                        memory=memory,
                        detail=detail_base,
                    )
                )
            updated = _parse_time(memory.get("last_confirmed_at") or memory.get("updated_at"))
            if status == "active" and updated is not None and updated < stale_before:
                findings_list.append(
                    _finding(
                        code="stale",
                        severity="info",
                        message="Active memory has not been recently confirmed; review it",
                        memory=memory,
                        detail={
                            **detail_base,
                            "last_seen_at": memory.get("last_confirmed_at")
                            or memory.get("updated_at"),
                        },
                    )
                )

        for group in by_content.values():
            if len(group) < 2:
                continue
            ids = [str(item.get("id")) for item in group]
            for memory in group:
                findings_list.append(
                    _finding(
                        code="duplicate_content",
                        severity="warning",
                        message="Multiple active memories contain equivalent content; review them",
                        memory=memory,
                        detail={"memory_ids": ids},
                    )
                )

        _write_scan(store, findings_list)
        return {
            "enabled": True,
            "ready": True,
            "scanned": len(memories),
            "findings": len(findings_list),
            "advisory_only": True,
            "last_scan_at": utc_now(),
        }


def findings(*, status: str = "open", limit: int = 100) -> List[Dict[str, Any]]:
    store = get_store()
    if store is None:
        return []
    size = max(1, min(500, int(limit or 100)))
    where = "" if status == "all" else " WHERE status = ?"
    values: list[Any] = [] if status == "all" else [status]
    values.append(size)
    try:
        with store.db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM memory_supervisor_findings" + where
                + " ORDER BY detected_at DESC LIMIT ?",
                tuple(values),
            ).fetchall()
        return [_row_to_finding(row) for row in rows]
    except Exception:
        return []


def resolve_finding(finding_id: str, *, status: str = "resolved") -> bool:
    if status not in ("resolved", "ignored"):
        raise ValueError("status must be resolved or ignored")
    store = get_store()
    if store is None:
        return False
    now = utc_now()
    with store.db.transaction() as conn:
        row = conn.execute(
            "SELECT id FROM memory_supervisor_findings WHERE id = ?", (finding_id,)
        ).fetchone()
        if row is None:
            return False
        conn.execute(
            "UPDATE memory_supervisor_findings SET status = ?, updated_at = ?, "
            "resolved_at = ? WHERE id = ?",
            (status, now, now, finding_id),
        )
    return True


def status() -> Dict[str, Any]:
    policy = load_policy()
    store = get_store()
    if store is None:
        return {
            "policy": policy_dict(policy),
            "ready": False,
            "enabled": False,
            "reason": "memory_disabled",
            "open_findings": 0,
        }
    counts: Dict[str, int] = {}
    try:
        with store.db.connect() as conn:
            for row in conn.execute(
                "SELECT status, COUNT(*) FROM memory_supervisor_findings GROUP BY status"
            ):
                counts[str(row[0])] = int(row[1])
    except Exception:
        counts = {}
    return {
        "policy": policy_dict(policy),
        "ready": True,
        "enabled": bool(policy.enabled),
        "last_scan_at": _read_meta(store, "last_scan_at"),
        "open_findings": counts.get("open", 0),
        "finding_counts": counts,
    }


def start_background() -> bool:
    """Start advisory scans only when memory and supervision are enabled."""
    from app.features.memory.config import get_memory_settings

    if not get_memory_settings().memory_enabled or not load_policy().enabled:
        return False
    if get_store() is None:
        return False
    global _MONITOR_THREAD
    with _MONITOR_LOCK:
        if _MONITOR_THREAD is not None and _MONITOR_THREAD.is_alive():
            return True
        _MONITOR_STOP.clear()

        def loop() -> None:
            while not _MONITOR_STOP.wait(float(load_policy().scan_interval_seconds)):
                try:
                    scan()
                except Exception as exc:  # noqa: BLE001
                    logger.warning("Memory supervisor scan failed: %s", type(exc).__name__)

        _MONITOR_THREAD = threading.Thread(
            target=loop,
            name="memory-supervisor",
            daemon=True,
        )
        _MONITOR_THREAD.start()
    return True


def stop_background() -> None:
    global _MONITOR_THREAD
    _MONITOR_STOP.set()
    with _MONITOR_LOCK:
        thread = _MONITOR_THREAD
        _MONITOR_THREAD = None
    if thread is not None and thread.is_alive():
        thread.join(timeout=2.0)


__all__ = [
    "SupervisorPolicy",
    "findings",
    "load_policy",
    "policy_dict",
    "resolve_finding",
    "save_policy",
    "scan",
    "start_background",
    "status",
    "stop_background",
    "update_policy",
]