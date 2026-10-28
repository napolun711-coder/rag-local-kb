"""长期记忆仓储（memories / sources / events）。

职责：
- 记忆 CRUD、确认/拒绝/恢复、来源与版本事件；
- canonical key 去重、冲突检测、active 唯一；
- 软删除时同步移除 FTS；彻底删除保留 memory_events 审计；
- 检索（active + scope + 敏感过滤 + FTS/LIKE）。

不做：
- 规则提取（fastmem.py）、上下文装配（context.py）、异步任务（）。
"""

from __future__ import annotations

import hashlib
import logging
import re
import sqlite3
from typing import Any, Dict, Iterable, List, Optional, Sequence

from app.features.memory.db import MemoryDatabase
from app.features.memory.tombstones import TombstoneLog
from app.features.memory.util import json_dumps, json_loads, new_id, utc_now

logger = logging.getLogger(__name__)

KINDS = (
    "preference",
    "goal",
    "fact",
    "constraint",
    "decision",
    "habit",
    "profile",
    "background",
)
STATUSES = ("active", "pending", "conflicted", "superseded", "expired", "deleted")
SOURCE_TYPES = (
    "user_explicit",
    "user_stated",
    "user_confirmed",
    "model_inferred",
    "import",
)
SENSITIVITIES = ("normal", "sensitive", "secret")
SCOPES = ("global", "topic")

MAX_CONTENT = 300
MAX_SUBJECT = 40

_NORMALIZE_RE = re.compile(r"[\s，,。；;：:！!？?、\"'“”‘’（）()【】\[\]]+")


class MemoryNotFound(Exception):
    pass


class MemoryConflict(Exception):
    pass


def normalize_subject(text: str) -> str:
    value = _NORMALIZE_RE.sub("", str(text or "").strip().lower())
    return value[:80]


def canonical_key(scope_key: str, kind: str, subject: str) -> str:
    raw = f"{scope_key}|{kind}|{normalize_subject(subject)}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def content_key(text: str) -> str:
    return hashlib.sha256(
        _NORMALIZE_RE.sub("", str(text or "").strip().lower()).encode("utf-8")
    ).hexdigest()


