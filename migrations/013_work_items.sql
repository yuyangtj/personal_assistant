-- Durable work items: the goals that chats and runs (tasks) attach to.
CREATE TABLE IF NOT EXISTS spaces (
    id VARCHAR(36) PRIMARY KEY,
    slug VARCHAR(64) NOT NULL UNIQUE,
    name VARCHAR(120) NOT NULL,
    kind VARCHAR(32) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS work_items (
    id VARCHAR(36) PRIMARY KEY,
    space_id VARCHAR(36) NOT NULL REFERENCES spaces(id),
    slug VARCHAR(80) NOT NULL UNIQUE,
    kind VARCHAR(16) NOT NULL,
    title VARCHAR(160) NOT NULL,
    status VARCHAR(16) NOT NULL,
    brief JSONB NOT NULL DEFAULT '{}'::jsonb,
    checklist JSONB NOT NULL DEFAULT '[]'::jsonb,
    links JSONB NOT NULL DEFAULT '[]'::jsonb,
    brief_synced_at TIMESTAMPTZ,
    version INTEGER NOT NULL DEFAULT 1,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT ck_work_items_kind CHECK (kind IN ('goal', 'list', 'routine', 'watch')),
    CONSTRAINT ck_work_items_status
        CHECK (status IN ('open', 'active', 'blocked', 'done', 'archived'))
);

CREATE INDEX IF NOT EXISTS ix_work_items_space_status_updated
    ON work_items(space_id, status, updated_at DESC);

CREATE TABLE IF NOT EXISTS work_item_events (
    id BIGSERIAL PRIMARY KEY,
    work_item_id VARCHAR(36) NOT NULL REFERENCES work_items(id) ON DELETE CASCADE,
    sequence INTEGER NOT NULL,
    event_type VARCHAR(64) NOT NULL,
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    chat_session_id VARCHAR(36) REFERENCES chat_sessions(id) ON DELETE SET NULL,
    task_id VARCHAR(36) REFERENCES tasks(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_work_item_events_sequence UNIQUE (work_item_id, sequence)
);

CREATE INDEX IF NOT EXISTS ix_work_item_events_created ON work_item_events(created_at);

CREATE TABLE IF NOT EXISTS chat_work_items (
    chat_session_id VARCHAR(36) NOT NULL REFERENCES chat_sessions(id) ON DELETE CASCADE,
    work_item_id VARCHAR(36) NOT NULL REFERENCES work_items(id) ON DELETE CASCADE,
    focused_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (chat_session_id, work_item_id)
);

CREATE INDEX IF NOT EXISTS ix_chat_work_items_work_item ON chat_work_items(work_item_id);

ALTER TABLE tasks
    ADD COLUMN IF NOT EXISTS work_item_id VARCHAR(36);

CREATE INDEX IF NOT EXISTS ix_tasks_work_item_id ON tasks(work_item_id);

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'fk_tasks_work_item_id'
    ) THEN
        ALTER TABLE tasks
            ADD CONSTRAINT fk_tasks_work_item_id
            FOREIGN KEY (work_item_id)
            REFERENCES work_items(id)
            ON DELETE SET NULL;
    END IF;
END
$$;
