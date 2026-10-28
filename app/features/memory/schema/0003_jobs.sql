-- 记忆任务队列与会话提取水位
--
-- 设计边界：
--   · priority 越小越优先（1=立即，3=轮次阈值，7=去抖）；
--   · dedupe_key 非空时，同一时刻只允许一个 queued/running 任务；
--   · running 任务必须有 lease_until；启动时把过期租约恢复为 queued；
--   · payload/result 只存 ID/范围/状态，不存消息正文；
--   · 会话彻底删除时任务随外键级联删除，软删除时由应用层取消。

CREATE TABLE IF NOT EXISTS memory_jobs (
    id            TEXT PRIMARY KEY,
    kind          TEXT NOT NULL,
    status        TEXT NOT NULL DEFAULT 'queued'
                  CHECK (status IN ('queued','running','done','failed','cancelled')),
    priority      INTEGER NOT NULL DEFAULT 7,
    session_id    TEXT REFERENCES sessions(id) ON DELETE CASCADE,
    dedupe_key    TEXT NOT NULL DEFAULT '',
    payload_json  TEXT NOT NULL DEFAULT '{}',
    result_json   TEXT NOT NULL DEFAULT '{}',
    attempts      INTEGER NOT NULL DEFAULT 0,
    max_attempts  INTEGER NOT NULL DEFAULT 5,
    worker_id     TEXT NOT NULL DEFAULT '',
    last_error    TEXT NOT NULL DEFAULT '',
    available_at  TEXT NOT NULL,
    lease_until   TEXT,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL,
    completed_at  TEXT
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_memory_jobs_active_dedupe
    ON memory_jobs(dedupe_key)
    WHERE dedupe_key <> '' AND status IN ('queued','running');

CREATE INDEX IF NOT EXISTS ix_memory_jobs_ready
    ON memory_jobs(status, available_at, priority, created_at);

CREATE INDEX IF NOT EXISTS ix_memory_jobs_session
    ON memory_jobs(session_id, status, created_at DESC);

CREATE TABLE IF NOT EXISTS session_memory_state (
    session_id          TEXT PRIMARY KEY REFERENCES sessions(id) ON DELETE CASCADE,
    last_extracted_seq  INTEGER NOT NULL DEFAULT 0,
    updated_at          TEXT NOT NULL
);
