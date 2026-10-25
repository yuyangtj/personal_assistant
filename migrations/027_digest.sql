-- The assistant's rolling summary of recent days across all chats ("recent").
CREATE TABLE IF NOT EXISTS digests (
    id VARCHAR(36) PRIMARY KEY,
    kind VARCHAR(32) NOT NULL UNIQUE,
    content TEXT NOT NULL DEFAULT '',
    through_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
