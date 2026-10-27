DROP INDEX IF EXISTS ix_chat_messages_session_created;
CREATE INDEX IF NOT EXISTS ix_chat_messages_session_created
    ON chat_messages(chat_session_id, created_at, id);
