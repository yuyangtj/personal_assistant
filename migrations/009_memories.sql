CREATE TABLE IF NOT EXISTS memories (
    id VARCHAR(36) PRIMARY KEY,
    kind VARCHAR(32) NOT NULL,
    content TEXT NOT NULL,
    tags JSON NOT NULL,
    source_chat_session_id VARCHAR(36) REFERENCES chat_sessions(id) ON DELETE SET NULL,
    source_task_id VARCHAR(36) REFERENCES tasks(id) ON DELETE SET NULL,
    active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS ix_memories_active_created ON memories(active, created_at);
