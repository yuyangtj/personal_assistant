-- A chat whose title is a long first message cut short; the worker asks a model for a
-- short one. Existing long titles get one too, once: only when the column is new, since
-- every migration runs again on each deploy.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'chat_sessions' AND column_name = 'title_pending'
    ) THEN
        ALTER TABLE chat_sessions ADD COLUMN title_pending BOOLEAN NOT NULL DEFAULT FALSE;
        UPDATE chat_sessions SET title_pending = TRUE WHERE NOT home AND length(title) > 32;
    END IF;
END $$;
