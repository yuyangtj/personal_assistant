CREATE TABLE IF NOT EXISTS worker_heartbeats (
    worker_id VARCHAR(255) PRIMARY KEY,
    instance_id VARCHAR(36) NOT NULL,
    lane VARCHAR(32) NOT NULL,
    started_at TIMESTAMPTZ NOT NULL,
    heartbeat_at TIMESTAMPTZ NOT NULL,
    stopped_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS ix_worker_heartbeats_freshness
    ON worker_heartbeats (instance_id, heartbeat_at);
