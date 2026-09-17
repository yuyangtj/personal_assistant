CREATE TABLE IF NOT EXISTS chat_sessions (
    id VARCHAR(36) PRIMARY KEY,
    title VARCHAR(160) NOT NULL,
    archived BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

ALTER TABLE tasks
    ADD COLUMN IF NOT EXISTS chat_session_id VARCHAR(36);

CREATE INDEX IF NOT EXISTS ix_tasks_chat_session_id
    ON tasks(chat_session_id);

CREATE INDEX IF NOT EXISTS ix_chat_sessions_updated_at
    ON chat_sessions(updated_at DESC);

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'fk_tasks_chat_session_id'
    ) THEN
        ALTER TABLE tasks
            ADD CONSTRAINT fk_tasks_chat_session_id
            FOREIGN KEY (chat_session_id)
            REFERENCES chat_sessions(id)
            ON DELETE SET NULL;
    END IF;
END
$$;
