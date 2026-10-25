-- When the Assistant last raised this task as stuck or still waiting; null = never.
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS followed_up_at TIMESTAMPTZ;
