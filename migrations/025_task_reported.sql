-- When finished background work was reported to the Assistant conversation; null = not yet.
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS reported_at TIMESTAMPTZ;
-- Work that finished before reporting existed is not reported now.
UPDATE tasks SET reported_at = now()
  WHERE reported_at IS NULL
    AND status IN ('completed', 'failed', 'cancelled', 'superseded', 'waiting_for_approval');
