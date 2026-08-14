-- #74: per-step provenance and cost accounting. One row per LLM/image call
-- made while running a Job, written by the worker right after each attempt
-- (success or failure) - so a Job's total cost is SUM(cost) across every
-- attempt, not just the one that eventually succeeded. `cost`/`tokens` are
-- nullable rather than defaulting to 0: a NULL means the provider didn't
-- report usage or pricing isn't configured, and that gap must stay visible
-- instead of silently collapsing into a zero.

CREATE TABLE IF NOT EXISTS generation_steps (
    id BIGSERIAL PRIMARY KEY,
    job_id BIGINT NOT NULL REFERENCES jobs (id),
    job_type TEXT NOT NULL,
    article_id TEXT REFERENCES articles (id),
    step_name TEXT NOT NULL,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    params JSONB NOT NULL DEFAULT '{}',
    prompt_template_version TEXT NOT NULL,
    prompt_hash TEXT NOT NULL,
    tokens INT,
    usage_missing BOOLEAN NOT NULL DEFAULT FALSE,
    latency_ms INT NOT NULL,
    cost NUMERIC,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS generation_steps_job_id_idx ON generation_steps (job_id);

CREATE INDEX IF NOT EXISTS generation_steps_article_id_idx
ON generation_steps (article_id) WHERE article_id IS NOT NULL;
