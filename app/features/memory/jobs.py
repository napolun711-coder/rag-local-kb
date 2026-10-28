"""记忆任务队列（SQLite）。

职责：
- 入队 / 去抖（dedupe_key）/ 优先级 / 租约 / 重试 / 启动恢复；
- 会话提取水位（last_extracted_seq）；
- 只存 ID、范围、状态与短错误类型，不存消息正文。

不做：
- 规则/LLM 提取（ 的 extract.py）；
- 合并/冲突判定（ 的 merge.py）。
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Sequence

from app.features.memory.db import MemoryDatabase
from app.features.memory.util import json_dumps, json_loads, new_id, utc_now

logger = logging.getLogger(__name__)

JOB_QUEUED = "queued"
JOB_RUNNING = "running"
JOB_DONE = "done"
JOB_FAILED = "failed"
JOB_CANCELLED = "cancelled"
JOB_STATUSES = (JOB_QUEUED, JOB_RUNNING, JOB_DONE, JOB_FAILED, JOB_CANCELLED)
ACTIVE_STATUSES = (JOB_QUEUED, JOB_RUNNING)


def _now_plus(seconds: int) -> str:
    value = datetime.now(timezone.utc) + timedelta(seconds=max(0, int(seconds or 0)))
    return value.isoformat(timespec="milliseconds").replace("+00:00", "Z")


class MemoryJobQueue:
    def __init__(self, db: MemoryDatabase) -> None:
        self.db = db

    # ============================================================
    # 入队 / 去抖
    # ============================================================

    def enqueue(
        self,
        *,
        kind: str,
        session_id: Optional[str] = None,
        payload: Optional[Dict[str, Any]] = None,
        priority: int = 7,
        delay_seconds: int = 0,
        dedupe_key: str = "",
        max_attempts: int = 5,
    ) -> Dict[str, Any]:
        """入队；dedupe_key 命中 queued/running 时更新而不是新建。"""
        clean_kind = str(kind or "").strip()
        if not clean_kind:
            raise ValueError("kind 不能为空")
        clean_key = str(dedupe_key or "").strip()[:200]
        clean_session = str(session_id).strip() if session_id else None
        clean_payload = payload if isinstance(payload, dict) else {}
        clean_priority = max(1, min(99, int(priority or 7)))
        clean_attempts = max(1, min(50, int(max_attempts or 5)))
        now = utc_now()
        due = _now_plus(delay_seconds)

        with self.db.transaction() as conn:
            if clean_key:
                existing = conn.execute(
                    "SELECT * FROM memory_jobs "
                    "WHERE dedupe_key = ? AND status IN ('queued','running') "
                    "ORDER BY created_at LIMIT 1",
                    (clean_key,),
                ).fetchone()
                if existing is not None:
                    merged = json_loads(existing["payload_json"], {})
                    if not isinstance(merged, dict):
                        merged = {}
                    merged.update(clean_payload)
                    fields = [
                        "payload_json = ?",
                        "priority = ?",
                        "max_attempts = ?",
                        "updated_at = ?",
                    ]
                    values: List[Any] = [
                        json_dumps(merged),
                        clean_priority,
                        clean_attempts,
                        now,
                    ]
                    if existing["status"] == JOB_QUEUED:
                        # 只对排队中的任务做去抖：running 任务不要改它的 due time。
                        fields.append("available_at = ?")
                        values.append(due)
                    else:
                        # running 期间又来了新内容：标记完成后补一轮，避免漏掉新消息。
                        merged["_follow_up_requested"] = True
                        values[0] = json_dumps(merged)
                    if clean_session and not existing["session_id"]:
                        fields.append("session_id = ?")
                        values.append(clean_session)
                    values.append(existing["id"])
                    conn.execute(
                        f"UPDATE memory_jobs SET {', '.join(fields)} WHERE id = ?",
                        tuple(values),
                    )
                    return _job_row(
                        conn.execute(
                            "SELECT * FROM memory_jobs WHERE id = ?", (existing["id"],)
                        ).fetchone()
                    )

            job_id = new_id("job")
            conn.execute(
                """
                INSERT INTO memory_jobs(
                    id, kind, status, priority, session_id, dedupe_key, payload_json,
                    result_json, attempts, max_attempts, worker_id, last_error,
                    available_at, lease_until, created_at, updated_at, completed_at
                ) VALUES (?, ?, 'queued', ?, ?, ?, ?, '{}', 0, ?, '', '', ?, NULL, ?, ?, NULL)
                """,
                (
                    job_id,
                    clean_kind[:64],
                    clean_priority,
                    clean_session,
                    clean_key,
                    json_dumps(clean_payload),
                    clean_attempts,
                    due,
                    now,
                    now,
                ),
            )
            return _job_row(
                conn.execute(
                    "SELECT * FROM memory_jobs WHERE id = ?", (job_id,)
                ).fetchone()
            )

    # ============================================================
    # 领取 / 完成 / 失败 / 恢复
    # ============================================================

    def claim(
        self,
        *,
        worker_id: str = "worker",
        lease_seconds: int = 120,
        kinds: Optional[Sequence[str]] = None,
    ) -> Optional[Dict[str, Any]]:
        """领取一个到期任务；priority 越小越优先。"""
        now = utc_now()
        lease = _now_plus(max(1, int(lease_seconds or 120)))
        wanted = [str(kind) for kind in (kinds or []) if str(kind)]
        where = ["status = 'queued'", "available_at <= ?"]
        values: List[Any] = [now]
        if wanted:
            where.append("kind IN (" + ",".join("?" for _ in wanted) + ")")
            values.extend(wanted)
        with self.db.transaction() as conn:
            row = conn.execute(
                "SELECT * FROM memory_jobs WHERE "
                + " AND ".join(where)
                + " ORDER BY priority ASC, available_at ASC, created_at ASC LIMIT 1",
                tuple(values),
            ).fetchone()
            if row is None:
                return None
            conn.execute(
                "UPDATE memory_jobs SET status = 'running', attempts = attempts + 1, "
                "worker_id = ?, lease_until = ?, updated_at = ? "
                "WHERE id = ? AND status = 'queued'",
                (str(worker_id)[:100], lease, now, row["id"]),
            )
            return _job_row(
                conn.execute(
                    "SELECT * FROM memory_jobs WHERE id = ?", (row["id"],)
                ).fetchone()
            )

    def complete(
        self,
        job_id: str,
        result: Optional[Dict[str, Any]] = None,
        *,
        worker_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        now = utc_now()
        with self.db.transaction() as conn:
            row = conn.execute(
                "SELECT * FROM memory_jobs WHERE id = ?", (job_id,)
            ).fetchone()
            if row is None:
                return None
            where = "id = ? AND status = 'running'"
            values: List[Any] = [
                json_dumps(result if isinstance(result, dict) else {}),
                now,
                now,
                job_id,
            ]
            if worker_id is not None:
                where += " AND worker_id = ?"
                values.append(str(worker_id))
            cursor = conn.execute(
                "UPDATE memory_jobs SET status = 'done', result_json = ?, "
                "lease_until = NULL, worker_id = '', last_error = '', "
                "completed_at = ?, updated_at = ? WHERE " + where,
                tuple(values),
            )
            if not cursor.rowcount:
                return _job_row(row)
            return _job_row(
                conn.execute(
                    "SELECT * FROM memory_jobs WHERE id = ?", (job_id,)
                ).fetchone()
            )

    def fail(
        self,
        job_id: str,
        error: str = "",
        *,
        retryable: bool = True,
        retry_delay_seconds: Optional[int] = None,
        worker_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """失败：可重试则回到 queued，否则 failed；错误只存短类型。"""
        now = utc_now()
        short_error = str(error or "")[:500]
        with self.db.transaction() as conn:
            row = conn.execute(
                "SELECT * FROM memory_jobs WHERE id = ?", (job_id,)
            ).fetchone()
            if row is None:
                return None
            attempts = int(row["attempts"] or 0)
            max_attempts = int(row["max_attempts"] or 5)
            if not retryable or attempts >= max_attempts:
                status = JOB_FAILED
                completed_at: Optional[str] = now
                available_at = str(row["available_at"] or now)
            else:
                status = JOB_QUEUED
                completed_at = None
                delay = (
                    int(retry_delay_seconds)
                    if retry_delay_seconds is not None
                    else min(300, 5 * (2 ** max(0, attempts - 1)))
                )
                available_at = _now_plus(delay)
            where = "id = ? AND status = 'running'"
            values: List[Any] = [
                status,
                available_at,
                None,
                "",
                short_error,
                completed_at,
                now,
                job_id,
            ]
            if worker_id is not None:
                where += " AND worker_id = ?"
                values.append(str(worker_id))
            cursor = conn.execute(
                "UPDATE memory_jobs SET status = ?, available_at = ?, lease_until = ?, "
                "worker_id = ?, last_error = ?, completed_at = ?, updated_at = ? "
                "WHERE " + where,
                tuple(values),
            )
            if not cursor.rowcount:
                return _job_row(row)
            return _job_row(
                conn.execute(
                    "SELECT * FROM memory_jobs WHERE id = ?", (job_id,)
                ).fetchone()
            )

    def recover_stale(self, *, now: Optional[str] = None) -> int:
        """启动恢复：running 且租约过期 → queued；超过最大尝试次数 → failed。"""
        current = now or utc_now()
        available_now = utc_now()
        with self.db.transaction() as conn:
            failed = conn.execute(
                "UPDATE memory_jobs SET status = 'failed', lease_until = NULL, "
                "worker_id = '', last_error = 'lease_expired', completed_at = ?, "
                "updated_at = ? WHERE status = 'running' "
                "AND (lease_until IS NULL OR lease_until < ?) "
                "AND attempts >= max_attempts",
                (current, current, current),
            )
            requeued = conn.execute(
                "UPDATE memory_jobs SET status = 'queued', lease_until = NULL, "
                "worker_id = '', last_error = 'lease_expired', available_at = ?, "
                "updated_at = ? WHERE status = 'running' "
                "AND (lease_until IS NULL OR lease_until < ?)",
                (available_now, current, current),
            )
            return int(failed.rowcount or 0) + int(requeued.rowcount or 0)

    def retry(self, job_id: str, *, delay_seconds: int = 0) -> Optional[Dict[str, Any]]:
        """手动重试 failed 任务；重置 attempts。"""
        now = utc_now()
        with self.db.transaction() as conn:
            row = conn.execute(
                "SELECT * FROM memory_jobs WHERE id = ?", (job_id,)
            ).fetchone()
            if row is None:
                return None
            if str(row["status"]) != JOB_FAILED:
                raise ValueError("只有 failed 任务可以手动重试")
            dedupe_key = str(row["dedupe_key"] or "")
            if dedupe_key:
                conflict = conn.execute(
                    "SELECT id FROM memory_jobs WHERE dedupe_key = ? "
                    "AND status IN ('queued','running') AND id != ? LIMIT 1",
                    (dedupe_key, job_id),
                ).fetchone()
                if conflict is not None:
                    raise ValueError("同一 dedupe_key 已有活动任务")
            conn.execute(
                "UPDATE memory_jobs SET status = 'queued', attempts = 0, "
                "worker_id = '', last_error = '', result_json = '{}', "
                "available_at = ?, lease_until = NULL, completed_at = NULL, "
                "updated_at = ? WHERE id = ?",
                (_now_plus(delay_seconds), now, job_id),
            )
            return _job_row(
                conn.execute(
                    "SELECT * FROM memory_jobs WHERE id = ?", (job_id,)
                ).fetchone()
            )

    def cancel_for_session(self, session_id: str) -> int:
        now = utc_now()
        with self.db.transaction() as conn:
            cursor = conn.execute(
                "UPDATE memory_jobs SET status = 'cancelled', lease_until = NULL, "
                "worker_id = '', completed_at = ?, updated_at = ? "
                "WHERE session_id = ? AND status IN ('queued','running')",
                (now, now, session_id),
            )
            return int(cursor.rowcount or 0)

    # ============================================================
    # 读取
    # ============================================================

    def get(self, job_id: str) -> Optional[Dict[str, Any]]:
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM memory_jobs WHERE id = ?", (job_id,)
            ).fetchone()
        return _job_row(row) if row is not None else None

    def list_jobs(
        self,
        *,
        status: Optional[str] = None,
        kind: Optional[str] = None,
        session_id: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> List[Dict[str, Any]]:
        where: List[str] = []
        values: List[Any] = []
        if status in JOB_STATUSES:
            where.append("status = ?")
            values.append(status)
        if kind:
            where.append("kind = ?")
            values.append(str(kind))
        if session_id:
            where.append("session_id = ?")
            values.append(str(session_id))
        try:
            size = max(1, min(500, int(limit or 50)))
        except (TypeError, ValueError):
            size = 50
        try:
            start = max(0, int(offset or 0))
        except (TypeError, ValueError):
            start = 0
        clause = (" WHERE " + " AND ".join(where)) if where else ""
        values.extend([size, start])
        with self.db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM memory_jobs" + clause
                + " ORDER BY created_at DESC LIMIT ? OFFSET ?",
                tuple(values),
            ).fetchall()
        return [_job_row(row) for row in rows]

    def counts(self) -> Dict[str, int]:
        result = {status: 0 for status in JOB_STATUSES}
        try:
            with self.db.connect() as conn:
                rows = conn.execute(
                    "SELECT status, COUNT(*) FROM memory_jobs GROUP BY status"
                ).fetchall()
            for row in rows:
                key = str(row[0])
                if key in result:
                    result[key] = int(row[1] or 0)
        except Exception as exc:  # noqa: BLE001 - 状态查询失败不能反噬主流程
            logger.warning("任务统计失败（忽略）：%s", type(exc).__name__)
        result["total"] = sum(result[status] for status in JOB_STATUSES)
        return result

    # ============================================================
    # 会话水位
    # ============================================================

    def get_watermark(self, session_id: str) -> int:
        if not session_id:
            return 0
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT last_extracted_seq FROM session_memory_state WHERE session_id = ?",
                (session_id,),
            ).fetchone()
        return int(row[0] or 0) if row is not None else 0

    def set_watermark(self, session_id: str, seq: int) -> int:
        if not session_id:
            raise ValueError("session_id 不能为空")
        value = max(0, int(seq or 0))
        now = utc_now()
        with self.db.transaction() as conn:
            conn.execute(
                "INSERT INTO session_memory_state(session_id, last_extracted_seq, updated_at) "
                "VALUES (?, ?, ?) ON CONFLICT(session_id) DO UPDATE SET "
                "last_extracted_seq = MAX(last_extracted_seq, excluded.last_extracted_seq), "
                "updated_at = excluded.updated_at",
                (session_id, value, now),
            )
            row = conn.execute(
                "SELECT last_extracted_seq FROM session_memory_state WHERE session_id = ?",
                (session_id,),
            ).fetchone()
        return int(row[0] or 0) if row is not None else 0

    def reset_watermark(self, session_id: str, seq: int = 0) -> int:
        """把水位直接设成 seq（可降）；清空会话/导入历史时使用。

        与 set_watermark 的区别：后者只增不减（防止并发任务把水位倒退），
        这里必须能降到 0，否则清空会话后新消息永远不会被提取。
        """
        if not session_id:
            raise ValueError("session_id 不能为空")
        value = max(0, int(seq or 0))
        now = utc_now()
        with self.db.transaction() as conn:
            conn.execute(
                "INSERT INTO session_memory_state(session_id, last_extracted_seq, updated_at) "
                "VALUES (?, ?, ?) ON CONFLICT(session_id) DO UPDATE SET "
                "last_extracted_seq = excluded.last_extracted_seq, "
                "updated_at = excluded.updated_at",
                (session_id, value, now),
            )
        return value

    def count_user_messages_since_watermark(self, session_id: str) -> int:
        if not session_id:
            return 0
        watermark = self.get_watermark(session_id)
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM messages WHERE session_id = ? "
                "AND role = 'user' AND status = 'complete' AND seq > ?",
                (session_id, watermark),
            ).fetchone()
        return int(row[0] or 0) if row is not None else 0

    def latest_message_seq(self, session_id: str) -> int:
        if not session_id:
            return 0
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT COALESCE(MAX(seq), 0) FROM messages WHERE session_id = ?",
                (session_id,),
            ).fetchone()
        return int(row[0] or 0) if row is not None else 0


def _job_row(row: sqlite3.Row) -> Dict[str, Any]:
    return {
        "id": str(row["id"]),
        "kind": str(row["kind"]),
        "status": str(row["status"]),
        "priority": int(row["priority"] or 0),
        "session_id": str(row["session_id"]) if row["session_id"] else None,
        "dedupe_key": str(row["dedupe_key"] or ""),
        "payload": json_loads(row["payload_json"], {}),
        "result": json_loads(row["result_json"], {}),
        "attempts": int(row["attempts"] or 0),
        "max_attempts": int(row["max_attempts"] or 0),
        "worker_id": str(row["worker_id"] or ""),
        "last_error": str(row["last_error"] or ""),
        "available_at": str(row["available_at"]),
        "lease_until": str(row["lease_until"]) if row["lease_until"] else None,
        "created_at": str(row["created_at"]),
        "updated_at": str(row["updated_at"]),
        "completed_at": str(row["completed_at"]) if row["completed_at"] else None,
    }


__all__ = [
    "ACTIVE_STATUSES",
    "JOB_CANCELLED",
    "JOB_DONE",
    "JOB_FAILED",
    "JOB_QUEUED",
    "JOB_RUNNING",
    "JOB_STATUSES",
    "MemoryJobQueue",
]
