CREATE TABLE IF NOT EXISTS chat_messages (
    id VARCHAR(36) PRIMARY KEY,
    chat_session_id VARCHAR(36) NOT NULL
        REFERENCES chat_sessions(id) ON DELETE CASCADE,
    role VARCHAR(16) NOT NULL,
    content TEXT NOT NULL,
    linked_task_id VARCHAR(36)
        REFERENCES tasks(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT ck_chat_messages_role
        CHECK (role IN ('user', 'assistant', 'system'))
);

CREATE INDEX IF NOT EXISTS ix_chat_messages_session_created
    ON chat_messages(chat_session_id, created_at);

CREATE INDEX IF NOT EXISTS ix_chat_messages_linked_task_id
    ON chat_messages(linked_task_id);

ALTER TABLE tasks
    ADD COLUMN IF NOT EXISTS origin_message_id VARCHAR(36);

CREATE INDEX IF NOT EXISTS ix_tasks_origin_message_id
    ON tasks(origin_message_id);

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'fk_tasks_origin_message_id'
    ) THEN
        ALTER TABLE tasks
            ADD CONSTRAINT fk_tasks_origin_message_id
            FOREIGN KEY (origin_message_id)
            REFERENCES chat_messages(id)
            ON DELETE SET NULL;
    END IF;
END
$$;
