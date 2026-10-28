-- 原始对话持久化（sessions / messages / topics）
--
-- FTS 表（messages_fts）与触发器不在这里创建：tokenizer 需要运行时探测
-- （优先 trigram，不可用时回落 unicode61），见 app/features/memory/db.py::_ensure_fts()。
--
-- 约定：
--   · 时间统一 ISO-8601 UTC 字符串；
--   · 主键是 TEXT 业务 ID，FTS 使用 SQLite 隐式 rowid；
--   · 不在这里执行 VACUUM（外部内容 FTS 依赖稳定 rowid）。

CREATE TABLE IF NOT EXISTS topics (
    id            TEXT PRIMARY KEY,
    name          TEXT NOT NULL,
    aliases_json  TEXT NOT NULL DEFAULT '[]',
    kind          TEXT NOT NULL DEFAULT 'project'
                  CHECK (kind IN ('project','topic','person','other')),
    description   TEXT NOT NULL DEFAULT '',
    status        TEXT NOT NULL DEFAULT 'active'
                  CHECK (status IN ('active','archived')),
    parent_id     TEXT REFERENCES topics(id) ON DELETE SET NULL,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL,
    meta_json     TEXT NOT NULL DEFAULT '{}'
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_topics_name
    ON topics(name COLLATE NOCASE);

CREATE TABLE IF NOT EXISTS sessions (
    id                TEXT PRIMARY KEY,
    title             TEXT NOT NULL DEFAULT '',
    topic_id          TEXT REFERENCES topics(id) ON DELETE SET NULL,
    client_session_id TEXT,
    status            TEXT NOT NULL DEFAULT 'active'
                      CHECK (status IN ('active','archived','deleted')),
    message_count     INTEGER NOT NULL DEFAULT 0,
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL,
    last_message_at   TEXT,
    deleted_at        TEXT,
    meta_json         TEXT NOT NULL DEFAULT '{}'
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_sessions_client
    ON sessions(client_session_id)
    WHERE client_session_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS ix_sessions_updated
    ON sessions(status, updated_at DESC);

CREATE INDEX IF NOT EXISTS ix_sessions_topic
    ON sessions(topic_id, updated_at DESC);

CREATE TABLE IF NOT EXISTS messages (
    id                TEXT PRIMARY KEY,
    session_id        TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    seq               INTEGER NOT NULL,
    role              TEXT NOT NULL
                      CHECK (role IN ('user','assistant','system','tool')),
    content           TEXT NOT NULL,
    status            TEXT NOT NULL DEFAULT 'complete'
                      CHECK (status IN ('complete','streaming','partial','error')),
    source            TEXT NOT NULL DEFAULT '',
    model             TEXT NOT NULL DEFAULT '',
    request_id        TEXT,
    client_message_id TEXT,
    reply_to_id       TEXT REFERENCES messages(id) ON DELETE SET NULL,
    created_at        TEXT NOT NULL,
    completed_at      TEXT,
    updated_at        TEXT NOT NULL,
    token_count       INTEGER,
    references_json   TEXT NOT NULL DEFAULT '[]',
    meta_json         TEXT NOT NULL DEFAULT '{}',
    -- 软删除时消息行保留、FTS 行移除；用此标记避免彻底删除时触发器
    -- 再次删除一个已经不在 FTS 里的 rowid（SQLite 会报 malformed）。
    fts_indexed       INTEGER NOT NULL DEFAULT 1
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_messages_seq
    ON messages(session_id, seq);

CREATE UNIQUE INDEX IF NOT EXISTS ux_messages_client
    ON messages(session_id, client_message_id)
    WHERE client_message_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS ix_messages_session_time
    ON messages(session_id, created_at);

CREATE INDEX IF NOT EXISTS ix_messages_status
    ON messages(status, updated_at);

CREATE INDEX IF NOT EXISTS ix_messages_request
    ON messages(session_id, request_id, role);
