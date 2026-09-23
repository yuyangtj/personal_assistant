ALTER TABLE tasks
    ADD COLUMN IF NOT EXISTS superseded_by_task_id VARCHAR(36)
    REFERENCES tasks(id) ON DELETE SET NULL;
CREATE INDEX IF NOT EXISTS ix_tasks_superseded_by_task_id
    ON tasks(superseded_by_task_id);
