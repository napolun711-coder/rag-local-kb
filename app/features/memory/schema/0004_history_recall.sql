-- 历史召回抑制（彻底忘记后，原始消息不再被历史检索返回）
--
-- 不存 quote 正文，只存消息 ID 与 quote 的 SHA-256；消息被删后也无法从本表复原内容。

CREATE TABLE IF NOT EXISTS memory_suppressions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    memory_id   TEXT NOT NULL,
    message_id  TEXT,
    session_id  TEXT,
    quote_hash  TEXT,
    reason      TEXT NOT NULL DEFAULT 'forget',
    created_at  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_memory_suppressions_message
    ON memory_suppressions(message_id);

CREATE INDEX IF NOT EXISTS ix_memory_suppressions_memory
    ON memory_suppressions(memory_id);
