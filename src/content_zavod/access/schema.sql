CREATE TABLE IF NOT EXISTS members (
    telegram_id BIGINT PRIMARY KEY,
    role TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS join_requests (
    id BIGSERIAL PRIMARY KEY,
    telegram_id BIGINT NOT NULL,
    username TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    resolved_by BIGINT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS join_requests_telegram_id_idx ON join_requests (telegram_id, status);

CREATE TABLE IF NOT EXISTS join_request_broadcasts (
    join_request_id BIGINT NOT NULL REFERENCES join_requests (id),
    owner_telegram_id BIGINT NOT NULL,
    chat_id BIGINT NOT NULL,
    message_id BIGINT NOT NULL,
    PRIMARY KEY (join_request_id, owner_telegram_id)
);
