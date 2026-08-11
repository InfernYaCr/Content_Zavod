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
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS article_versions_article_id_idx ON article_versions (article_id, created_at DESC);
