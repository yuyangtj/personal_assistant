-- A coding run's step plan and the steps finished so far, so a restarted run resumes.
ALTER TABLE coding_runs ADD COLUMN IF NOT EXISTS plan JSONB;
ALTER TABLE coding_runs ADD COLUMN IF NOT EXISTS steps JSONB NOT NULL DEFAULT '[]'::jsonb;
