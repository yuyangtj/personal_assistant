-- Rebuild the index with id only while it still lacks it: every deploy replays this file,
-- and dropping it each time would lock chat_messages while it rebuilds.
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM pg_indexes
        WHERE indexname = 'ix_chat_messages_session_created'
          AND indexdef NOT LIKE '%(chat_session_id, created_at, id)%'
    ) THEN
        DROP INDEX ix_chat_messages_session_created;
    END IF;
END
$$;
CREATE INDEX IF NOT EXISTS ix_chat_messages_session_created
    ON chat_messages(chat_session_id, created_at, id);