class MemoryRepository:
    def __init__(self, db: MemoryDatabase, tombstones: TombstoneLog) -> None:
        self.db = db
        self.tombstones = tombstones

    # ============================================================
    # 写入
    # ============================================================

    def create(
        self,
        *,
        content: str,
        subject: str,
        kind: str,
        scope: str = "global",
        topic_id: Optional[str] = None,
        source_type: str = "user_confirmed",
        sensitivity: str = "normal",
        confidence: float = 0.8,
        importance: int = 3,
        status: str = "active",
        pinned: bool = False,
        valid_from: Optional[str] = None,
        valid_to: Optional[str] = None,
        meta: Optional[Dict[str, Any]] = None,
        source: Optional[Dict[str, Any]] = None,
        actor: str = "user",
        user_edited: bool = False,
    ) -> Dict[str, Any]:
        clean_content = str(content or "").strip()[:MAX_CONTENT]
        clean_subject = str(subject or "").strip()[:MAX_SUBJECT]
        if not clean_content:
            raise ValueError("content 不能为空")
        if not clean_subject:
            raise ValueError("subject 不能为空")
        kind = _choice(kind, KINDS, "fact")
        scope = _choice(scope, SCOPES, "global")
        source_type = _choice(source_type, SOURCE_TYPES, "user_confirmed")
        sensitivity = _choice(sensitivity, SENSITIVITIES, "normal")
        status = _choice(status, STATUSES, "pending")
        # 数据层硬约束（不依赖调用方自觉）：
        # - secret 永不落库；
        # - 模型推断/敏感信息不得直接 active，只能待确认。
        if sensitivity == "secret":
            raise ValueError("secret 记忆不允许落库")
        if status == "active" and (
            source_type == "model_inferred" or sensitivity == "sensitive"
        ):
            status = "pending"
        importance = max(1, min(5, int(importance or 3)))
        confidence = max(0.0, min(1.0, float(confidence)))
        topic_id = _topic(scope, topic_id)
        if scope == "topic" and not topic_id:
            raise ValueError("scope=topic 时必须提供 topic_id")
        scope_key = "global" if scope == "global" else f"topic:{topic_id}"
        canonical = canonical_key(scope_key, kind, clean_subject)
        now = utc_now()

        with self.db.transaction() as conn:
            _ensure_topic(conn, topic_id)
            if status == "active":
                existing = conn.execute(
                    "SELECT * FROM memories WHERE canonical_key = ? AND status = 'active'",
                    (canonical,),
                ).fetchone()
                if existing is not None:
                    if content_key(existing["content"]) == content_key(clean_content):
                        before = _memory_row(existing)
                        self._add_source(conn, existing["id"], source, relation="supports")
                        conn.execute(
                            "UPDATE memories SET confidence = MAX(confidence, ?), "
                            "last_confirmed_at = ?, updated_at = ?, "
                            "version = version + 1 WHERE id = ?",
                            (confidence, now, now, existing["id"]),
                        )
                        after = _memory_row(
                            conn.execute(
                                "SELECT * FROM memories WHERE id = ?", (existing["id"],)
                            ).fetchone()
                        )
                        self._event(
                            conn,
                            existing["id"],
                            int(after["version"]),
                            "merge",
                            actor,
                            "同内容重复，合并来源",
                            before,
                            after,
                        )
                        return after
                    # 同一主题已有 active 且内容不同：不静默覆盖，落成 conflicted。
                    status = "conflicted"
                    meta = dict(meta or {})
                    meta.setdefault("conflict_with", existing["id"])

            memory_id = new_id("mem")
            conn.execute(
                """
                INSERT INTO memories(
                    id, scope, topic_id, scope_key, kind, subject, content, keywords,
                    canonical_key, status, confidence, source_type, sensitivity,
                    importance, pinned, user_edited, valid_from, valid_to,
                    last_confirmed_at, version, created_at, updated_at, meta_json,
                    fts_indexed
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, 1)
                """,
                (
                    memory_id,
                    scope,
                    topic_id,
                    scope_key,
                    kind,
                    clean_subject,
                    clean_content,
                    _keywords(clean_subject, clean_content),
                    canonical,
                    status,
                    confidence,
                    source_type,
                    sensitivity,
                    importance,
                    1 if pinned else 0,
                    1 if user_edited else 0,
                    valid_from,
                    valid_to,
                    now if status == "active" else None,
                    now,
                    now,
                    json_dumps(meta if isinstance(meta, dict) else {}),
                ),
            )
            self._add_source(conn, memory_id, source, relation="supports")
            self._event(
                conn,
                memory_id,
                1,
                "create",
                actor,
                "",
                None,
                _memory_row(
                    conn.execute(
                        "SELECT * FROM memories WHERE id = ?", (memory_id,)
                    ).fetchone()
                ),
            )
            row = conn.execute("SELECT * FROM memories WHERE id = ?", (memory_id,)).fetchone()
            return _memory_row(row)

    def update(
        self,
        memory_id: str,
        *,
        content: Optional[str] = None,
        subject: Optional[str] = None,
        kind: Optional[str] = None,
        scope: Optional[str] = None,
        topic_id: Optional[str] = None,
        importance: Optional[int] = None,
        pinned: Optional[bool] = None,
        sensitivity: Optional[str] = None,
        valid_from: Optional[str] = None,
        valid_to: Optional[str] = None,
        meta: Optional[Dict[str, Any]] = None,
        actor: str = "user",
        reason: str = "",
    ) -> Dict[str, Any]:
        with self.db.transaction() as conn:
            old = conn.execute("SELECT * FROM memories WHERE id = ?", (memory_id,)).fetchone()
            if old is None:
                raise MemoryNotFound(memory_id)
            before = _memory_row(old)

            fields: List[str] = []
            values: List[Any] = []
            new_content = str(content).strip()[:MAX_CONTENT] if content is not None else old["content"]
            new_subject = str(subject).strip()[:MAX_SUBJECT] if subject is not None else old["subject"]
            new_kind = _choice(kind, KINDS, old["kind"]) if kind is not None else old["kind"]
            new_scope = _choice(scope, SCOPES, old["scope"]) if scope is not None else old["scope"]
            new_topic = _topic(new_scope, topic_id if topic_id is not None else old["topic_id"])
            if new_scope == "topic" and not new_topic:
                raise ValueError("scope=topic 时必须提供 topic_id")
            _ensure_topic(conn, new_topic)
            new_scope_key = "global" if new_scope == "global" else f"topic:{new_topic}"
            new_canonical = canonical_key(new_scope_key, new_kind, new_subject)
            new_sensitivity = (
                _choice(sensitivity, SENSITIVITIES, "normal")
                if sensitivity is not None
                else str(old["sensitivity"])
            )
            # 硬不变量：secret 不允许通过任何写路径落库。
            if new_sensitivity == "secret":
                raise ValueError("secret 记忆不允许落库")

            if old["status"] == "active":
                conflict = conn.execute(
                    "SELECT id FROM memories WHERE canonical_key = ? AND status = 'active' "
                    "AND id != ?",
                    (new_canonical, memory_id),
                ).fetchone()
                if conflict is not None:
                    raise MemoryConflict("同一主题已有 active 记忆；请先合并或使旧记忆失效")

            if content is not None:
                fields.append("content = ?")
                values.append(new_content)
            if subject is not None:
                fields.append("subject = ?")
                values.append(new_subject)
            if kind is not None:
                fields.append("kind = ?")
                values.append(new_kind)
            if scope is not None or topic_id is not None:
                fields.extend(["scope = ?", "topic_id = ?", "scope_key = ?"])
                values.extend([new_scope, new_topic, new_scope_key])
            if subject is not None or kind is not None or scope is not None or topic_id is not None:
                fields.extend(["canonical_key = ?", "keywords = ?"])
                values.extend([new_canonical, _keywords(new_subject, new_content)])
            elif content is not None:
                fields.append("keywords = ?")
                values.append(_keywords(new_subject, new_content))
            if importance is not None:
                fields.append("importance = ?")
                values.append(max(1, min(5, int(importance))))
            if pinned is not None:
                fields.append("pinned = ?")
                values.append(1 if pinned else 0)
            if sensitivity is not None:
                fields.append("sensitivity = ?")
                values.append(new_sensitivity)
            if valid_from is not None:
                fields.append("valid_from = ?")
                values.append(valid_from)
            if valid_to is not None:
                fields.append("valid_to = ?")
                values.append(valid_to)
            if meta is not None:
                merged = json_loads(old["meta_json"], {})
                if not isinstance(merged, dict):
                    merged = {}
                merged.update(meta)
                fields.append("meta_json = ?")
                values.append(json_dumps(merged))

            # 方案 A：敏感信息与模型推断不得保持 active；用户 confirm 后才可 active。
            force_pending = (
                new_sensitivity == "sensitive"
                or str(old["source_type"]) == "model_inferred"
            )
            if force_pending and str(old["status"]) in ("active", "conflicted"):
                fields.append("status = ?")
                values.append("pending")

            now = utc_now()
            fields.extend(["user_edited = 1", "updated_at = ?", "version = version + 1"])
            values.append(now)
            values.append(memory_id)
            conn.execute(
                f"UPDATE memories SET {', '.join(fields)} WHERE id = ?", tuple(values)
            )
            after = _memory_row(
                conn.execute("SELECT * FROM memories WHERE id = ?", (memory_id,)).fetchone()
            )
            self._event(
                conn,
                memory_id,
                int(after["version"]),
                "update",
                actor,
                reason,
                before,
                after,
            )
            return after

    # ============================================================
    # 状态
    # ============================================================

    def confirm(self, memory_id: str, *, actor: str = "user") -> Dict[str, Any]:
        with self.db.transaction() as conn:
            row = conn.execute("SELECT * FROM memories WHERE id = ?", (memory_id,)).fetchone()
            if row is None:
                raise MemoryNotFound(memory_id)
            if row["status"] == "active":
                return _memory_row(row)
            if row["status"] not in ("pending", "conflicted"):
                raise ValueError("只有 pending/conflicted 记忆可以确认")
            # 数据层硬约束：sensitive 只能保持 pending，不能被 confirm 成 active 注入。
            if str(row["sensitivity"]) == "sensitive":
                raise ValueError("敏感记忆只能保持 pending，不能确认注入；如不再需要请删除")
            active = conn.execute(
                "SELECT * FROM memories WHERE canonical_key = ? AND status = 'active' "
                "AND id != ?",
                (row["canonical_key"], memory_id),
            ).fetchone()
            now = utc_now()
            if active is not None:
                if content_key(active["content"]) == content_key(row["content"]):
                    # 已有同内容 active：把候选来源并过去，候选软删，直接返回 active。
                    active_before = _memory_row(active)
                    _merge_sources(conn, memory_id, active["id"])
                    _remove_memory_fts(conn, memory_id)
                    conn.execute(
                        "UPDATE memories SET status = 'deleted', deleted_at = ?, "
                        "updated_at = ?, version = version + 1 WHERE id = ?",
                        (now, now, memory_id),
                    )
                    candidate_after = _memory_row(
                        conn.execute(
                            "SELECT * FROM memories WHERE id = ?", (memory_id,)
                        ).fetchone()
                    )
                    self._event(
                        conn,
                        memory_id,
                        int(candidate_after["version"]),
                        "merge",
                        actor,
                        "同内容候选合并到 active",
                        _memory_row(row),
                        candidate_after,
                    )
                    conn.execute(
                        "UPDATE memories SET confidence = MAX(confidence, ?), "
                        "last_confirmed_at = ?, updated_at = ?, "
                        "version = version + 1 WHERE id = ?",
                        (row["confidence"], now, now, active["id"]),
                    )
                    active_after = _memory_row(
                        conn.execute(
                            "SELECT * FROM memories WHERE id = ?", (active["id"],)
                        ).fetchone()
                    )
                    self._event(
                        conn,
                        active["id"],
                        int(active_after["version"]),
                        "merge",
                        actor,
                        "合并同内容候选来源",
                        active_before,
                        active_after,
                    )
                    return active_after
                conn.execute(
                    "UPDATE memories SET status = 'superseded', valid_to = ?, "
                    "superseded_by = ?, updated_at = ?, version = version + 1 "
                    "WHERE id = ?",
                    (now, memory_id, now, active["id"]),
                )
                self._event(
                    conn,
                    active["id"],
                    int(active["version"]) + 1,
                    "supersede",
                    actor,
                    "用户确认了新的冲突记忆",
                    _memory_row(active),
                    _memory_row(
                        conn.execute(
                            "SELECT * FROM memories WHERE id = ?", (active["id"],)
                        ).fetchone()
                    ),
                )
            conn.execute(
                "UPDATE memories SET status = 'active', source_type = 'user_confirmed', "
                "last_confirmed_at = ?, updated_at = ?, version = version + 1, fts_indexed = 1 "
                "WHERE id = ?",
                (now, now, memory_id),
            )
            # 如果之前软删除/待确认时 FTS 没索引，确认时补上。
            if not row["fts_indexed"]:
                _index_memory(conn, memory_id)
            after = _memory_row(
                conn.execute("SELECT * FROM memories WHERE id = ?", (memory_id,)).fetchone()
            )
            self._event(
                conn,
                memory_id,
                int(after["version"]),
                "confirm",
                actor,
                "",
                _memory_row(row),
                after,
            )
            return after

    def reject(self, memory_id: str, *, actor: str = "user", reason: str = "") -> bool:
        with self.db.transaction() as conn:
            row = conn.execute("SELECT * FROM memories WHERE id = ?", (memory_id,)).fetchone()
            if row is None:
                raise MemoryNotFound(memory_id)
            if row["status"] == "deleted":
                return True
            _remove_memory_fts(conn, memory_id)
            now = utc_now()
            conn.execute(
                "UPDATE memories SET status = 'deleted', deleted_at = ?, updated_at = ?, "
                "version = version + 1 WHERE id = ?",
                (now, now, memory_id),
            )
            after = _memory_row(
                conn.execute("SELECT * FROM memories WHERE id = ?", (memory_id,)).fetchone()
            )
            self._event(
                conn,
                memory_id,
                int(after["version"]),
                "reject",
                actor,
                reason,
                _memory_row(row),
                after,
            )
        return True

    def delete(self, memory_id: str, *, purge: bool = False, actor: str = "user") -> bool:
        with self.db.transaction() as conn:
            row = conn.execute("SELECT * FROM memories WHERE id = ?", (memory_id,)).fetchone()
            if row is None:
                return False
            before = _memory_row(row)
            _remove_memory_fts(conn, memory_id)
            if purge:
                # memory_events 不设外键：先写 delete 事件，再删正文，审计仍保留。
                self._event(
                    conn,
                    memory_id,
                    int(row["version"]) + 1,
                    "delete",
                    actor,
                    "彻底删除",
                    before,
                    None,
                )
                # 彻底删除不能把正文留在事件 before_json / 来源 quote 里：
                # 事件保留种类与时间用于审计，但清空内容字段。
                conn.execute(
                    "UPDATE memory_events SET before_json = NULL, after_json = NULL "
                    "WHERE memory_id = ?",
                    (memory_id,),
                )
                # 彻底忘记后，历史召回不再返回来源消息；只存 ID/哈希，不存 quote 正文。
                now = utc_now()
                sources = conn.execute(
                    "SELECT message_id, session_id, quote FROM memory_sources "
                    "WHERE memory_id = ?",
                    (memory_id,),
                ).fetchall()
                for source in sources:
                    conn.execute(
                        "INSERT INTO memory_suppressions("
                        "memory_id, message_id, session_id, quote_hash, reason, created_at"
                        ") VALUES (?, ?, ?, ?, 'forget', ?)",
                        (
                            memory_id,
                            source["message_id"],
                            source["session_id"],
                            content_key(str(source["quote"] or "")),
                            now,
                        ),
                    )
                conn.execute(
                    "DELETE FROM memory_sources WHERE memory_id = ?", (memory_id,)
                )
                conn.execute("DELETE FROM memories WHERE id = ?", (memory_id,))
            else:
                now = utc_now()
                conn.execute(
                    "UPDATE memories SET status = 'deleted', deleted_at = ?, "
                    "updated_at = ?, version = version + 1 WHERE id = ?",
                    (now, now, memory_id),
                )
                after = _memory_row(
                    conn.execute(
                        "SELECT * FROM memories WHERE id = ?", (memory_id,)
                    ).fetchone()
                )
                self._event(
                    conn,
                    memory_id,
                    int(after["version"]),
                    "delete",
                    actor,
                    "软删除",
                    before,
                    after,
                )
        self.tombstones.append(
            kind="memory", item_id=memory_id, mode="purge" if purge else "soft"
        )
        return True

    def restore(self, memory_id: str, *, actor: str = "user") -> Dict[str, Any]:
        with self.db.transaction() as conn:
            row = conn.execute("SELECT * FROM memories WHERE id = ?", (memory_id,)).fetchone()
            if row is None:
                raise MemoryNotFound(memory_id)
            if row["status"] != "deleted":
                return _memory_row(row)
            # 方案 A：模型推断与敏感信息恢复后仍是 pending，必须用户 confirm 才 active。
            target_status = (
                "pending"
                if str(row["source_type"]) == "model_inferred"
                or str(row["sensitivity"]) == "sensitive"
                else "active"
            )
            if target_status == "active":
                active = conn.execute(
                    "SELECT id FROM memories WHERE canonical_key = ? AND status = 'active'",
                    (row["canonical_key"],),
                ).fetchone()
                if active is not None:
                    raise MemoryConflict("同一主题已有 active 记忆，无法恢复")
            now = utc_now()
            conn.execute(
                "UPDATE memories SET status = ?, deleted_at = NULL, "
                "updated_at = ?, version = version + 1, fts_indexed = ? WHERE id = ?",
                (
                    target_status,
                    now,
                    1 if target_status == "active" else 0,
                    memory_id,
                ),
            )
            if target_status == "active":
                _index_memory(conn, memory_id)
            after = _memory_row(
                conn.execute("SELECT * FROM memories WHERE id = ?", (memory_id,)).fetchone()
            )
            self._event(
                conn,
                memory_id,
                int(after["version"]),
                "restore",
                actor,
                "恢复为待确认（模型推断/敏感信息）" if target_status == "pending" else "",
                _memory_row(row),
                after,
            )
            return after

    def replace_active(
        self,
        old_id: str,
        *,
        content: str,
        subject: Optional[str] = None,
        kind: Optional[str] = None,
        scope: Optional[str] = None,
        topic_id: Optional[str] = None,
        source_type: str = "user_explicit",
        sensitivity: str = "normal",
        confidence: float = 0.95,
        importance: int = 3,
        source: Optional[Dict[str, Any]] = None,
        reason: str = "用户纠正",
        actor: str = "user",
    ) -> Dict[str, Any]:
        """原子地把一条 active 记忆替换为新内容（纠正路径）。"""
        with self.db.transaction() as conn:
            old = conn.execute("SELECT * FROM memories WHERE id = ?", (old_id,)).fetchone()
            if old is None:
                raise MemoryNotFound(old_id)
            if old["status"] != "active":
                raise ValueError("只能替换 active 记忆")
            new_content = str(content or "").strip()[:MAX_CONTENT]
            new_subject = str(subject or old["subject"]).strip()[:MAX_SUBJECT]
            new_kind = _choice(kind, KINDS, old["kind"]) if kind else old["kind"]
            new_scope = _choice(scope, SCOPES, old["scope"]) if scope else old["scope"]
            new_topic = _topic(new_scope, topic_id if topic_id is not None else old["topic_id"])
            if new_scope == "topic" and not new_topic:
                raise ValueError("scope=topic 时必须提供 topic_id")
            _ensure_topic(conn, new_topic)
            new_scope_key = "global" if new_scope == "global" else f"topic:{new_topic}"
            new_canonical = canonical_key(new_scope_key, new_kind, new_subject)
            if _choice(sensitivity, SENSITIVITIES, "normal") == "secret":
                raise ValueError("secret 记忆不允许落库")
            now = utc_now()
            # 先让旧 active 失效，释放 canonical 唯一键。
            conn.execute(
                "UPDATE memories SET status = 'superseded', valid_to = ?, updated_at = ?, "
                "version = version + 1 WHERE id = ?",
                (now, now, old_id),
            )
            new_id_value = new_id("mem")
            conn.execute(
                """
                INSERT INTO memories(
                    id, scope, topic_id, scope_key, kind, subject, content, keywords,
                    canonical_key, status, confidence, source_type, sensitivity,
                    importance, pinned, user_edited, valid_from, valid_to,
                    last_confirmed_at, version, created_at, updated_at, meta_json,
                    fts_indexed
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?, ?, ?, 0, 0,
                          ?, NULL, ?, 1, ?, ?, '{}', 1)
                """,
                (
                    new_id_value,
                    new_scope,
                    new_topic,
                    new_scope_key,
                    new_kind,
                    new_subject,
                    new_content,
                    _keywords(new_subject, new_content),
                    new_canonical,
                    max(0.0, min(1.0, float(confidence))),
                    _choice(source_type, SOURCE_TYPES, "user_explicit"),
                    _choice(sensitivity, SENSITIVITIES, "normal"),
                    max(1, min(5, int(importance or 3))),
                    now,
                    now,
                    now,
                    now,
                ),
            )
            conn.execute(
                "UPDATE memories SET superseded_by = ? WHERE id = ?", (new_id_value, old_id)
            )
            self._add_source(conn, new_id_value, source, relation="supports")
            self._event(
                conn,
                old_id,
                int(old["version"]) + 1,
                "supersede",
                actor,
                reason,
                _memory_row(old),
                _memory_row(
                    conn.execute("SELECT * FROM memories WHERE id = ?", (old_id,)).fetchone()
                ),
            )
            after = _memory_row(
                conn.execute(
                    "SELECT * FROM memories WHERE id = ?", (new_id_value,)
                ).fetchone()
            )
            self._event(conn, new_id_value, 1, "create", actor, reason, None, after)
            return after

    # ============================================================
    # 读取 / 检索
    # ============================================================

    def get(self, memory_id: str, *, include_deleted: bool = False) -> Optional[Dict[str, Any]]:
        with self.db.connect() as conn:
            row = conn.execute("SELECT * FROM memories WHERE id = ?", (memory_id,)).fetchone()
        if row is None:
            return None
        if not include_deleted and row["status"] == "deleted":
            return None
        return _memory_row(row)

    def find_by_canonical(
        self,
        canonical_key: str,
        *,
        status: Optional[str] = None,
        limit: int = 10,
    ) -> List[Dict[str, Any]]:
        """按 canonical key 查记忆； 合并策略使用。"""
        clause = " WHERE canonical_key = ?"
        values: List[Any] = [canonical_key]
        if status:
            clause += " AND status = ?"
            values.append(str(status))
        values.append(max(1, min(50, int(limit or 10))))
        with self.db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM memories" + clause
                + " ORDER BY updated_at DESC LIMIT ?",
                tuple(values),
            ).fetchall()
        return [_memory_row(row) for row in rows]

    def active_by_canonical(self, canonical_key: str) -> Optional[Dict[str, Any]]:
        rows = self.find_by_canonical(canonical_key, status="active", limit=1)
        return rows[0] if rows else None

    def mark_conflicted(
        self,
        memory_id: str,
        *,
        reason: str = "",
        meta: Optional[Dict[str, Any]] = None,
        actor: str = "system",
    ) -> Dict[str, Any]:
        """把 active 记忆标记为 conflicted（Tier 2 高置信冲突策略）。"""
        with self.db.transaction() as conn:
            row = conn.execute(
                "SELECT * FROM memories WHERE id = ?", (memory_id,)
            ).fetchone()
            if row is None:
                raise MemoryNotFound(memory_id)
            if str(row["status"]) != "active":
                return _memory_row(row)
            before = _memory_row(row)
            merged = json_loads(row["meta_json"], {})
            if not isinstance(merged, dict):
                merged = {}
            if isinstance(meta, dict):
                merged.update(meta)
            now = utc_now()
            conn.execute(
                "UPDATE memories SET status = 'conflicted', updated_at = ?, "
                "version = version + 1, meta_json = ? WHERE id = ?",
                (now, json_dumps(merged), memory_id),
            )
            after = _memory_row(
                conn.execute(
                    "SELECT * FROM memories WHERE id = ?", (memory_id,)
                ).fetchone()
            )
            self._event(
                conn,
                memory_id,
                int(after["version"]),
                "update",
                actor,
                reason or "tier2_conflict",
                before,
                after,
            )
            return after

    def expire(
        self,
        memory_id: str,
        *,
        reason: str = "expired",
        actor: str = "system",
    ) -> Dict[str, Any]:
        """Mark a memory as expired and remove it from retrieval indexes."""
        with self.db.transaction() as conn:
            row = conn.execute(
                "SELECT * FROM memories WHERE id = ?", (memory_id,)
            ).fetchone()
            if row is None:
                raise MemoryNotFound(memory_id)
            if str(row["status"]) in ("expired", "deleted"):
                return _memory_row(row)
            before = _memory_row(row)
            now = utc_now()
            _remove_memory_fts(conn, memory_id)
            conn.execute(
                "UPDATE memories SET status = 'expired', fts_indexed = 0, "
                "updated_at = ?, version = version + 1 WHERE id = ?",
                (now, memory_id),
            )
            after = _memory_row(
                conn.execute(
                    "SELECT * FROM memories WHERE id = ?", (memory_id,)
                ).fetchone()
            )
            self._event(
                conn,
                memory_id,
                int(after["version"]),
                "expire",
                actor,
                reason,
                before,
                after,
            )
            return after
    def promote(
        self,
        memory_id: str,
        *,
        reason: str = "tier2_reextract",
        actor: str = "system",
    ) -> Dict[str, Any]:
        """把 pending/conflicted 的用户候选提升为 active（Tier 2 重新提取）。"""
        with self.db.transaction() as conn:
            row = conn.execute(
                "SELECT * FROM memories WHERE id = ?", (memory_id,)
            ).fetchone()
            if row is None:
                raise MemoryNotFound(memory_id)
            if str(row["status"]) == "active":
                return _memory_row(row)
            if str(row["status"]) not in ("pending", "conflicted"):
                raise ValueError("只有 pending/conflicted 记忆可以提升为 active")
            if (
                str(row["source_type"]) == "model_inferred"
                or str(row["sensitivity"]) != "normal"
            ):
                raise ValueError("模型推断/敏感记忆不能自动提升为 active")
            before = _memory_row(row)
            now = utc_now()
            conn.execute(
                "UPDATE memories SET status = 'active', last_confirmed_at = ?, "
                "updated_at = ?, version = version + 1, fts_indexed = 1 WHERE id = ?",
                (now, now, memory_id),
            )
            if not row["fts_indexed"]:
                _index_memory(conn, memory_id)
            after = _memory_row(
                conn.execute(
                    "SELECT * FROM memories WHERE id = ?", (memory_id,)
                ).fetchone()
            )
            self._event(
                conn,
                memory_id,
                int(after["version"]),
                "confirm",
                actor,
                reason,
                before,
                after,
            )
            return after

    def list(
        self,
        *,
        status: Optional[str] = None,
        kind: Optional[str] = None,
        scope: Optional[str] = None,
        topic_id: Optional[str] = None,
        q: str = "",
        limit: int = 50,
        offset: int = 0,
        include_deleted: bool = False,
    ) -> List[Dict[str, Any]]:
        size = max(1, min(500, int(limit or 50)))
        start = max(0, int(offset or 0))
        where: List[str] = []
        values: List[Any] = []
        if status:
            where.append("status = ?")
            values.append(_choice(status, STATUSES, "active"))
        elif not include_deleted:
            where.append("status != 'deleted'")
        if kind:
            where.append("kind = ?")
            values.append(_choice(kind, KINDS, "fact"))
        if scope:
            where.append("scope = ?")
            values.append(_choice(scope, SCOPES, "global"))
        if topic_id:
            where.append("topic_id = ?")
            values.append(topic_id)
        text = str(q or "").strip()
        if text:
            where.append("(content LIKE ? OR subject LIKE ? OR keywords LIKE ?)")
            like = f"%{text}%"
            values.extend([like, like, like])
        clause = (" WHERE " + " AND ".join(where)) if where else ""
        values.extend([size, start])
        with self.db.connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM memories{clause} "
                "ORDER BY pinned DESC, importance DESC, updated_at DESC "
                "LIMIT ? OFFSET ?",
                tuple(values),
            ).fetchall()
        return [_memory_row(row) for row in rows]

    def search(
        self,
        query: str,
        *,
        scope_keys: Sequence[str],
        limit: int = 12,
        include_sensitive: bool = False,
    ) -> List[Dict[str, Any]]:
        text = str(query or "").strip()
        keys = [key for key in scope_keys if key]
        if not keys:
            return []
        size = max(1, min(50, int(limit or 12)))
        now = utc_now()
        where = [
            "m.status = 'active'",
            "(m.valid_to IS NULL OR m.valid_to > ?)",
            "m.sensitivity != 'secret'",
            "m.scope_key IN (" + ",".join("?" for _ in keys) + ")",
        ]
        values: List[Any] = [now, *keys]
        if not include_sensitive:
            where.append("m.sensitivity = 'normal'")

        rows: List[sqlite3.Row] = []
        with self.db.connect() as conn:
            # FTS 对中文问句容易因为“整个短语”匹配不到（如“项目代号是什么”），
            # 因此 FTS/LIKE 之后还有一层 subject/keyword 命中兜底。
            fts_text = re.sub(
                r"[，,。；;：:！!？?、\"'“”‘’（）()【】\[\]]+", " ", text
            ).strip()
            if fts_text and len(fts_text) >= 3 and self.db.memories_fts_available:
                try:
                    sql = (
                        "SELECT m.* FROM memories_fts f JOIN memories m ON m.rowid = f.rowid "
                        "WHERE memories_fts MATCH ? AND " + " AND ".join(where) +
                        " ORDER BY bm25(memories_fts), m.pinned DESC, "
                        "m.importance DESC, m.updated_at DESC LIMIT ?"
                    )
                    rows = conn.execute(sql, (fts_text, *values, size)).fetchall()
                except sqlite3.OperationalError:
                    rows = []
            if not rows and text:
                like = f"%{text}%"
                rows = conn.execute(
                    "SELECT m.* FROM memories m WHERE " + " AND ".join(where) +
                    " AND (m.content LIKE ? OR m.subject LIKE ? OR m.keywords LIKE ?) "
                    "ORDER BY m.pinned DESC, m.importance DESC, m.updated_at DESC LIMIT ?",
                    (*values, like, like, like, size),
                ).fetchall()
            if not rows and text:
                rows = _subject_keyword_search(
                    conn,
                    text,
                    keys=keys,
                    limit=size,
                    include_sensitive=include_sensitive,
                )
        return [_memory_row(row) for row in rows]

    def stable_global(self, *, limit: int = 12) -> List[Dict[str, Any]]:
        """总是注入的全局偏好/画像/约束（数量应很小）。"""
        with self.db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM memories WHERE status = 'active' AND scope = 'global' "
                "AND kind IN ('preference','profile','constraint') "
                "AND sensitivity = 'normal' "
                "AND (valid_to IS NULL OR valid_to > ?) "
                "ORDER BY pinned DESC, importance DESC, updated_at DESC LIMIT ?",
                (utc_now(), max(1, min(50, int(limit or 12)))),
            ).fetchall()
        return [_memory_row(row) for row in rows]

    def find_active_containing(
        self, text: str, *, scope_keys: Sequence[str], limit: int = 10
    ) -> List[Dict[str, Any]]:
        value = str(text or "").strip()
        if not value or not scope_keys:
            return []
        placeholders = ",".join("?" for _ in scope_keys)
        with self.db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM memories WHERE status = 'active' "
                f"AND scope_key IN ({placeholders}) AND content LIKE ? "
                "ORDER BY pinned DESC, importance DESC, updated_at DESC LIMIT ?",
                (*scope_keys, f"%{value}%", max(1, min(50, int(limit or 10)))),
            ).fetchall()
        return [_memory_row(row) for row in rows]

    # ============================================================
    # 来源 / 事件
    # ============================================================

    def sources(self, memory_id: str) -> List[Dict[str, Any]]:
        with self.db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM memory_sources WHERE memory_id = ? ORDER BY created_at",
                (memory_id,),
            ).fetchall()
        return [_source_row(row) for row in rows]

    def events(
        self, memory_id: Optional[str] = None, *, limit: int = 100
    ) -> List[Dict[str, Any]]:
        with self.db.connect() as conn:
            if memory_id:
                rows = conn.execute(
                    "SELECT * FROM memory_events WHERE memory_id = ? "
                    "ORDER BY id DESC LIMIT ?",
                    (memory_id, max(1, min(500, int(limit or 100)))),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM memory_events ORDER BY id DESC LIMIT ?",
                    (max(1, min(500, int(limit or 100)))),
                ).fetchall()
        return [_event_row(row) for row in rows]

    # ============================================================
    # 内部
    # ============================================================

    def _add_source(
        self,
        conn: sqlite3.Connection,
        memory_id: str,
        source: Optional[Dict[str, Any]],
        *,
        relation: str = "supports",
    ) -> None:
        if not source:
            return
        message_id = str(source.get("message_id") or "") or None
        session_id = str(source.get("session_id") or "") or None
        quote = str(source.get("quote") or "")[:500]
        if not message_id and not quote:
            return
        source_meta = (
            dict(source.get("meta")) if isinstance(source.get("meta"), dict) else {}
        )
        if quote:
            # 来源 quote 必须能在落盘消息里逐字找到；找不到时保留原值但显式标记。
            message = (
                conn.execute(
                    "SELECT content FROM messages WHERE id = ?", (message_id,)
                ).fetchone()
                if message_id
                else None
            )
            source_meta["quote_verified"] = bool(
                message is not None and quote in str(message["content"] or "")
            )
        conn.execute(
            "INSERT OR IGNORE INTO memory_sources("
            "memory_id, message_id, session_id, quote, relation, created_at, meta_json"
            ") VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                memory_id,
                message_id,
                session_id,
                quote,
                relation if relation in ("supports", "contradicts", "derived_from", "user_edit") else "supports",
                utc_now(),
                json_dumps(source_meta),
            ),
        )

    def _event(
        self,
        conn: sqlite3.Connection,
        memory_id: str,
        version: int,
        action: str,
        actor: str,
        reason: str,
        before: Optional[Dict[str, Any]],
        after: Optional[Dict[str, Any]],
    ) -> None:
        conn.execute(
            "INSERT INTO memory_events("
            "memory_id, version, action, actor, reason, before_json, after_json, created_at"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                memory_id,
                max(1, int(version)),
                action,
                actor if actor in ("user", "system", "import") else "system",
                str(reason or "")[:500],
                json_dumps(before) if before else None,
                json_dumps(after) if after else None,
                utc_now(),
            ),
        )


