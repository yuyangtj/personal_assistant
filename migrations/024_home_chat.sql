-- The one "Assistant" conversation the app opens in; there is at most one.
ALTER TABLE chat_sessions ADD COLUMN IF NOT EXISTS home BOOLEAN NOT NULL DEFAULT FALSE;
CREATE UNIQUE INDEX IF NOT EXISTS ux_chat_sessions_home ON chat_sessions (home) WHERE home;
