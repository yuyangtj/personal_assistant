CREATE TABLE IF NOT EXISTS purchases (
    id VARCHAR(36) PRIMARY KEY,
    source VARCHAR(32) NOT NULL,
    occurred_on DATE NOT NULL,
    occurred_time VARCHAR(5) NOT NULL DEFAULT '',
    merchant VARCHAR(200) NOT NULL,
    amount NUMERIC(12, 2) NOT NULL,
    currency VARCHAR(3) NOT NULL,
    original_amount NUMERIC(12, 2),
    payment_type VARCHAR(64) NOT NULL DEFAULT '',
    status VARCHAR(64) NOT NULL DEFAULT '',
    occurrence INTEGER NOT NULL DEFAULT 1,
    imported_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_purchases_identity
        UNIQUE (source, occurred_on, occurred_time, merchant, amount, occurrence)
);
CREATE INDEX IF NOT EXISTS ix_purchases_occurred_on ON purchases(occurred_on);
