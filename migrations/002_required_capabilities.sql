ALTER TABLE tasks
    ADD COLUMN IF NOT EXISTS required_capabilities JSONB NOT NULL DEFAULT '[]'::jsonb;
