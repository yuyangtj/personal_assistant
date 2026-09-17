ALTER TABLE tasks
    ADD COLUMN IF NOT EXISTS parent_task_id VARCHAR(36);

CREATE INDEX IF NOT EXISTS ix_tasks_parent_task_id
    ON tasks(parent_task_id);

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'fk_tasks_parent_task_id'
    ) THEN
        ALTER TABLE tasks
            ADD CONSTRAINT fk_tasks_parent_task_id
            FOREIGN KEY (parent_task_id)
            REFERENCES tasks(id)
            ON DELETE SET NULL;
    END IF;
END
$$;