# ============================================================
# 行 → 字典
# ============================================================

def _memory_row(row: sqlite3.Row) -> Dict[str, Any]:
    return {
        "id": str(row["id"]),
        "scope": str(row["scope"]),
        "topic_id": str(row["topic_id"]) if row["topic_id"] else None,
        "scope_key": str(row["scope_key"]),
        "kind": str(row["kind"]),
        "subject": str(row["subject"]),
        "content": str(row["content"]),
        "keywords": str(row["keywords"] or ""),
        "canonical_key": str(row["canonical_key"]),
        "status": str(row["status"]),
        "confidence": float(row["confidence"]),
        "source_type": str(row["source_type"]),
        "sensitivity": str(row["sensitivity"]),
        "importance": int(row["importance"]),
        "pinned": bool(row["pinned"]),
        "user_edited": bool(row["user_edited"]),
        "valid_from": row["valid_from"],
        "valid_to": row["valid_to"],
        "last_confirmed_at": row["last_confirmed_at"],
        "superseded_by": row["superseded_by"],
        "merged_into": row["merged_into"],
        "version": int(row["version"]),
        "created_at": str(row["created_at"]),
        "updated_at": str(row["updated_at"]),
        "deleted_at": row["deleted_at"],
        "meta": json_loads(row["meta_json"], {}),
    }


