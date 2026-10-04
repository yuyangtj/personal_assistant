-- Reminders and routines, optionally tied to a work item and the chat that set them.
CREATE TABLE IF NOT EXISTS schedules (
    id VARCHAR(36) PRIMARY KEY,
    kind VARCHAR(16) NOT NULL,
    message TEXT NOT NULL,
    work_item_id VARCHAR(36) REFERENCES work_items(id) ON DELETE CASCADE,
    chat_session_id VARCHAR(36) REFERENCES chat_sessions(id) ON DELETE SET NULL,
    next_run_at TIMESTAMPTZ NOT NULL,
    recurrence VARCHAR(16) NOT NULL DEFAULT 'none',
    timezone VARCHAR(64) NOT NULL DEFAULT 'UTC',
    active BOOLEAN NOT NULL DEFAULT TRUE,
    last_run_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT ck_schedules_kind CHECK (kind IN ('reminder', 'routine')),
    CONSTRAINT ck_schedules_recurrence
        CHECK (recurrence IN ('none', 'daily', 'weekdays', 'weekly', 'monthly'))
);

CREATE INDEX IF NOT EXISTS ix_schedules_due ON schedules(active, next_run_at);
CREATE INDEX IF NOT EXISTS ix_schedules_work_item ON schedules(work_item_id);
