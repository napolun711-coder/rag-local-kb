"""会话/消息存储（SQLite）。

职责边界：
- 只做持久化与查询，不做记忆识别、打分、检索排序；
- 不做网络调用；
- 消息正文在落盘前做保守凭据脱敏；
- 软删除只改 status；彻底删除先删消息（触发 FTS 删除）再删会话，并写墓碑。
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timedelta, timezone
import shutil
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from app.features.memory.db import MemoryDatabase
from app.features.memory.jobs import MemoryJobQueue
from app.features.memory.memories import MemoryRepository
from app.features.memory.redact import redact_text
from app.features.memory.tombstones import TombstoneLog
from app.features.memory.util import (
    json_dumps,
    json_loads,
    new_id,
    title_from_text,
    utc_now,
    value_to_iso,
)

logger = logging.getLogger(__name__)

MAX_SESSION_TITLE = 120
MAX_IMPORT_SESSIONS = 100
MAX_IMPORT_MESSAGES = 50_000
MAX_LIST_LIMIT = 500


class SessionNotFound(Exception):
    """会话不存在或已被软删除。"""


class MemoryStore:
    def __init__(self, db: Optional[MemoryDatabase] = None) -> None:
        self.db = db or MemoryDatabase()
        self.tombstones = TombstoneLog(self.db.path.parent / "deletions.jsonl")
        self.memories = MemoryRepository(self.db, self.tombstones)
        self.jobs = MemoryJobQueue(self.db)

    def initialize(self) -> None:
        self.db.initialize()

    # ============================================================
    # topic / topic（topics）
    # ============================================================

    def create_topic(
        self,
        *,
        name: str,
        kind: str = "topic",
        description: str = "",
        parent_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        clean_name = str(name or "").strip()[:80]
        if not clean_name:
            raise ValueError("topic名称不能为空")
        clean_kind = str(kind or "topic").strip().lower()
        if clean_kind not in ("project", "topic", "person", "other"):
            clean_kind = "topic"
        clean_parent = str(parent_id or "").strip() or None
        now = utc_now()
        topic_id = new_id("t")
        try:
            with self.db.transaction() as conn:
                if clean_parent and not conn.execute(
                    "SELECT 1 FROM topics WHERE id = ?", (clean_parent,)
                ).fetchone():
                    raise ValueError("上级topic不存在")
                conn.execute(
                    "INSERT INTO topics(id, name, aliases_json, kind, description,"
                    " status, parent_id, created_at, updated_at, meta_json)"
                    " VALUES (?, ?, '[]', ?, ?, 'active', ?, ?, ?, '{}')",
                    (
                        topic_id,
                        clean_name,
                        clean_kind,
                        str(description or "").strip()[:500],
                        clean_parent,
                        now,
                        now,
                    ),
                )
        except sqlite3.IntegrityError as exc:
            raise ValueError("同名topic已存在") from exc
        return self.get_topic(topic_id) or {}

    def list_topics(self, *, status: str = "active", limit: int = 200) -> List[Dict[str, Any]]:
        size = max(1, min(int(limit or 200), 500))
        wanted = str(status or "active").strip().lower()
        with self.db.connect() as conn:
            if wanted in ("active", "archived"):
                rows = conn.execute(
                    "SELECT * FROM topics WHERE status = ? ORDER BY updated_at DESC LIMIT ?",
                    (wanted, size),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM topics ORDER BY updated_at DESC LIMIT ?", (size,)
                ).fetchall()
        return [_topic_row(row) for row in rows]

    def get_topic(self, topic_id: str) -> Optional[Dict[str, Any]]:
        if not topic_id:
            return None
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM topics WHERE id = ?", (str(topic_id),)
            ).fetchone()
        return _topic_row(row) if row else None

    def update_topic(
        self,
        topic_id: str,
        *,
        name: Optional[str] = None,
        description: Optional[str] = None,
        status: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        fields: List[str] = []
        values: List[Any] = []
        if name is not None:
            clean_name = str(name).strip()[:80]
            if not clean_name:
                raise ValueError("topic名称不能为空")
            fields.append("name = ?")
            values.append(clean_name)
        if description is not None:
            fields.append("description = ?")
            values.append(str(description).strip()[:500])
        if status is not None:
            wanted = str(status).strip().lower()
            if wanted not in ("active", "archived"):
                raise ValueError("status 只能是 active / archived")
            fields.append("status = ?")
            values.append(wanted)
        if not fields:
            return self.get_topic(topic_id)
        fields.append("updated_at = ?")
        values.append(utc_now())
        values.append(str(topic_id))
        try:
            with self.db.transaction() as conn:
                cursor = conn.execute(
                    f"UPDATE topics SET {', '.join(fields)} WHERE id = ?", tuple(values)
                )
                if cursor.rowcount == 0:
                    return None
        except sqlite3.IntegrityError as exc:
            raise ValueError("同名topic已存在") from exc
        return self.get_topic(topic_id)

    # ============================================================
    # 文档 ↔ topic（）
    # ============================================================

    def set_document_topic(
        self,
        file_path: str,
        topic_id: str,
        *,
        name: str = "",
        file_id: str = "",
    ) -> Dict[str, Any]:
        """给文档打topic标签；同一文档重复打标签会覆盖旧归属。"""
        key = document_key(file_path)
        if not key:
            raise ValueError("文档名不能为空")
        wanted_topic = str(topic_id or "").strip()
        if not wanted_topic:
            raise ValueError("topic_id 不能为空")
        if self.get_topic(wanted_topic) is None:
            raise ValueError("topic不存在")
        now = utc_now()
        with self.db.transaction() as conn:
            conn.execute(
                """
                INSERT INTO document_topics(
                    document_key, file_path, topic_id, document_name, file_id,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(document_key) DO UPDATE SET
                    file_path = excluded.file_path,
                    topic_id = excluded.topic_id,
                    document_name = excluded.document_name,
                    file_id = excluded.file_id,
                    updated_at = excluded.updated_at
                """,
                (
                    key,
                    str(file_path or "").strip(),
                    wanted_topic,
                    str(name or "").strip()[:200],
                    str(file_id or "").strip()[:128],
                    now,
                    now,
                ),
            )
        return self.document_topic(file_path) or {}

    def document_topic(self, file_path: str) -> Optional[Dict[str, Any]]:
        key = document_key(file_path)
        if not key:
            return None
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM document_topics WHERE document_key = ?", (key,)
            ).fetchone()
        return _document_topic_row(row) if row else None

    def document_topics(self) -> List[Dict[str, Any]]:
        with self.db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM document_topics ORDER BY updated_at DESC"
            ).fetchall()
        return [_document_topic_row(row) for row in rows]

    def document_topic_map(self) -> Dict[str, str]:
        """document_key → topic_id（文件列表批量标注用，避免逐行查询）。"""
        with self.db.connect() as conn:
            rows = conn.execute(
                "SELECT document_key, topic_id FROM document_topics"
            ).fetchall()
        return {str(row["document_key"]): str(row["topic_id"]) for row in rows}

    def documents_for_topic(self, topic_id: str) -> List[Dict[str, Any]]:
        wanted = str(topic_id or "").strip()
        if not wanted:
            return []
        with self.db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM document_topics WHERE topic_id = ? "
                "ORDER BY updated_at DESC",
                (wanted,),
            ).fetchall()
        return [_document_topic_row(row) for row in rows]

    def clear_document_topic(self, file_path: str) -> bool:
        key = document_key(file_path)
        if not key:
            return False
        with self.db.transaction() as conn:
            cursor = conn.execute(
                "DELETE FROM document_topics WHERE document_key = ?", (key,)
            )
        return cursor.rowcount > 0

    # ============================================================
    # 会话
    # ============================================================

    def create_session(
        self,
        *,
        title: str = "",
        topic_id: Optional[str] = None,
        client_session_id: Optional[str] = None,
        created_at: Optional[str] = None,
        updated_at: Optional[str] = None,
    ) -> Dict[str, Any]:
        now = utc_now()
        created = created_at or now
        updated = updated_at or created
        clean_title = str(title or "").strip()[:MAX_SESSION_TITLE]

        with self.db.transaction() as conn:
            if client_session_id:
                existing = conn.execute(
                    "SELECT * FROM sessions WHERE client_session_id = ?",
                    (str(client_session_id),),
                ).fetchone()
                if existing is not None:
                    if str(existing["status"]) != "deleted":
                        return _session_row(existing)
                    # 旧会话已删除：让出唯一键，允许同一旧 ID 重新导入/新建。
                    conn.execute(
                        "UPDATE sessions SET client_session_id = NULL WHERE id = ?",
                        (existing["id"],),
                    )

            clean_topic = str(topic_id or "").strip() or None
            if clean_topic and not conn.execute(
                "SELECT 1 FROM topics WHERE id = ?", (clean_topic,)
            ).fetchone():
                clean_topic = None

            session_id = new_id("s")
            conn.execute(
                """
                INSERT INTO sessions(
                    id, title, topic_id, client_session_id, status,
                    message_count, created_at, updated_at, last_message_at, meta_json
                ) VALUES (?, ?, ?, ?, 'active', 0, ?, ?, NULL, '{}')
                """,
                (
                    session_id,
                    clean_title,
                    clean_topic,
                    str(client_session_id) if client_session_id else None,
                    created,
                    updated,
                ),
            )
            row = conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
            return _session_row(row)

    def get_session(
        self, session_id: str, *, include_deleted: bool = False
    ) -> Optional[Dict[str, Any]]:
        if not session_id:
            return None
        with self.db.connect() as conn:
            row = conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
        if row is None:
            return None
        if not include_deleted and str(row["status"]) == "deleted":
            return None
        return _session_row(row)

    def list_sessions(
        self, *, limit: int = 50, offset: int = 0, status: str = "active"
    ) -> List[Dict[str, Any]]:
        try:
            size = max(1, min(MAX_LIST_LIMIT, int(limit)))
        except (TypeError, ValueError):
            size = 50
        try:
            start = max(0, int(offset))
        except (TypeError, ValueError):
            start = 0
        wanted = str(status or "active").strip().lower()
        with self.db.connect() as conn:
            if wanted in ("active", "archived", "deleted"):
                rows = conn.execute(
                    "SELECT * FROM sessions WHERE status = ? "
                    "ORDER BY updated_at DESC LIMIT ? OFFSET ?",
                    (wanted, size, start),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM sessions ORDER BY updated_at DESC LIMIT ? OFFSET ?",
                    (size, start),
                ).fetchall()
        return [_session_row(row) for row in rows]

    def update_session(
        self,
        session_id: str,
        *,
        title: Optional[str] = None,
        topic_id: Optional[str] = None,
        status: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        fields: List[str] = []
        values: List[Any] = []
        if title is not None:
            fields.append("title = ?")
            values.append(str(title).strip()[:MAX_SESSION_TITLE])
        if topic_id is not None:
            clean_topic = str(topic_id).strip() or None
            if clean_topic:
                with self.db.connect() as conn:
                    exists = conn.execute(
                        "SELECT 1 FROM topics WHERE id = ?", (clean_topic,)
                    ).fetchone()
                if not exists:
                    raise ValueError("topic_id 不存在")
            fields.append("topic_id = ?")
            values.append(clean_topic)
        if status is not None:
            wanted = str(status).strip().lower()
            if wanted not in ("active", "archived"):
                raise ValueError("status 只能是 active / archived")
            fields.append("status = ?")
            values.append(wanted)

        if not fields:
            return self.get_session(session_id)

        fields.append("updated_at = ?")
        values.append(utc_now())
        values.append(session_id)
        with self.db.transaction() as conn:
            conn.execute(
                f"UPDATE sessions SET {', '.join(fields)} WHERE id = ?", tuple(values)
            )
            row = conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
        return _session_row(row) if row is not None else None

    def update_session_meta(
        self, session_id: str, patch: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """合并写入会话元数据（约束/摘要/任务进度，不建新表）。"""
        if not isinstance(patch, dict) or not patch:
            return self.get_session(session_id)
        with self.db.transaction() as conn:
            row = conn.execute(
                "SELECT * FROM sessions WHERE id = ?", (session_id,)
            ).fetchone()
            if row is None or str(row["status"]) == "deleted":
                return None
            current = json_loads(row["meta_json"], {})
            if not isinstance(current, dict):
                current = {}
            current.update(patch)
            conn.execute(
                "UPDATE sessions SET meta_json = ?, updated_at = ? WHERE id = ?",
                (json_dumps(current), utc_now(), session_id),
            )
            updated = conn.execute(
                "SELECT * FROM sessions WHERE id = ?", (session_id,)
            ).fetchone()
        return _session_row(updated) if updated is not None else None

    def touch_session(self, session_id: str) -> bool:
        with self.db.transaction() as conn:
            cursor = conn.execute(
                "UPDATE sessions SET updated_at = ? WHERE id = ?",
                (utc_now(), session_id),
            )
            return cursor.rowcount > 0

    def delete_session(self, session_id: str, *, purge: bool = False) -> bool:
        with self.db.transaction() as conn:
            row = conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
            if row is None:
                return False
            message_ids = [
                str(item[0])
                for item in conn.execute(
                    "SELECT id FROM messages WHERE session_id = ?", (session_id,)
                ).fetchall()
            ]
            if purge:
                # 会话彻底删除：先清掉这些消息产生的来源 quote / 事件正文，
                # 再删消息与会话；否则“彻底删除”后 quote 仍留在 memory_sources。
                affected = [
                    str(item[0])
                    for item in conn.execute(
                        "SELECT DISTINCT memory_id FROM memory_sources "
                        "WHERE message_id IN (SELECT id FROM messages WHERE session_id = ?)",
                        (session_id,),
                    ).fetchall()
                ]
                conn.execute(
                    "DELETE FROM memory_sources WHERE message_id IN "
                    "(SELECT id FROM messages WHERE session_id = ?)",
                    (session_id,),
                )
                for memory_id in affected:
                    conn.execute(
                        "UPDATE memory_events SET before_json = NULL, after_json = NULL "
                        "WHERE memory_id = ?",
                        (memory_id,),
                    )
                # 再显式删消息，确保 FTS 触发器逐行执行；最后删会话。
                conn.execute("DELETE FROM messages WHERE session_id = ?", (session_id,))
                conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
            else:
                # 软删除保留消息行（可审计/将来可恢复），但必须把 FTS 也清掉：
                # 用户的删除操作要覆盖后续检索路径，不能只靠查询侧 join 过滤。
                _remove_session_fts(conn, session_id)
                conn.execute(
                    "UPDATE sessions SET status = 'deleted', deleted_at = ?, updated_at = ? "
                    "WHERE id = ?",
                    (utc_now(), utc_now(), session_id),
                )
        try:
            self.jobs.cancel_for_session(session_id)
        except Exception as exc:  # noqa: BLE001 - 取消任务失败不能反噬删除
            logger.warning("取消会话任务失败（忽略）：%s", type(exc).__name__)
        self.tombstones.append(
            kind="session",
            item_id=session_id,
            mode="purge" if purge else "soft",
            message_ids=message_ids,
        )
        return True

    def export_memories(self) -> Dict[str, Any]:
        """导出记忆、来源、事件，以及topic与文档标签（格式 v2）。"""
        items: List[Dict[str, Any]] = []
        offset = 0
        while True:
            batch = self.memories.list(include_deleted=True, limit=500, offset=offset)
            if not batch:
                break
            for memory in batch:
                item = dict(memory)
                item["sources"] = self.memories.sources(str(memory["id"]))
                item["events"] = self.memories.events(str(memory["id"]), limit=500)
                items.append(item)
            offset += len(batch)
        return {
            "version": 2,
            "exported_at": utc_now(),
            "items": items,
            "topics": self.list_topics(status=""),
            "document_topics": self.document_topics(),
        }

    def import_topics(self, items: Any) -> Dict[str, int]:
        """导入topic；按 id 幂等，不覆盖已有记录。"""
        imported = skipped = errors = 0
        if not isinstance(items, list):
            return {"imported": 0, "skipped": 0, "errors": 0}
        pending_parents: List[Any] = []
        with self.db.transaction() as conn:
            for raw in items:
                if not isinstance(raw, dict):
                    errors += 1
                    continue
                topic_id = str(raw.get("id") or "").strip()
                name = str(raw.get("name") or "").strip()[:80]
                if not topic_id or not name:
                    errors += 1
                    continue
                if conn.execute(
                    "SELECT 1 FROM topics WHERE id = ?", (topic_id,)
                ).fetchone():
                    skipped += 1
                    continue
                kind = str(raw.get("kind") or "topic").strip().lower()
                if kind not in ("project", "topic", "person", "other"):
                    kind = "topic"
                status = str(raw.get("status") or "active").strip().lower()
                if status not in ("active", "archived"):
                    status = "active"
                parent = str(raw.get("parent_id") or "").strip() or None
                now = utc_now()
                created = str(raw.get("created_at") or "").strip() or now
                updated = str(raw.get("updated_at") or "").strip() or now
                meta = raw.get("meta") if isinstance(raw.get("meta"), dict) else {}
                aliases = raw.get("aliases") if isinstance(raw.get("aliases"), list) else []
                conn.execute(
                    "INSERT INTO topics("
                    " id, name, aliases_json, kind, description, status, parent_id,"
                    " created_at, updated_at, meta_json"
                    ") VALUES (?, ?, ?, ?, ?, ?, NULL, ?, ?, ?)",
                    (
                        topic_id,
                        name,
                        json_dumps(aliases),
                        kind,
                        str(raw.get("description") or "").strip()[:500],
                        status,
                        created,
                        updated,
                        json_dumps(meta),
                    ),
                )
                if parent:
                    pending_parents.append((topic_id, parent))
                imported += 1
            for topic_id, parent in pending_parents:
                if conn.execute(
                    "SELECT 1 FROM topics WHERE id = ?", (parent,)
                ).fetchone():
                    conn.execute(
                        "UPDATE topics SET parent_id = ? WHERE id = ?",
                        (parent, topic_id),
                    )
        return {"imported": imported, "skipped": skipped, "errors": errors}

    def import_document_topics(self, items: Any) -> Dict[str, int]:
        """导入文档 ↔ topic标签；已存在的文档标签不覆盖。"""
        imported = skipped = errors = 0
        if not isinstance(items, list):
            return {"imported": 0, "skipped": 0, "errors": 0}
        for raw in items:
            if not isinstance(raw, dict):
                errors += 1
                continue
            file_path = str(raw.get("file_path") or "").strip()
            topic_id = str(raw.get("topic_id") or "").strip()
            if not file_path or not topic_id:
                errors += 1
                continue
            if self.document_topic(file_path) is not None:
                skipped += 1
                continue
            try:
                self.set_document_topic(
                    file_path,
                    topic_id,
                    name=str(raw.get("document_name") or ""),
                    file_id=str(raw.get("file_id") or ""),
                )
                imported += 1
            except ValueError:
                errors += 1
        return {"imported": imported, "skipped": skipped, "errors": errors}

    def import_memories(self, payload: Any) -> Dict[str, int]:
        """导入记忆、topic与文档标签；按 id 幂等，不覆盖已有记录。"""
        if not isinstance(payload, dict):
            raise ValueError("导入内容必须是 JSON 对象")
        items = payload.get("items")
        if not isinstance(items, list):
            raise ValueError("导入内容缺少 items 数组")
        topic_result = self.import_topics(payload.get("topics"))
        doc_result = self.import_document_topics(payload.get("document_topics"))
        imported = 0
        skipped = 0
        errors = 0
        for raw in items:
            if not isinstance(raw, dict):
                errors += 1
                continue
            memory_id = str(raw.get("id") or "")
            content = str(raw.get("content") or "").strip()[:300]
            subject = str(raw.get("subject") or "").strip()[:40]
            if not content or not subject:
                errors += 1
                continue
            with self.db.connect() as conn:
                exists = None
                if memory_id:
                    exists = conn.execute(
                        "SELECT id FROM memories WHERE id = ?", (memory_id,)
                    ).fetchone()
                if exists is None:
                    exists = conn.execute(
                        "SELECT id FROM memories WHERE subject = ? AND content = ? LIMIT 1",
                        (subject, content),
                    ).fetchone()
            if exists is not None:
                skipped += 1
                continue
            source = None
            sources = raw.get("sources")
            if isinstance(sources, list) and sources:
                first = sources[0] if isinstance(sources[0], dict) else {}
                quote = str(first.get("quote") or "")[:500]
                if quote:
                    message_id = str(first.get("message_id") or "") or None
                    session_id = str(first.get("session_id") or "") or None
                    with self.db.connect() as conn:
                        if message_id and not conn.execute(
                            "SELECT 1 FROM messages WHERE id = ?", (message_id,)
                        ).fetchone():
                            message_id = None
                        if session_id and not conn.execute(
                            "SELECT 1 FROM sessions WHERE id = ?", (session_id,)
                        ).fetchone():
                            session_id = None
                    source = {
                        "message_id": message_id,
                        "session_id": session_id,
                        "quote": quote,
                    }
            status = str(raw.get("status") or "pending")
            if status not in ("active", "pending", "conflicted"):
                status = "pending"
            sensitivity = str(raw.get("sensitivity") or "normal")
            if sensitivity == "secret":
                skipped += 1
                continue
            try:
                self.memories.create(
                    content=content,
                    subject=subject,
                    kind=str(raw.get("kind") or "fact"),
                    scope=str(raw.get("scope") or "global"),
                    topic_id=str(raw.get("topic_id") or "") or None,
                    source_type=str(raw.get("source_type") or "import"),
                    sensitivity=sensitivity,
                    confidence=float(raw.get("confidence") or 0.5),
                    importance=int(raw.get("importance") or 3),
                    status=status,
                    meta=raw.get("meta") if isinstance(raw.get("meta"), dict) else {},
                    source=source,
                    actor="import",
                )
                imported += 1
            except Exception:  # noqa: BLE001 - 单条导入失败不阻断其它条目
                errors += 1
        return {
            "imported": imported,
            "skipped": skipped,
            "errors": errors,
            "topics_imported": topic_result["imported"],
            "topics_skipped": topic_result["skipped"],
            "topics_errors": topic_result["errors"],
            "document_topics_imported": doc_result["imported"],
            "document_topics_skipped": doc_result["skipped"],
            "document_topics_errors": doc_result["errors"],
        }

    def clear_all(self, *, purge_backups: bool = False, backup_root: Optional[Path] = None) -> Dict[str, int]:
        """清空会话/消息/记忆/来源/事件/任务/topics 与 FTS。"""
        counts: Dict[str, int] = {}
        with self.db.transaction() as conn:
            for table in (
                "memory_supervisor_findings",
                "memory_supervisor_meta",
                "memory_sources",
                "memory_events",
                "memory_suppressions",
                "memories",
                "memory_jobs",
                "session_memory_state",
                "messages",
                "sessions",
                "document_topics",
                "topics",
            ):
                try:
                    counts[table] = int(
                        conn.execute('SELECT COUNT(*) FROM "%s"' % table).fetchone()[0]
                    )
                    conn.execute('DELETE FROM "%s"' % table)
                except Exception:  # noqa: BLE001 - 旧库缺表时跳过
                    counts[table] = 0
            for table in ("messages_fts", "memories_fts"):
                try:
                    conn.execute(
                        "INSERT INTO %s(%s) VALUES('rebuild')" % (table, table)
                    )
                except Exception:  # noqa: BLE001
                    pass
        self.tombstones.append(kind="all", item_id="all", mode="purge")
        if purge_backups:
            root = Path(backup_root) if backup_root else (self.db.path.parent.parent / "backups")
            if root.is_dir():
                for child in root.iterdir():
                    try:
                        if child.is_dir():
                            shutil.rmtree(child, ignore_errors=True)
                        else:
                            child.unlink(missing_ok=True)
                    except OSError:
                        pass
        return counts

    def clear_session_messages(self, session_id: str) -> int:
        with self.db.transaction() as conn:
            row = conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
            if row is None or str(row["status"]) == "deleted":
                raise SessionNotFound(session_id)
            message_ids = [
                str(item[0])
                for item in conn.execute(
                    "SELECT id FROM messages WHERE session_id = ?", (session_id,)
                ).fetchall()
            ]
            conn.execute("DELETE FROM messages WHERE session_id = ?", (session_id,))
            now = utc_now()
            conn.execute(
                "UPDATE sessions SET message_count = 0, updated_at = ?, last_message_at = NULL "
                "WHERE id = ?",
                (now, session_id),
            )
        # 清空后新消息从 seq=1 重新开始：水位必须归零，否则永远不会再提取。
        try:
            self.jobs.reset_watermark(session_id, 0)
        except Exception as exc:  # noqa: BLE001 - 水位失败不能反噬清空
            logger.warning("重置提取水位失败（忽略）：%s", type(exc).__name__)
        try:
            self.jobs.cancel_for_session(session_id)
        except Exception as exc:  # noqa: BLE001 - 取消任务失败不能反噬清空
            logger.warning("取消会话任务失败（忽略）：%s", type(exc).__name__)
        if message_ids:
            self.tombstones.append(
                kind="session_messages",
                item_id=session_id,
                mode="purge",
                message_ids=message_ids,
            )
        return len(message_ids)

    # ============================================================
    # 消息
    # ============================================================

    def add_user_message(
        self,
        session_id: str,
        content: str,
        *,
        client_message_id: Optional[str] = None,
        created_at: Optional[str] = None,
        source: str = "user",
        meta: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        with self.db.transaction() as conn:
            session = conn.execute(
                "SELECT * FROM sessions WHERE id = ?", (session_id,)
            ).fetchone()
            if session is None or str(session["status"]) == "deleted":
                raise SessionNotFound(session_id)

            if client_message_id:
                existing = conn.execute(
                    "SELECT * FROM messages WHERE session_id = ? AND client_message_id = ?",
                    (session_id, str(client_message_id)),
                ).fetchone()
                if existing is not None:
                    return _message_row(existing)

            now = created_at or utc_now()
            seq = _next_seq(conn, session_id)
            clean_content, redaction = redact_text(content)
            merged_meta = dict(meta or {})
            if redaction.get("redacted"):
                merged_meta["redaction"] = redaction
            message_id = _insert_message(
                conn,
                session_id=session_id,
                seq=seq,
                role="user",
                content=clean_content,
                status="complete",
                source=source,
                model="",
                request_id=None,
                client_message_id=str(client_message_id) if client_message_id else None,
                reply_to_id=None,
                created_at=now,
                completed_at=now,
                references=[],
                meta=merged_meta,
            )
            _bump_session(conn, session_id, at=now)
            # 首条用户消息生成标题（与前端规则一致）。
            if not str(session["title"] or "").strip():
                conn.execute(
                    "UPDATE sessions SET title = ? WHERE id = ?",
                    (title_from_text(clean_content), session_id),
                )
            row = conn.execute("SELECT * FROM messages WHERE id = ?", (message_id,)).fetchone()
            return _message_row(row)

    def add_assistant_message(
        self,
        session_id: str,
        *,
        content: str = "",
        status: str = "streaming",
        source: str = "",
        model: str = "",
        request_id: Optional[str] = None,
        reply_to_id: Optional[str] = None,
        references: Optional[Iterable[Dict[str, Any]]] = None,
        meta: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        clean_status = str(status or "streaming").strip().lower()
        if clean_status not in ("complete", "streaming", "partial", "error"):
            clean_status = "streaming"

        with self.db.transaction() as conn:
            session = conn.execute(
                "SELECT * FROM sessions WHERE id = ?", (session_id,)
            ).fetchone()
            if session is None or str(session["status"]) == "deleted":
                raise SessionNotFound(session_id)

            # 同一次请求的流式/部分结果：复用，避免 /chat/stream 失败后前端走 /chat
            # 回退时产生两条助手消息。
            if request_id:
                existing = conn.execute(
                    "SELECT * FROM messages WHERE session_id = ? AND role = 'assistant' "
                    "AND request_id = ? AND status IN ('streaming','partial') "
                    "ORDER BY seq DESC LIMIT 1",
                    (session_id, str(request_id)),
                ).fetchone()
                if existing is not None:
                    return _message_row(existing)

            now = utc_now()
            seq = _next_seq(conn, session_id)
            clean_content, redaction = redact_text(content)
            merged_meta = dict(meta or {})
            if redaction.get("redacted"):
                merged_meta["redaction"] = redaction
            message_id = _insert_message(
                conn,
                session_id=session_id,
                seq=seq,
                role="assistant",
                content=clean_content,
                status=clean_status,
                source=source,
                model=model,
                request_id=str(request_id) if request_id else None,
                client_message_id=None,
                reply_to_id=str(reply_to_id) if reply_to_id else None,
                created_at=now,
                completed_at=now if clean_status in ("complete", "partial", "error") else None,
                references=list(references or []),
                meta=merged_meta,
            )
            _bump_session(conn, session_id, at=now)
            row = conn.execute("SELECT * FROM messages WHERE id = ?", (message_id,)).fetchone()
            return _message_row(row)

    def update_message(
        self,
        message_id: str,
        *,
        content: Optional[str] = None,
        status: Optional[str] = None,
        references: Optional[Iterable[Dict[str, Any]]] = None,
        meta: Optional[Dict[str, Any]] = None,
        model: Optional[str] = None,
        completed: bool = False,
    ) -> Optional[Dict[str, Any]]:
        fields: List[str] = []
        values: List[Any] = []
        merged_meta: Optional[Dict[str, Any]] = None

        if content is not None:
            clean_content, redaction = redact_text(content)
            fields.append("content = ?")
            values.append(clean_content)
            if redaction.get("redacted"):
                merged_meta = {"redaction": redaction}
        if status is not None:
            clean_status = str(status).strip().lower()
            if clean_status not in ("complete", "streaming", "partial", "error"):
                raise ValueError("status 非法")
            fields.append("status = ?")
            values.append(clean_status)
        if references is not None:
            fields.append("references_json = ?")
            values.append(json_dumps(list(references)))
        if model is not None:
            fields.append("model = ?")
            values.append(str(model))

        now = utc_now()
        fields.append("updated_at = ?")
        values.append(now)
        if completed:
            fields.append("completed_at = ?")
            values.append(now)

        values.append(message_id)
        with self.db.transaction() as conn:
            current = conn.execute(
                "SELECT meta_json FROM messages WHERE id = ?", (message_id,)
            ).fetchone()
            if current is None:
                return None
            if meta or merged_meta:
                existing = json_loads(current["meta_json"], {})
                if not isinstance(existing, dict):
                    existing = {}
                existing.update(meta or {})
                if merged_meta:
                    existing.update(merged_meta)
                fields.append("meta_json = ?")
                values.insert(len(values) - 1, json_dumps(existing))
            conn.execute(
                f"UPDATE messages SET {', '.join(fields)} WHERE id = ?", tuple(values)
            )
            row = conn.execute("SELECT * FROM messages WHERE id = ?", (message_id,)).fetchone()
        return _message_row(row) if row is not None else None

    def get_message(self, message_id: str) -> Optional[Dict[str, Any]]:
        with self.db.connect() as conn:
            row = conn.execute("SELECT * FROM messages WHERE id = ?", (message_id,)).fetchone()
        return _message_row(row) if row is not None else None

    def list_messages(
        self,
        session_id: str,
        *,
        limit: int = 200,
        before_seq: Optional[int] = None,
        include_deleted: bool = False,
    ) -> List[Dict[str, Any]]:
        with self.db.connect() as conn:
            session = conn.execute(
                "SELECT * FROM sessions WHERE id = ?", (session_id,)
            ).fetchone()
            if session is None:
                raise SessionNotFound(session_id)
            if not include_deleted and str(session["status"]) == "deleted":
                raise SessionNotFound(session_id)

            try:
                size = max(1, min(1000, int(limit)))
            except (TypeError, ValueError):
                size = 200
            if before_seq is None:
                rows = conn.execute(
                    "SELECT * FROM messages WHERE session_id = ? "
                    "ORDER BY seq DESC LIMIT ?",
                    (session_id, size),
                ).fetchall()
            else:
                try:
                    boundary = int(before_seq)
                except (TypeError, ValueError):
                    boundary = 0
                rows = conn.execute(
                    "SELECT * FROM messages WHERE session_id = ? AND seq < ? "
                    "ORDER BY seq DESC LIMIT ?",
                    (session_id, boundary, size),
                ).fetchall()
        return [_message_row(row) for row in reversed(rows)]

    def search_history(
        self,
        query: str,
        *,
        topic_id: str = "",
        limit: int = 10,
    ) -> List[Dict[str, Any]]:
        """-mini：只读检索历史消息（FTS5 trigram，失败回落 LIKE）。"""
        text = " ".join(str(query or "").split())
        if len(text) < 2:
            return []
        try:
            size = max(1, min(20, int(limit or 10)))
        except (TypeError, ValueError):
            size = 10
        clean_topic = str(topic_id or "").strip()
        params: List[Any] = []
        topic_sql = ""
        if clean_topic:
            topic_sql = " AND s.topic_id = ?"
            params.append(clean_topic)

        sql_head = (
            "SELECT m.id, m.session_id, m.role, m.content, m.created_at, "
            "s.title AS session_title, s.topic_id AS session_topic_id, "
            "t.name AS topic_name "
            "FROM messages_fts f "
            "JOIN messages m ON m.rowid = f.rowid "
            "JOIN sessions s ON s.id = m.session_id "
            "LEFT JOIN topics t ON t.id = s.topic_id "
            "WHERE messages_fts MATCH ? AND m.status = 'complete' "
            "AND m.fts_indexed = 1 AND m.role IN ('user','assistant') "
            "AND s.status != 'deleted' "
            "AND NOT EXISTS (SELECT 1 FROM memory_suppressions ms "
            "WHERE ms.message_id = m.id)"
        )
        rows = []
        with self.db.connect() as conn:
            if getattr(self.db, "fts_available", False):
                fts_query = '"' + text.replace('"', '""') + '"'
                try:
                    rows = conn.execute(
                        sql_head
                        + topic_sql
                        + " ORDER BY bm25(messages_fts) LIMIT ?",
                        [fts_query, *params, size],
                    ).fetchall()
                except sqlite3.OperationalError:
                    rows = []
            if not rows:
                like_sql = (
                    "SELECT m.id, m.session_id, m.role, m.content, m.created_at, "
                    "s.title AS session_title, s.topic_id AS session_topic_id, "
                    "t.name AS topic_name "
                    "FROM messages m "
                    "JOIN sessions s ON s.id = m.session_id "
                    "LEFT JOIN topics t ON t.id = s.topic_id "
                    "WHERE m.content LIKE ? AND m.status = 'complete' "
                    "AND m.fts_indexed = 1 AND m.role IN ('user','assistant') "
                    "AND s.status != 'deleted' "
                    "AND NOT EXISTS (SELECT 1 FROM memory_suppressions ms "
                    "WHERE ms.message_id = m.id)"
                )
                rows = conn.execute(
                    like_sql + topic_sql + " ORDER BY m.created_at DESC LIMIT ?",
                    ["%" + text + "%", *params, size],
                ).fetchall()
        return [
            {
                "message_id": str(row["id"]),
                "session_id": str(row["session_id"]),
                "session_title": str(row["session_title"] or ""),
                "topic_id": str(row["session_topic_id"]) if row["session_topic_id"] else None,
                "topic_name": str(row["topic_name"]) if row["topic_name"] else None,
                "role": str(row["role"]),
                "snippet": _snippet(row["content"], text),
                "created_at": str(row["created_at"]),
            }
            for row in rows
        ]

    def count_messages(self, session_id: str) -> int:
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM messages WHERE session_id = ?", (session_id,)
            ).fetchone()
        return int(row[0] or 0) if row else 0

    def list_user_messages_after(
        self,
        session_id: str,
        after_seq: int,
        *,
        limit: int = 20,
    ) -> List[Dict[str, Any]]:
        """按 seq 升序取水位之后的完整用户消息（ 提取输入）。"""
        if not session_id:
            return []
        try:
            size = max(1, min(500, int(limit or 20)))
        except (TypeError, ValueError):
            size = 20
        try:
            boundary = max(0, int(after_seq or 0))
        except (TypeError, ValueError):
            boundary = 0
        with self.db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM messages WHERE session_id = ? "
                "AND role = 'user' AND status = 'complete' AND seq > ? "
                "ORDER BY seq ASC LIMIT ?",
                (session_id, boundary, size),
            ).fetchall()
        return [_message_row(row) for row in rows]

    def export_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        session = self.get_session(session_id, include_deleted=True)
        if session is None:
            return None
        return {
            "session": session,
            "messages": self.list_messages(
                session_id, limit=1000, include_deleted=True
            ),
        }

    # ============================================================
    # 恢复 / 导入
    # ============================================================

    def repair_stale_streaming(self, *, older_than_seconds: int = 600) -> int:
        cutoff = (
            datetime.now(timezone.utc) - timedelta(seconds=max(0, int(older_than_seconds)))
        ).isoformat(timespec="milliseconds").replace("+00:00", "Z")
        now = utc_now()
        with self.db.transaction() as conn:
            cursor = conn.execute(
                "UPDATE messages SET status = 'partial', completed_at = ?, updated_at = ? "
                "WHERE status = 'streaming' AND updated_at < ?",
                (now, now, cutoff),
            )
            return int(cursor.rowcount or 0)

    def import_sessions(self, payload: Any) -> Dict[str, Any]:
        """把 localStorage 的 `{sessions:[...]}` 导入；按 client_session_id 幂等。"""
        if isinstance(payload, dict):
            items = payload.get("sessions")
        else:
            items = payload
        if not isinstance(items, list):
            raise ValueError("sessions 必须是数组")
        if len(items) > MAX_IMPORT_SESSIONS:
            raise ValueError(f"一次最多导入 {MAX_IMPORT_SESSIONS} 条会话")

        total_messages = 0
        for item in items:
            if isinstance(item, dict) and isinstance(item.get("messages"), list):
                total_messages += len(item["messages"])
        if total_messages > MAX_IMPORT_MESSAGES:
            raise ValueError(f"一次最多导入 {MAX_IMPORT_MESSAGES} 条消息")

        mapping: Dict[str, str] = {}
        imported = 0
        skipped = 0
        skipped_empty = 0
        redacted_count = 0

        with self.db.transaction() as conn:
            for index, item in enumerate(items):
                if not isinstance(item, dict):
                    skipped += 1
                    continue
                old_id = str(
                    item.get("id") or item.get("client_session_id") or ""
                ).strip()
                if not old_id:
                    skipped += 1
                    continue
                existing = conn.execute(
                    "SELECT id, status FROM sessions WHERE client_session_id = ?",
                    (old_id,),
                ).fetchone()
                if existing is not None:
                    if str(existing["status"]) == "deleted":
                        # 旧会话已软删：解除 client_session_id 映射（保留旧行做审计），
                        # 允许这次重新导入创建一条新的 active 会话；否则旧对话会从 UI 消失。
                        conn.execute(
                            "UPDATE sessions SET client_session_id = NULL WHERE id = ?",
                            (str(existing["id"]),),
                        )
                    else:
                        mapping[old_id] = str(existing["id"])
                        skipped += 1
                        continue

                messages = item.get("messages")
                if not isinstance(messages, list) or not messages:
                    skipped_empty += 1
                    continue

                created_at = value_to_iso(
                    item.get("created_at") or item.get("updated_at")
                )
                updated_at = value_to_iso(
                    item.get("updated_at") or item.get("created_at"),
                    fallback=created_at,
                )
                first_user_text = ""
                for candidate in messages:
                    if isinstance(candidate, dict) and str(
                        candidate.get("role") or ""
                    ).lower() == "user":
                        first_user_text = str(
                            candidate.get("text") or candidate.get("content") or ""
                        )
                        break
                title = str(item.get("title") or "").strip() or title_from_text(
                    first_user_text
                )

                session_id = new_id("s")
                conn.execute(
                    """
                    INSERT INTO sessions(
                        id, title, topic_id, client_session_id, status,
                        message_count, created_at, updated_at, last_message_at, meta_json
                    ) VALUES (?, ?, NULL, ?, 'active', 0, ?, ?, NULL, ?)
                    """,
                    (
                        session_id,
                        title[:MAX_SESSION_TITLE],
                        old_id,
                        created_at,
                        updated_at,
                        json_dumps({"import": True, "legacy_id": old_id}),
                    ),
                )

                seq = 0
                inserted = 0
                last_at = created_at
                for msg_index, message in enumerate(messages):
                    if not isinstance(message, dict):
                        continue
                    raw_role = str(message.get("role") or "").lower()
                    if raw_role in ("ai", "assistant"):
                        role = "assistant"
                    elif raw_role == "user":
                        role = "user"
                    else:
                        continue
                    content = str(
                        message.get("text") or message.get("content") or ""
                    )
                    if not content.strip():
                        continue
                    content, redaction = redact_text(content)
                    if redaction.get("redacted"):
                        redacted_count += 1
                    seq += 1
                    inserted += 1
                    at = value_to_iso(
                        message.get("ts") or message.get("created_at"),
                        fallback=last_at,
                    )
                    last_at = at
                    refs = message.get("references")
                    if not isinstance(refs, list):
                        refs = []
                    meta: Dict[str, Any] = {
                        "import": True,
                        "legacy_index": msg_index,
                    }
                    if redaction.get("redacted"):
                        meta["redaction"] = redaction
                    if isinstance(message.get("generation"), dict):
                        meta["generation"] = message["generation"]
                    if message.get("retry_upstream") is True:
                        meta["retry_upstream"] = True
                    if message.get("upstream_retry") is True:
                        meta["upstream_retry"] = True
                    _insert_message(
                        conn,
                        session_id=session_id,
                        seq=seq,
                        role=role,
                        content=content,
                        status="complete",
                        source="import",
                        model="",
                        request_id=None,
                        client_message_id=f"import:{old_id}:{msg_index}",
                        reply_to_id=None,
                        created_at=at,
                        completed_at=at,
                        references=refs,
                        meta=meta,
                    )

                if inserted == 0:
                    # 没有可用消息：撤销刚建的空会话，保持“空会话不进列表”的约定。
                    conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
                    skipped_empty += 1
                    continue

                conn.execute(
                    "UPDATE sessions SET message_count = ?, updated_at = ?, "
                    "last_message_at = ? WHERE id = ?",
                    (inserted, updated_at, last_at, session_id),
                )
                # 导入的是历史聊天，不是“新输入”：水位直接推到末尾，
                # 避免下一次自动提取把整段旧对话灌成记忆。
                conn.execute(
                    "INSERT INTO session_memory_state(session_id, last_extracted_seq, updated_at) "
                    "VALUES (?, ?, ?) ON CONFLICT(session_id) DO UPDATE SET "
                    "last_extracted_seq = excluded.last_extracted_seq, "
                    "updated_at = excluded.updated_at",
                    (session_id, seq, utc_now()),
                )
                mapping[old_id] = session_id
                imported += 1

        return {
            "imported": imported,
            "skipped": skipped,
            "skipped_empty": skipped_empty,
            "redacted_messages": redacted_count,
            "mapping": mapping,
        }

    # ============================================================
    # 状态
    # ============================================================

    def status(self) -> Dict[str, Any]:
        result = self.db.status()
        result["enabled"] = True
        result["tombstones_path"] = str(self.tombstones.path)
        try:
            with self.db.connect() as conn:
                result["memory_count"] = int(
                    conn.execute(
                        "SELECT COUNT(*) FROM memories WHERE status != 'deleted'"
                    ).fetchone()[0]
                )
                result["pending_memory_count"] = int(
                    conn.execute(
                        "SELECT COUNT(*) FROM memories WHERE status IN ('pending','conflicted')"
                    ).fetchone()[0]
                )
        except Exception:  # noqa: BLE001 - 旧库/ 迁移前没有 memories 表
            result["memory_count"] = 0
            result["pending_memory_count"] = 0
        result["jobs"] = self.jobs.counts()
        return result


# ============================================================
# SQL 行 → API 字典
# ============================================================

def document_key(file_path: str) -> str:
    """文档比对键：去掉目录、转小写、去首尾空白；与 gallery.key_of 一致。"""
    return Path(str(file_path or "")).name.strip().lower()


def _document_topic_row(row: sqlite3.Row) -> Dict[str, Any]:
    return {
        "document_key": str(row["document_key"]),
        "file_path": str(row["file_path"] or ""),
        "topic_id": str(row["topic_id"] or ""),
        "document_name": str(row["document_name"] or ""),
        "file_id": str(row["file_id"] or ""),
        "created_at": str(row["created_at"] or ""),
        "updated_at": str(row["updated_at"] or ""),
    }


def _topic_row(row: sqlite3.Row) -> Dict[str, Any]:
    return {
        "id": str(row["id"]),
        "name": str(row["name"] or ""),
        "aliases": json_loads(row["aliases_json"], []),
        "kind": str(row["kind"] or "topic"),
        "description": str(row["description"] or ""),
        "status": str(row["status"] or "active"),
        "parent_id": str(row["parent_id"]) if row["parent_id"] else None,
        "created_at": str(row["created_at"]),
        "updated_at": str(row["updated_at"]),
        "meta": json_loads(row["meta_json"], {}),
    }


def _snippet(content: str, query: str, limit: int = 240) -> str:
    text = " ".join(str(content or "").split())
    if not text:
        return ""
    needle = str(query or "").strip()
    index = text.lower().find(needle.lower()) if needle else -1
    if index < 0:
        index = 0
    start = max(0, index - 80)
    end = min(len(text), start + max(80, limit))
    prefix = "…" if start > 0 else ""
    suffix = "…" if end < len(text) else ""
    return prefix + text[start:end] + suffix


def _session_row(row: sqlite3.Row) -> Dict[str, Any]:
    return {
        "id": str(row["id"]),
        "title": str(row["title"] or ""),
        "topic_id": str(row["topic_id"]) if row["topic_id"] else None,
        "client_session_id": (
            str(row["client_session_id"]) if row["client_session_id"] else None
        ),
        "status": str(row["status"]),
        "message_count": int(row["message_count"] or 0),
        "created_at": str(row["created_at"]),
        "updated_at": str(row["updated_at"]),
        "last_message_at": str(row["last_message_at"]) if row["last_message_at"] else None,
        "deleted_at": str(row["deleted_at"]) if row["deleted_at"] else None,
        "meta": json_loads(row["meta_json"], {}),
    }


def _message_row(row: sqlite3.Row) -> Dict[str, Any]:
    return {
        "id": str(row["id"]),
        "session_id": str(row["session_id"]),
        "seq": int(row["seq"]),
        "role": str(row["role"]),
        "content": str(row["content"] or ""),
        "status": str(row["status"]),
        "source": str(row["source"] or ""),
        "model": str(row["model"] or ""),
        "request_id": str(row["request_id"]) if row["request_id"] else None,
        "client_message_id": (
            str(row["client_message_id"]) if row["client_message_id"] else None
        ),
        "reply_to_id": str(row["reply_to_id"]) if row["reply_to_id"] else None,
        "created_at": str(row["created_at"]),
        "completed_at": str(row["completed_at"]) if row["completed_at"] else None,
        "updated_at": str(row["updated_at"]),
        "token_count": int(row["token_count"]) if row["token_count"] is not None else None,
        "references": json_loads(row["references_json"], []),
        "meta": json_loads(row["meta_json"], {}),
    }


# ============================================================
# 内部写入工具
# ============================================================

def _next_seq(conn: sqlite3.Connection, session_id: str) -> int:
    row = conn.execute(
        "SELECT COALESCE(MAX(seq), 0) + 1 FROM messages WHERE session_id = ?",
        (session_id,),
    ).fetchone()
    return int(row[0] or 1)


def _insert_message(
    conn: sqlite3.Connection,
    *,
    session_id: str,
    seq: int,
    role: str,
    content: str,
    status: str,
    source: str,
    model: str,
    request_id: Optional[str],
    client_message_id: Optional[str],
    reply_to_id: Optional[str],
    created_at: str,
    completed_at: Optional[str],
    references: Any,
    meta: Any,
) -> str:
    message_id = new_id("m")
    conn.execute(
        """
        INSERT INTO messages(
            id, session_id, seq, role, content, status, source, model,
            request_id, client_message_id, reply_to_id, created_at, completed_at,
            updated_at, token_count, references_json, meta_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?)
        """,
        (
            message_id,
            session_id,
            seq,
            role,
            content,
            status,
            source,
            model,
            request_id,
            client_message_id,
            reply_to_id,
            created_at,
            completed_at,
            created_at,
            json_dumps(references if isinstance(references, list) else []),
            json_dumps(meta if isinstance(meta, dict) else {}),
        ),
    )
    return message_id


def _bump_session(conn: sqlite3.Connection, session_id: str, *, at: str) -> None:
    conn.execute(
        "UPDATE sessions SET message_count = message_count + 1, "
        "updated_at = ?, last_message_at = ? WHERE id = ?",
        (at, at, session_id),
    )


def _remove_session_fts(conn: sqlite3.Connection, session_id: str) -> None:
    """把某会话的消息从 FTS 移除，但保留 messages 行（软删除语义）。

    FTS5 external-content 表的 delete 命令需要旧 rowid + content。
    如果 FTS 表不存在（SQLite 未编译 FTS5），静默跳过。
    """
    exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='messages_fts'"
    ).fetchone()
    if not exists:
        return
    rows = conn.execute(
        "SELECT rowid, content FROM messages WHERE session_id = ? AND fts_indexed = 1",
        (session_id,),
    ).fetchall()
    for row in rows:
        conn.execute(
            "INSERT INTO messages_fts(messages_fts, rowid, content) VALUES ('delete', ?, ?)",
            (row["rowid"], row["content"]),
        )
    conn.execute(
        "UPDATE messages SET fts_indexed = 0 WHERE session_id = ?", (session_id,)
    )


__all__ = [
    "MAX_IMPORT_MESSAGES",
    "MAX_IMPORT_SESSIONS",
    "MemoryStore",
    "SessionNotFound",
]