def _source_row(row: sqlite3.Row) -> Dict[str, Any]:
    return {
        "id": int(row["id"]),
        "memory_id": str(row["memory_id"]),
        "message_id": row["message_id"],
        "session_id": row["session_id"],
        "quote": str(row["quote"] or ""),
        "relation": str(row["relation"]),
        "created_at": str(row["created_at"]),
        "meta": json_loads(row["meta_json"], {}),
    }


def _event_row(row: sqlite3.Row) -> Dict[str, Any]:
    return {
        "id": int(row["id"]),
        "memory_id": str(row["memory_id"]),
        "version": int(row["version"]),
        "action": str(row["action"]),
        "actor": str(row["actor"]),
        "reason": str(row["reason"] or ""),
        "before": json_loads(row["before_json"], None),
        "after": json_loads(row["after_json"], None),
        "created_at": str(row["created_at"]),
    }


# ============================================================
# 工具
# ============================================================

def _choice(value: Any, allowed: Sequence[str], default: str) -> str:
    text = str(value or "").strip().lower()
    return text if text in allowed else default


def _ensure_topic(conn: sqlite3.Connection, topic_id: Optional[str]) -> None:
    """topic 作用域必须指向已存在的topic，否则返回 400 而不是外键 500。"""
    if not topic_id:
        return
    row = conn.execute(
        "SELECT 1 FROM topics WHERE id = ?", (str(topic_id),)
    ).fetchone()
    if row is None:
        raise ValueError("topic 不存在")


