ALTER TABLE workflow_runs
    ADD COLUMN IF NOT EXISTS origin_message_id VARCHAR(36);

CREATE INDEX IF NOT EXISTS ix_workflow_runs_origin_message_id
    ON workflow_runs(origin_message_id);

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'fk_workflow_runs_origin_message_id'
    ) THEN
        ALTER TABLE workflow_runs
            ADD CONSTRAINT fk_workflow_runs_origin_message_id
            FOREIGN KEY (origin_message_id)
            REFERENCES chat_messages(id)
            ON DELETE SET NULL;
    END IF;
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'uq_workflow_runs_origin_message_id'
    ) THEN
        ALTER TABLE workflow_runs
            ADD CONSTRAINT uq_workflow_runs_origin_message_id
            UNIQUE (origin_message_id);
    END IF;
END
$$;
