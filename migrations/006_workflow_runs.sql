CREATE TABLE IF NOT EXISTS workflow_runs (
    id VARCHAR(36) PRIMARY KEY,
    workflow_id VARCHAR(128) NOT NULL,
    status VARCHAR(32) NOT NULL,
    workflow_input JSONB NOT NULL DEFAULT '{}'::jsonb,
    current_stage VARCHAR(128),
    chat_session_id VARCHAR(36) REFERENCES chat_sessions(id) ON DELETE SET NULL,
    task_id VARCHAR(36) REFERENCES tasks(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_workflow_runs_workflow_id ON workflow_runs(workflow_id);
CREATE INDEX IF NOT EXISTS ix_workflow_runs_status ON workflow_runs(status);
CREATE INDEX IF NOT EXISTS ix_workflow_runs_status_created ON workflow_runs(status, created_at);
CREATE INDEX IF NOT EXISTS ix_workflow_runs_chat_session_id ON workflow_runs(chat_session_id);
CREATE INDEX IF NOT EXISTS ix_workflow_runs_task_id ON workflow_runs(task_id);

CREATE TABLE IF NOT EXISTS workflow_run_events (
    id BIGSERIAL PRIMARY KEY,
    workflow_run_id VARCHAR(36) NOT NULL REFERENCES workflow_runs(id) ON DELETE CASCADE,
    sequence INTEGER NOT NULL,
    event_type VARCHAR(64) NOT NULL,
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_workflow_run_events_sequence UNIQUE (workflow_run_id, sequence)
);

CREATE INDEX IF NOT EXISTS ix_workflow_run_events_run_sequence
    ON workflow_run_events(workflow_run_id, sequence);
