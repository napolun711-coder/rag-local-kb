"""SQLite 连接、迁移与 FTS 索引。

设计约束：
- 零新依赖：只用标准库 `sqlite3`；
- 每次操作短连接（WAL 允许多读一写）；写操作由进程内锁 + `BEGIN IMMEDIATE` 串行化；
- `RAGKB_MEMORY_ENABLED=off` 时不会走到这里（runtime 先判断）；
- FTS5 不可用时降级为“无索引”而不是让应用启动失败； 不依赖 FTS 的查询接口。
"""

from __future__ import annotations

import hashlib
import logging
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterator, Optional

from app.platform.paths import BASE_DIR, MEMORY_DIR

logger = logging.getLogger(__name__)

SCHEMA_DIR = Path(__file__).resolve().parent / "schema"
DEFAULT_DB_PATH = MEMORY_DIR / "ragkb.db"
SCHEMA_VERSION = 6


def resolve_db_path(value: str = "") -> Path:
    """把配置值收敛成绝对路径；空值 = data/memory/ragkb.db。"""
    text = str(value or "").strip()
    if not text:
        return DEFAULT_DB_PATH
    path = Path(text).expanduser()
    if not path.is_absolute():
        path = BASE_DIR / path
    return path


def _checksum(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class MemoryDatabase:
    """数据库门面：初始化、迁移、事务与状态。"""

    def __init__(self, path: Optional[Path | str] = None) -> None:
        self.path = Path(path) if path is not None else DEFAULT_DB_PATH
        self._lock = threading.RLock()
        self._ready = False
        self._error = ""
        self.fts_available = False
        self.memories_fts_available = False
        self.fts_tokenizer = ""

    # ============================================================
    # 初始化 / 迁移
    # ============================================================

    @property
    def ready(self) -> bool:
        return self._ready

    @property
    def error(self) -> str:
        return self._error

    def initialize(self) -> None:
        """创建目录、开 WAL、执行迁移、做一次快速完整性检查。"""
        if self._ready:
            return
        with self._lock:
            if self._ready:
                return
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with self.connect() as conn:
                    # 这些 PRAGMA 对当前连接生效；WAL 会写进数据库文件。
                    conn.execute("PRAGMA journal_mode=WAL")
                    conn.execute("PRAGMA foreign_keys=ON")
                    conn.execute("PRAGMA synchronous=NORMAL")
                    conn.execute("PRAGMA busy_timeout=5000")
                    self._migrate(conn)
                    self._ensure_fts(conn)
                    check = conn.execute("PRAGMA quick_check").fetchone()
                    if not check or str(check[0]).lower() != "ok":
                        raise RuntimeError("SQLite quick_check 未通过")
                self._ready = True
                self._error = ""
            except Exception as exc:  # noqa: BLE001 - 由 runtime 统一降级
                self._ready = False
                self._error = f"{type(exc).__name__}: {exc}"
                raise

    def _migrate(self, conn: sqlite3.Connection) -> None:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version    INTEGER PRIMARY KEY,
                applied_at TEXT NOT NULL,
                checksum   TEXT NOT NULL
            )
            """
        )
        row = conn.execute("SELECT MAX(version) FROM schema_migrations").fetchone()
        current = int(row[0] or 0) if row else 0
        if current > SCHEMA_VERSION:
            # 旧代码不要写新库：宁可禁用记忆，也不要用旧列定义破坏数据。
            raise RuntimeError(
                f"数据库 schema v{current} 高于当前代码支持的 v{SCHEMA_VERSION}"
            )

        for path in sorted(SCHEMA_DIR.glob("*.sql")):
            try:
                version = int(path.stem.split("_", 1)[0])
            except ValueError:
                logger.warning("忽略无法识别版本号的迁移文件：%s", path.name)
                continue
            if version <= current:
                continue
            sql = path.read_text(encoding="utf-8")
            # executescript 会先提交当前事务；这里的 DDL 都带 IF NOT EXISTS，
            # 即使进程在“执行完 SQL、写迁移记录前”崩溃，重跑也不会重复建表。
            conn.executescript(sql)
            from app.features.memory.util import utc_now

            conn.execute(
                "INSERT OR REPLACE INTO schema_migrations(version, applied_at, checksum) "
                "VALUES (?, ?, ?)",
                (version, utc_now(), _checksum(sql)),
            )
            conn.commit()
            current = version
            logger.info("记忆库迁移完成：v%d（%s）", version, path.name)

    def _ensure_fts(self, conn: sqlite3.Connection) -> None:
        """创建 FTS5 表与同步触发器；失败时降级为无索引。"""
        created_messages = False
        existing = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='messages_fts'"
        ).fetchone()
        if existing:
            sql = str(existing[0] or "")
            self.fts_tokenizer = "trigram" if "trigram" in sql else "unicode61"
            self.fts_available = True
        else:
            for tokenizer in ("trigram", "unicode61"):
                try:
                    conn.execute(
                        "CREATE VIRTUAL TABLE messages_fts USING fts5("
                        "content, content='messages', content_rowid='rowid', "
                        f"tokenize='{tokenizer}')"
                    )
                    self.fts_tokenizer = tokenizer
                    self.fts_available = True
                    created_messages = True
                    break
                except sqlite3.OperationalError as exc:
                    logger.warning(
                        "FTS5 tokenizer=%s 不可用：%s", tokenizer, type(exc).__name__
                    )
            if not self.fts_available:
                logger.warning("SQLite 未编译 FTS5：对话仍可保存，历史检索会自动降级")
                conn.commit()
                return

        conn.executescript(
            """
            CREATE TRIGGER IF NOT EXISTS messages_ai
            AFTER INSERT ON messages BEGIN
                INSERT INTO messages_fts(rowid, content) VALUES (new.rowid, new.content);
            END;

            CREATE TRIGGER IF NOT EXISTS messages_ad
            AFTER DELETE ON messages
            WHEN old.fts_indexed = 1
            BEGIN
                INSERT INTO messages_fts(messages_fts, rowid, content)
                VALUES ('delete', old.rowid, old.content);
            END;

            CREATE TRIGGER IF NOT EXISTS messages_au
            AFTER UPDATE OF content ON messages
            WHEN old.fts_indexed = 1
            BEGIN
                INSERT INTO messages_fts(messages_fts, rowid, content)
                VALUES ('delete', old.rowid, old.content);
                INSERT INTO messages_fts(rowid, content) VALUES (new.rowid, new.content);
            END;
            """
        )

        # FTS 表刚建/刚重建时，必须从主表回填；只建空表会让 MATCH 永远 0 命中。
        if created_messages:
            try:
                conn.execute("INSERT INTO messages_fts(messages_fts) VALUES('rebuild')")
            except sqlite3.OperationalError as exc:
                logger.warning(
                    "messages FTS 回填失败（降级为后续写入）：%s", type(exc).__name__
                )

        # memories 表存在时，为记忆内容建第二张 FTS。tokenizer 与 messages_fts 一致。
        memories_table = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='memories'"
        ).fetchone()
        if memories_table:
            existing_mem = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='memories_fts'"
            ).fetchone()
            if not existing_mem:
                try:
                    conn.execute(
                        "CREATE VIRTUAL TABLE memories_fts USING fts5("
                        "subject, content, keywords, content='memories', "
                        "content_rowid='rowid', "
                        f"tokenize='{self.fts_tokenizer}')"
                    )
                    self.memories_fts_available = True
                    try:
                        conn.execute(
                            "INSERT INTO memories_fts(memories_fts) VALUES('rebuild')"
                        )
                    except sqlite3.OperationalError as exc:
                        logger.warning(
                            "memories FTS 回填失败（降级为后续写入）：%s",
                            type(exc).__name__,
                        )
                except sqlite3.OperationalError as exc:
                    logger.warning(
                        "memories FTS 创建失败（降级为 LIKE）：%s", type(exc).__name__
                    )
            else:
                self.memories_fts_available = True

            if self.memories_fts_available:
                conn.executescript(
                    """
                    CREATE TRIGGER IF NOT EXISTS memories_ai
                    AFTER INSERT ON memories BEGIN
                        INSERT INTO memories_fts(rowid, subject, content, keywords)
                        VALUES (new.rowid, new.subject, new.content, new.keywords);
                    END;

                    CREATE TRIGGER IF NOT EXISTS memories_ad
                    AFTER DELETE ON memories
                    WHEN old.fts_indexed = 1
                    BEGIN
                        INSERT INTO memories_fts(
                            memories_fts, rowid, subject, content, keywords
                        ) VALUES (
                            'delete', old.rowid, old.subject, old.content, old.keywords
                        );
                    END;

                    CREATE TRIGGER IF NOT EXISTS memories_au
                    AFTER UPDATE OF subject, content, keywords ON memories
                    WHEN old.fts_indexed = 1
                    BEGIN
                        INSERT INTO memories_fts(
                            memories_fts, rowid, subject, content, keywords
                        ) VALUES (
                            'delete', old.rowid, old.subject, old.content, old.keywords
                        );
                        INSERT INTO memories_fts(rowid, subject, content, keywords)
                        VALUES (new.rowid, new.subject, new.content, new.keywords);
                    END;
                    """
                )
        conn.commit()

    # ============================================================
    # 连接 / 事务
    # ============================================================

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        """短连接；读操作直接使用。"""
        conn = sqlite3.connect(str(self.path), timeout=5.0, isolation_level=None)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("PRAGMA busy_timeout=5000")
            yield conn
        finally:
            conn.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """写事务：进程内锁 + BEGIN IMMEDIATE，避免同一进程并发写互相打架。"""
        with self._lock:
            conn = sqlite3.connect(str(self.path), timeout=5.0, isolation_level=None)
            conn.row_factory = sqlite3.Row
            try:
                conn.execute("PRAGMA foreign_keys=ON")
                conn.execute("PRAGMA busy_timeout=5000")
                conn.execute("BEGIN IMMEDIATE")
                try:
                    yield conn
                    conn.commit()
                except Exception:
                    conn.rollback()
                    raise
            finally:
                conn.close()

    # ============================================================
    # 状态
    # ============================================================

    def status(self) -> Dict[str, Any]:
        result: Dict[str, Any] = {
            "ready": self._ready,
            "error": self._error,
            "path": str(self.path),
            "fts_available": self.fts_available,
            "memories_fts_available": self.memories_fts_available,
            "fts_tokenizer": self.fts_tokenizer,
            "schema_version": None,
            "session_count": 0,
            "message_count": 0,
            "deleted_session_count": 0,
            "deleted_message_count": 0,
        }
        if not self._ready:
            return result
        try:
            with self.connect() as conn:
                row = conn.execute("SELECT MAX(version) FROM schema_migrations").fetchone()
                result["schema_version"] = int(row[0] or 0) if row else 0
                result["session_count"] = int(
                    conn.execute(
                        "SELECT COUNT(*) FROM sessions WHERE status != 'deleted'"
                    ).fetchone()[0]
                )
                result["message_count"] = int(
                    conn.execute(
                        "SELECT COUNT(*) FROM messages m "
                        "JOIN sessions s ON s.id = m.session_id "
                        "WHERE s.status != 'deleted'"
                    ).fetchone()[0]
                )
                result["deleted_session_count"] = int(
                    conn.execute(
                        "SELECT COUNT(*) FROM sessions WHERE status = 'deleted'"
                    ).fetchone()[0]
                )
                result["deleted_message_count"] = int(
                    conn.execute(
                        "SELECT COUNT(*) FROM messages m "
                        "JOIN sessions s ON s.id = m.session_id "
                        "WHERE s.status = 'deleted'"
                    ).fetchone()[0]
                )
        except Exception as exc:  # noqa: BLE001 - 状态查询失败不能反噬主流程
            result["error"] = f"{type(exc).__name__}: {exc}"
        return result

    def close(self) -> None:
        """短连接模式没有常驻连接；保留接口给 runtime/lifespan 调用。"""
        return None
