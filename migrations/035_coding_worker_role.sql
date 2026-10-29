DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'assistant_coding') THEN
        CREATE ROLE assistant_coding NOLOGIN;
    END IF;
    EXECUTE format('GRANT CONNECT ON DATABASE %I TO assistant_coding', current_database());
END
$$;

REVOKE ALL ON SCHEMA public FROM assistant_coding;
GRANT USAGE ON SCHEMA public TO assistant_coding;
REVOKE ALL ON ALL TABLES IN SCHEMA public FROM assistant_coding;
REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM assistant_coding;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA public FROM assistant_coding;

GRANT SELECT ON tasks TO assistant_coding;
-- superseded_by_task_id: a failed PR revision gives the PR its review back.
GRANT UPDATE (
    status, claimed_by, claim_id, lease_expires_at, version, updated_at, superseded_by_task_id
) ON tasks TO assistant_coding;
GRANT SELECT, INSERT ON task_events TO assistant_coding;
GRANT SELECT, INSERT ON executions TO assistant_coding;
GRANT UPDATE (status, output, error, completed_at) ON executions TO assistant_coding;
GRANT SELECT, INSERT, UPDATE ON coding_runs TO assistant_coding;
GRANT SELECT, INSERT, UPDATE ON provider_runtime_states TO assistant_coding;
GRANT SELECT ON workflow_runs TO assistant_coding;
GRANT UPDATE (task_id, status, current_stage, updated_at) ON workflow_runs TO assistant_coding;
GRANT SELECT, INSERT ON workflow_run_events TO assistant_coding;
GRANT SELECT, INSERT, UPDATE ON worker_heartbeats TO assistant_coding;
GRANT USAGE, SELECT ON SEQUENCE task_events_id_seq TO assistant_coding;
GRANT USAGE, SELECT ON SEQUENCE workflow_run_events_id_seq TO assistant_coding;

-- Row security and its policies are set once: ALTER TABLE ... ROW LEVEL SECURITY and
-- CREATE POLICY lock the whole table, and every deploy replays this file. A later change
-- to a policy goes in a new migration that drops and recreates it.
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM pg_policies WHERE policyname = 'assistant_coding_workflow_run_events'
    ) THEN
        RETURN;
    END IF;
    ALTER TABLE tasks ENABLE ROW LEVEL SECURITY;
    CREATE POLICY assistant_coding_tasks ON tasks
        TO assistant_coding
        USING (
            required_capabilities ?| ARRAY['coding', 'pull_request_creation', 'coding-pull-request']
            OR source_context ? 'repository_id'
        )
        WITH CHECK (
            required_capabilities ?| ARRAY['coding', 'pull_request_creation', 'coding-pull-request']
            OR source_context ? 'repository_id'
        );

    ALTER TABLE task_events ENABLE ROW LEVEL SECURITY;
    CREATE POLICY assistant_coding_task_events ON task_events
        TO assistant_coding
        USING (
            EXISTS (
                SELECT 1 FROM tasks
                WHERE tasks.id = task_events.task_id
                  AND (
                      tasks.required_capabilities ?|
                          ARRAY['coding', 'pull_request_creation', 'coding-pull-request']
                      OR tasks.source_context ? 'repository_id'
                  )
            )
        )
        WITH CHECK (
            EXISTS (
                SELECT 1 FROM tasks
                WHERE tasks.id = task_events.task_id
                  AND (
                      tasks.required_capabilities ?|
                          ARRAY['coding', 'pull_request_creation', 'coding-pull-request']
                      OR tasks.source_context ? 'repository_id'
                  )
            )
        );

    ALTER TABLE executions ENABLE ROW LEVEL SECURITY;
    CREATE POLICY assistant_coding_executions ON executions
        TO assistant_coding
        USING (
            EXISTS (
                SELECT 1 FROM tasks
                WHERE tasks.id = executions.task_id
                  AND (
                      tasks.required_capabilities ?|
                          ARRAY['coding', 'pull_request_creation', 'coding-pull-request']
                      OR tasks.source_context ? 'repository_id'
                  )
            )
        )
        WITH CHECK (
            EXISTS (
                SELECT 1 FROM tasks
                WHERE tasks.id = executions.task_id
                  AND (
                      tasks.required_capabilities ?|
                          ARRAY['coding', 'pull_request_creation', 'coding-pull-request']
                      OR tasks.source_context ? 'repository_id'
                  )
            )
        );

    ALTER TABLE coding_runs ENABLE ROW LEVEL SECURITY;
    CREATE POLICY assistant_coding_coding_runs ON coding_runs
        TO assistant_coding
        USING (
            EXISTS (
                SELECT 1 FROM tasks
                WHERE tasks.id = coding_runs.task_id
                  AND (
                      tasks.required_capabilities ?|
                          ARRAY['coding', 'pull_request_creation', 'coding-pull-request']
                      OR tasks.source_context ? 'repository_id'
                  )
            )
        )
        WITH CHECK (
            EXISTS (
                SELECT 1 FROM tasks
                WHERE tasks.id = coding_runs.task_id
                  AND (
                      tasks.required_capabilities ?|
                          ARRAY['coding', 'pull_request_creation', 'coding-pull-request']
                      OR tasks.source_context ? 'repository_id'
                  )
            )
        );

    ALTER TABLE workflow_runs ENABLE ROW LEVEL SECURITY;
    CREATE POLICY assistant_coding_workflow_runs ON workflow_runs
        TO assistant_coding
        USING (
            EXISTS (
                SELECT 1 FROM tasks
                WHERE tasks.id = workflow_runs.task_id
                  AND (
                      tasks.required_capabilities ?|
                          ARRAY['coding', 'pull_request_creation', 'coding-pull-request']
                      OR tasks.source_context ? 'repository_id'
                  )
            )
        )
        WITH CHECK (
            EXISTS (
                SELECT 1 FROM tasks
                WHERE tasks.id = workflow_runs.task_id
                  AND (
                      tasks.required_capabilities ?|
                          ARRAY['coding', 'pull_request_creation', 'coding-pull-request']
                      OR tasks.source_context ? 'repository_id'
                  )
            )
        );

    ALTER TABLE workflow_run_events ENABLE ROW LEVEL SECURITY;
    CREATE POLICY assistant_coding_workflow_run_events ON workflow_run_events
        TO assistant_coding
        USING (
            EXISTS (
                SELECT 1
                FROM workflow_runs
                JOIN tasks ON tasks.id = workflow_runs.task_id
                WHERE workflow_runs.id = workflow_run_events.workflow_run_id
                  AND (
                      tasks.required_capabilities ?|
                          ARRAY['coding', 'pull_request_creation', 'coding-pull-request']
                      OR tasks.source_context ? 'repository_id'
                  )
            )
        )
        WITH CHECK (
            EXISTS (
                SELECT 1
                FROM workflow_runs
                JOIN tasks ON tasks.id = workflow_runs.task_id
                WHERE workflow_runs.id = workflow_run_events.workflow_run_id
                  AND (
                      tasks.required_capabilities ?|
                          ARRAY['coding', 'pull_request_creation', 'coding-pull-request']
                      OR tasks.source_context ? 'repository_id'
                  )
            )
        );
