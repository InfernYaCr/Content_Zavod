-- Baseline: the tables/columns/indexes every module applied via its own
-- `ensure_schema()` before #71. Every statement here is idempotent
-- (`IF NOT EXISTS`), so this migration is a no-op on an existing
-- installation and a full bootstrap on a fresh database - both converge on
-- the same schema. `plans_one_pending_review_per_week_key` is deliberately
-- left out: an existing installation may already have duplicate
-- `pending_review` Plans for the same week, and creating that unique index
-- here would fail on their data. 0002 dedupes first, then adds it.

-- access
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

-- scheduling
CREATE TABLE IF NOT EXISTS schedule_settings (
    id TEXT PRIMARY KEY DEFAULT 'weekly_plan_trigger',
    day_of_week TEXT NOT NULL,
    hour INT NOT NULL,
    minute INT NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- job_queue
CREATE TABLE IF NOT EXISTS jobs (
    id BIGSERIAL PRIMARY KEY,
    job_type TEXT NOT NULL,
    payload JSONB NOT NULL,
    idempotency_key TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL DEFAULT 'queued',
    attempts INT NOT NULL DEFAULT 0,
    output JSONB,
    error TEXT,
    notified_at TIMESTAMPTZ,
    notification_attempts INT NOT NULL DEFAULT 0,
    notify_locked_at TIMESTAMPTZ,
    run_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    locked_at TIMESTAMPTZ,
    lease_token TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE jobs ADD COLUMN IF NOT EXISTS lease_token TEXT;

CREATE INDEX IF NOT EXISTS jobs_status_run_at_idx ON jobs (status, run_at);
CREATE INDEX IF NOT EXISTS jobs_notified_at_idx ON jobs (notified_at) WHERE notified_at IS NULL;

-- owner_settings
CREATE TABLE IF NOT EXISTS owner_settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- domain
CREATE TABLE IF NOT EXISTS plans (
    id TEXT PRIMARY KEY,
    week_label TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending_review',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS plan_items (
    id TEXT PRIMARY KEY,
    plan_id TEXT NOT NULL REFERENCES plans (id),
    position INT NOT NULL,
    title TEXT NOT NULL,
    summary TEXT NOT NULL DEFAULT '',
    keywords JSONB NOT NULL DEFAULT '[]',
    status TEXT NOT NULL DEFAULT 'pending_review',
    cover_image BYTEA,
    cover_mime_type TEXT,
    cover_generated_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS plan_items_plan_id_position_idx ON plan_items (plan_id, position);

CREATE INDEX IF NOT EXISTS plans_week_label_status_idx ON plans (week_label, status);

CREATE TABLE IF NOT EXISTS articles (
    id TEXT PRIMARY KEY,
    plan_id TEXT NOT NULL REFERENCES plans (id),
    plan_item_id TEXT NOT NULL REFERENCES plan_items (id),
    title TEXT NOT NULL,
    platform TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'queued',
    active_generation_job_id BIGINT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS articles_plan_id_idx ON articles (plan_id, created_at);

CREATE UNIQUE INDEX IF NOT EXISTS articles_plan_item_id_platform_key ON articles (plan_item_id, platform);

CREATE TABLE IF NOT EXISTS article_versions (
    id BIGSERIAL PRIMARY KEY,
    article_id TEXT NOT NULL REFERENCES articles (id),
    content TEXT NOT NULL,
    prompt TEXT NOT NULL,
    model TEXT NOT NULL,
    tokens INT NOT NULL,
    cost NUMERIC NOT NULL,
    source_job_id BIGINT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS article_versions_article_id_idx ON article_versions (article_id, created_at DESC);

ALTER TABLE articles ADD COLUMN IF NOT EXISTS active_generation_job_id BIGINT;

ALTER TABLE article_versions ADD COLUMN IF NOT EXISTS source_job_id BIGINT;

CREATE UNIQUE INDEX IF NOT EXISTS article_versions_source_job_id_key
ON article_versions (source_job_id) WHERE source_job_id IS NOT NULL;
