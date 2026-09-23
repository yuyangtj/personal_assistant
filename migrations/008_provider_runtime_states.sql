CREATE TABLE IF NOT EXISTS provider_runtime_states (
    provider_key VARCHAR(128) PRIMARY KEY,
    cooldown_until TIMESTAMPTZ,
    last_error_category VARCHAR(64),
    consecutive_failures INTEGER NOT NULL DEFAULT 0,
    last_success_at TIMESTAMPTZ,
    last_failure_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_provider_runtime_states_cooldown_until
    ON provider_runtime_states(cooldown_until);