def _topic(scope: str, topic_id: Optional[str]) -> Optional[str]:
    if scope != "topic":
        return None
    value = str(topic_id or "").strip()
    return value or None


def _keywords(subject: str, content: str) -> str:
    # 不做分词依赖：主题 + 正文里按标点/空白切出的 2-20 字片段。
    parts = [str(subject or "").strip()]
    parts.extend(
        token.strip("._-")
        for token in re.split(r"[\s，,。；;：:！!？?、\"'“”‘’（）()【】\[\]]+", str(content or ""))
    )
    seen: List[str] = []
    for part in parts:
        if 2 <= len(part) <= 20 and part not in seen:
            seen.append(part)
        if len(seen) >= 12:
            break
    return " ".join(seen)


def _subject_keyword_search(
    conn: sqlite3.Connection,
    text: str,
    *,
    keys: Sequence[str],
    limit: int,
    include_sensitive: bool,
) -> List[sqlite3.Row]:
    """中文问句的兜底：subject/keyword 是否出现在问题里。

    FTS trigram 对“项目代号是什么”这种整句短语可能匹配不到“项目代号是 A3”；
    这里用记忆自己的 subject/keywords 做确定性包含判断，避免  召回失效。
    """
    query = normalize_subject(text)
    if not query:
        return []
    where = [
        "status = 'active'",
        "(valid_to IS NULL OR valid_to > ?)",
        "sensitivity != 'secret'",
        "scope_key IN (" + ",".join("?" for _ in keys) + ")",
    ]
    values: List[Any] = [utc_now(), *keys]
    if not include_sensitive:
        where.append("sensitivity = 'normal'")
    rows = conn.execute(
        "SELECT * FROM memories WHERE " + " AND ".join(where) + " LIMIT 500",
        tuple(values),
    ).fetchall()
    scored: List[tuple[int, sqlite3.Row]] = []
    for row in rows:
        subject = normalize_subject(row["subject"])
        content = normalize_subject(row["content"])
        score = 0
        if subject and subject in query:
            score += 3
        for keyword in str(row["keywords"] or "").split():
            normalized = normalize_subject(keyword)
            if len(normalized) >= 2 and normalized in query:
                score += 2
        if query in content:
            score += 2
        if content and content in query:
            score += 2
        if score:
            scored.append((score, row))
    scored.sort(
        key=lambda item: (
            item[0],
            int(item[1]["pinned"] or 0),
            int(item[1]["importance"] or 0),
            str(item[1]["updated_at"] or ""),
        ),
        reverse=True,
    )
    return [row for _, row in scored[: max(1, int(limit))]]


