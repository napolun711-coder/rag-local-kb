-- Automatic memory supervision findings and scan metadata.

CREATE TABLE IF NOT EXISTS memory_supervisor_findings (
    id            TEXT PRIMARY KEY,
    memory_id     TEXT,
    code          TEXT NOT NULL,
    severity      TEXT NOT NULL
                  CHECK (severity IN ('info','warning','high','critical')),
    status        TEXT NOT NULL DEFAULT 'open'
                  CHECK (status IN ('open','resolved','ignored')),
    message       TEXT NOT NULL DEFAULT '',
    detail_json   TEXT NOT NULL DEFAULT '{}',
    fingerprint   TEXT NOT NULL UNIQUE,
    detected_at   TEXT NOT NULL,
    updated_at    TEXT NOT NULL,
    resolved_at   TEXT
);

CREATE INDEX IF NOT EXISTS ix_memory_supervisor_findings_status
    ON memory_supervisor_findings(status, severity, detected_at DESC);

CREATE INDEX IF NOT EXISTS ix_memory_supervisor_findings_memory
    ON memory_supervisor_findings(memory_id, detected_at DESC);

CREATE TABLE IF NOT EXISTS memory_supervisor_meta (
    meta_key     TEXT PRIMARY KEY,
    value        TEXT NOT NULL DEFAULT '',
    updated_at   TEXT NOT NULL
);