END
$$;

CREATE OR REPLACE FUNCTION public.post_coding_assistant_reply(
    p_task_id VARCHAR(36),
    p_message_id VARCHAR(36),
    p_content TEXT,
    p_blocks JSONB,
    p_emotion VARCHAR(16)
)
RETURNS VOID
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = ''
AS $$
DECLARE
    v_chat_session_id VARCHAR(36);
BEGIN
    SELECT tasks.chat_session_id
    INTO v_chat_session_id
    FROM public.tasks
    WHERE tasks.id = p_task_id
      AND tasks.chat_session_id IS NOT NULL
      AND (
          tasks.required_capabilities ?|
              ARRAY['coding', 'pull_request_creation', 'coding-pull-request']
          OR tasks.source_context ? 'repository_id'
      );

    IF v_chat_session_id IS NULL THEN
        RAISE EXCEPTION 'task is not a coding task linked to a chat';
    END IF;

    INSERT INTO public.chat_messages (
        id,
        chat_session_id,
        role,
        content,
        linked_task_id,
        blocks,
        emotion
    ) VALUES (
        p_message_id,
        v_chat_session_id,
        'assistant',
        p_content,
        p_task_id,
        p_blocks,
        p_emotion
    );

    UPDATE public.chat_sessions
    SET updated_at = pg_catalog.now()
    WHERE id = v_chat_session_id;
END;
$$;

REVOKE ALL ON FUNCTION public.post_coding_assistant_reply(
    VARCHAR, VARCHAR, TEXT, JSONB, VARCHAR
) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.post_coding_assistant_reply(
    VARCHAR, VARCHAR, TEXT, JSONB, VARCHAR
) FROM assistant_coding;
GRANT EXECUTE ON FUNCTION public.post_coding_assistant_reply(
    VARCHAR, VARCHAR, TEXT, JSONB, VARCHAR
) TO assistant_coding;
