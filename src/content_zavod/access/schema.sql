CREATE TABLE IF NOT EXISTS members (
    telegram_id BIGINT PRIMARY KEY,
    role TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
