-- 文档 ↔ topic映射
--
-- 为什么放记忆库：
--   · topics 就在这里，外键与“删除topic → 标签级联删除”最直接；
--   ·  备份/恢复已覆盖 ragkb.db，不需要新增备份目标。
--
-- document_key 是规范化的 file_path（去目录、小写、去首尾空白）；file_path 保留原值，
-- 因为 LightRAG chunk 里带的是原始大小写的文档名。

CREATE TABLE IF NOT EXISTS document_topics (
    document_key  TEXT PRIMARY KEY,
    file_path     TEXT NOT NULL DEFAULT '',
    topic_id      TEXT NOT NULL REFERENCES topics(id) ON DELETE CASCADE,
    document_name TEXT NOT NULL DEFAULT '',
    file_id       TEXT NOT NULL DEFAULT '',
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_document_topics_topic
    ON document_topics(topic_id, updated_at DESC);