def _remove_memory_fts(conn: sqlite3.Connection, memory_id: str) -> None:
    exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='memories_fts'"
    ).fetchone()
    if exists:
        rows = conn.execute(
            "SELECT rowid, subject, content, keywords FROM memories "
            "WHERE id = ? AND fts_indexed = 1",
            (memory_id,),
        ).fetchall()
        for row in rows:
            conn.execute(
                "INSERT INTO memories_fts(memories_fts, rowid, subject, content, keywords) "
                "VALUES ('delete', ?, ?, ?, ?)",
                (row["rowid"], row["subject"], row["content"], row["keywords"]),
            )
    conn.execute("UPDATE memories SET fts_indexed = 0 WHERE id = ?", (memory_id,))


def _merge_sources(conn: sqlite3.Connection, from_id: str, to_id: str) -> None:
    """把候选记忆的来源并到目标记忆，避免确认同内容候选时丢来源。"""
    conn.execute(
        "INSERT OR IGNORE INTO memory_sources("
        "memory_id, message_id, session_id, quote, relation, created_at, meta_json"
        ") SELECT ?, message_id, session_id, quote, relation, created_at, meta_json "
        "FROM memory_sources WHERE memory_id = ?",
        (to_id, from_id),
    )


def _index_memory(conn: sqlite3.Connection, memory_id: str) -> None:
    exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='memories_fts'"
    ).fetchone()
    if not exists:
        return
    row = conn.execute(
        "SELECT rowid, subject, content, keywords FROM memories WHERE id = ?",
        (memory_id,),
    ).fetchone()
    if row is None:
        return
    conn.execute(
        "INSERT INTO memories_fts(rowid, subject, content, keywords) VALUES (?, ?, ?, ?)",
        (row["rowid"], row["subject"], row["content"], row["keywords"]),
    )
    conn.execute("UPDATE memories SET fts_indexed = 1 WHERE id = ?", (memory_id,))


__all__ = [
    "KINDS",
    "MAX_CONTENT",
    "MAX_SUBJECT",
    "MemoryConflict",
    "MemoryNotFound",
    "MemoryRepository",
    "SENSITIVITIES",
    "SCOPES",
    "SOURCE_TYPES",
    "STATUSES",
    "canonical_key",
    "content_key",
    "normalize_subject",
]
