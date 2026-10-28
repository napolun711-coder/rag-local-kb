-- 长期记忆（memories / sources / events）
--
-- 设计边界：
--   · 记忆正文与来源分离；每次变更写 memory_events（硬删除后审计仍在）；
--   · active 的 canonical_key 唯一；conflicted 必须能与 active 共存；
--   · 软删除时消息/记忆行保留，但 FTS 行移除（fts_indexed=0），避免检索命中；
--   · memories_fts 由 app/features/memory/db.py::_ensure_fts() 创建（tokenizer 运行时探测）。

CREATE TABLE IF NOT EXISTS memories (
    id                TEXT PRIMARY KEY,
    scope             TEXT NOT NULL DEFAULT 'global'
                      CHECK (scope IN ('global','topic')),
    topic_id          TEXT REFERENCES topics(id) ON DELETE SET NULL,
    scope_key         TEXT NOT NULL,
    kind              TEXT NOT NULL
                      CHECK (kind IN ('preference','goal','fact','constraint',
                                      'decision','habit','profile','background')),
    subject           TEXT NOT NULL,
    content           TEXT NOT NULL,
    keywords          TEXT NOT NULL DEFAULT '',
    canonical_key     TEXT NOT NULL,
    status            TEXT NOT NULL DEFAULT 'pending'
                      CHECK (status IN ('active','pending','conflicted',
                                        'superseded','expired','deleted')),
    confidence        REAL NOT NULL DEFAULT 0.5
                      CHECK (confidence >= 0.0 AND confidence <= 1.0),
    source_type       TEXT NOT NULL
                      CHECK (source_type IN ('user_explicit','user_stated',
                                             'user_confirmed','model_inferred','import')),
    sensitivity       TEXT NOT NULL DEFAULT 'normal'
                      CHECK (sensitivity IN ('normal','sensitive','secret')),
    importance        INTEGER NOT NULL DEFAULT 3
                      CHECK (importance BETWEEN 1 AND 5),
    pinned            INTEGER NOT NULL DEFAULT 0,
    user_edited       INTEGER NOT NULL DEFAULT 0,
    valid_from        TEXT,
    valid_to          TEXT,
    last_confirmed_at TEXT,
    superseded_by     TEXT REFERENCES memories(id) ON DELETE SET NULL,
    merged_into       TEXT REFERENCES memories(id) ON DELETE SET NULL,
    version           INTEGER NOT NULL DEFAULT 1,
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL,
    deleted_at        TEXT,
    meta_json         TEXT NOT NULL DEFAULT '{}',
    fts_indexed       INTEGER NOT NULL DEFAULT 1,

    CHECK (
        (scope = 'global' AND topic_id IS NULL AND scope_key = 'global')
        OR
        (scope = 'topic' AND topic_id IS NOT NULL AND scope_key = 'topic:' || topic_id)
    )
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_memories_active_key
    ON memories(canonical_key)
    WHERE status = 'active';

CREATE INDEX IF NOT EXISTS ix_memories_canonical
    ON memories(canonical_key, status);

CREATE INDEX IF NOT EXISTS ix_memories_live
    ON memories(status, scope_key, updated_at DESC);

CREATE INDEX IF NOT EXISTS ix_memories_topic
    ON memories(topic_id, status, updated_at DESC);

CREATE INDEX IF NOT EXISTS ix_memories_pinned
    ON memories(pinned, status, importance DESC);

CREATE TABLE IF NOT EXISTS memory_sources (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    memory_id   TEXT NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
    message_id  TEXT REFERENCES messages(id) ON DELETE SET NULL,
    session_id  TEXT REFERENCES sessions(id) ON DELETE SET NULL,
    quote       TEXT NOT NULL DEFAULT '',
    relation    TEXT NOT NULL DEFAULT 'supports'
                CHECK (relation IN ('supports','contradicts','derived_from','user_edit')),
    created_at  TEXT NOT NULL,
    meta_json   TEXT NOT NULL DEFAULT '{}'
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_memory_sources
    ON memory_sources(memory_id, COALESCE(message_id,''), relation, quote);

CREATE INDEX IF NOT EXISTS ix_memory_sources_memory
    ON memory_sources(memory_id, created_at);

CREATE TABLE IF NOT EXISTS memory_events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    memory_id   TEXT NOT NULL,
    version     INTEGER NOT NULL,
    action      TEXT NOT NULL
                CHECK (action IN ('create','update','confirm','reject','merge',
                                  'split','supersede','expire','delete','restore','redact')),
    actor       TEXT NOT NULL CHECK (actor IN ('user','system','import')),
    reason      TEXT NOT NULL DEFAULT '',
    before_json TEXT,
    after_json  TEXT,
    created_at  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_memory_events_memory
    ON memory_events(memory_id, created_at);

CREATE INDEX IF NOT EXISTS ix_memory_events_time
    ON memory_events(created_at DESC);
