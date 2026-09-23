CREATE TABLE IF NOT EXISTS coding_runs (
    task_id VARCHAR(36) PRIMARY KEY REFERENCES tasks(id) ON DELETE CASCADE,
    repository_id VARCHAR(128) NOT NULL,
    github_repository VARCHAR(255) NOT NULL,
    branch VARCHAR(255) NOT NULL,
    base_sha VARCHAR(64),
    phase VARCHAR(32) NOT NULL,
    commit_sha VARCHAR(64),
    pull_request_number INTEGER,
    pull_request_url TEXT,
    pull_request_head_sha VARCHAR(64),
    validation_results JSONB NOT NULL DEFAULT '[]'::jsonb,
    runner_attempts JSONB NOT NULL DEFAULT '[]'::jsonb,
    report JSONB,
    last_error TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
