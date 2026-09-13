CREATE TABLE IF NOT EXISTS tasks (
    id VARCHAR(36) PRIMARY KEY,
    original_request TEXT NOT NULL,
    current_goal TEXT NOT NULL,
    status VARCHAR(32) NOT NULL,
    cancel_requested BOOLEAN NOT NULL DEFAULT FALSE,
    required_capabilities JSONB NOT NULL DEFAULT '[]'::jsonb,
    source_context JSONB NOT NULL DEFAULT '{}'::jsonb,
    external_source VARCHAR(32),
    external_key VARCHAR(255),
    claimed_by VARCHAR(255),
    lease_expires_at TIMESTAMPTZ,
    version INTEGER NOT NULL DEFAULT 1,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_tasks_external_key UNIQUE (external_source, external_key)
);

CREATE INDEX IF NOT EXISTS ix_tasks_status ON tasks(status);
CREATE INDEX IF NOT EXISTS ix_tasks_queue ON tasks(status, created_at);

CREATE TABLE IF NOT EXISTS task_events (
    id BIGSERIAL PRIMARY KEY,
    task_id VARCHAR(36) NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    sequence INTEGER NOT NULL,
    event_type VARCHAR(64) NOT NULL,
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_task_events_sequence UNIQUE (task_id, sequence)
);

CREATE INDEX IF NOT EXISTS ix_task_events_task_sequence
    ON task_events(task_id, sequence);

CREATE TABLE IF NOT EXISTS executions (
    id VARCHAR(36) PRIMARY KEY,
    task_id VARCHAR(36) NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    executor_id VARCHAR(128) NOT NULL,
    status VARCHAR(32) NOT NULL,
    input JSONB NOT NULL DEFAULT '{}'::jsonb,
    output JSONB,
    error TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS ix_executions_task_created
    ON executions(task_id, created_at);
