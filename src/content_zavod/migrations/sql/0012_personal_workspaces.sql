-- Personal Workspaces (#118, ADR-0015): one shared schema, `workspace_id` on every user row.
--
-- NOT an expand step (ADR-0013): code from before this migration can't run against it
-- (`owner_settings` is renamed, every INSERT now needs `workspace_id NOT NULL`). Deploy =
-- stop `bot` and `worker`, `pg_dump`, start both on the new image.
--
-- Clean slate, not a backfill - the project owner's decision (#118): current Plans, Статьи,
-- Jobs, Настройки, waits and the Расписание are deleted. Deliberately no RESTART IDENTITY:
-- `jobs.id`/`article_versions.id` keep counting, so a «🔁 Повторить» button from before
-- carries a job_id that no new Job will ever have.
--
-- `members`, `join_requests`, `join_request_broadcasts` stay: the bot still uses them until
-- the Telegram PR, whose `0013_drop_roles.sql` drops them. Likewise the `team_note` key in
-- `bot_state` stays until 0013 - the team chat keeps its pinned note instead of getting a
-- second one.
--
-- Rollback: only by restoring the pre-deploy `pg_dump` - the data is gone by decision.

TRUNCATE plans, plan_items, articles, article_versions, generation_steps, topic_research,
    jobs, pending_inputs, schedule_settings;

CREATE TABLE workspaces (
    id BIGSERIAL PRIMARY KEY,
    owner_telegram_id BIGINT NOT NULL UNIQUE,
    owner_username TEXT,
    timezone TEXT NOT NULL,
    plan_limit INT,
    article_limit INT,
    bot_blocked_at TIMESTAMPTZ,
    last_job_claimed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- owner_settings splits in two: Настройки and the Онбординг flag move per Workspace, what is
-- left is the bot's own global state (Инструкция picture file_ids, the Telegraph token).
CREATE TABLE workspace_settings (
    workspace_id BIGINT NOT NULL REFERENCES workspaces (id),
    key TEXT NOT NULL,
    value TEXT NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (workspace_id, key)
);

ALTER TABLE owner_settings RENAME TO bot_state;
ALTER INDEX owner_settings_pkey RENAME TO bot_state_pkey;
DELETE FROM bot_state
WHERE key NOT LIKE 'asset_file_id:%' AND key NOT IN ('telegraph_access_token', 'team_note');

-- jobs: the idempotency key is unique per Workspace (two Workspaces' `generate_plan:2026-W42`
-- must be two Jobs); (workspace_id, id) is the target of the composite FKs below.
ALTER TABLE jobs ADD COLUMN workspace_id BIGINT NOT NULL REFERENCES workspaces (id);
ALTER TABLE jobs DROP CONSTRAINT jobs_idempotency_key_key;
ALTER TABLE jobs ADD CONSTRAINT jobs_workspace_id_idempotency_key_key
    UNIQUE (workspace_id, idempotency_key);
ALTER TABLE jobs ADD CONSTRAINT jobs_workspace_id_id_key UNIQUE (workspace_id, id);
CREATE INDEX jobs_workspace_id_status_idx ON jobs (workspace_id, status);

-- plans
ALTER TABLE plans ADD COLUMN workspace_id BIGINT NOT NULL REFERENCES workspaces (id);
ALTER TABLE plans ADD CONSTRAINT plans_workspace_id_id_key UNIQUE (workspace_id, id);
DROP INDEX plans_one_pending_review_per_week_key;
CREATE UNIQUE INDEX plans_one_pending_review_per_week_key
    ON plans (workspace_id, week_label) WHERE status = 'pending_review';
DROP INDEX plans_week_label_status_idx;
CREATE INDEX plans_workspace_id_week_label_status_idx ON plans (workspace_id, week_label, status);
CREATE INDEX plans_workspace_id_created_at_idx ON plans (workspace_id, created_at DESC);

-- plan_items: a Тема can only belong to a Plan of its own Workspace.
ALTER TABLE plan_items ADD COLUMN workspace_id BIGINT NOT NULL REFERENCES workspaces (id);
ALTER TABLE plan_items DROP CONSTRAINT plan_items_plan_id_fkey;
ALTER TABLE plan_items ADD CONSTRAINT plan_items_plan_fkey
    FOREIGN KEY (workspace_id, plan_id) REFERENCES plans (workspace_id, id);
ALTER TABLE plan_items ADD CONSTRAINT plan_items_workspace_id_id_key UNIQUE (workspace_id, id);

-- articles
ALTER TABLE articles ADD COLUMN workspace_id BIGINT NOT NULL REFERENCES workspaces (id);
ALTER TABLE articles DROP CONSTRAINT articles_plan_id_fkey;
ALTER TABLE articles DROP CONSTRAINT articles_plan_item_id_fkey;
ALTER TABLE articles ADD CONSTRAINT articles_plan_fkey
    FOREIGN KEY (workspace_id, plan_id) REFERENCES plans (workspace_id, id);
ALTER TABLE articles ADD CONSTRAINT articles_plan_item_fkey
    FOREIGN KEY (workspace_id, plan_item_id) REFERENCES plan_items (workspace_id, id);
ALTER TABLE articles ADD CONSTRAINT articles_workspace_id_id_key UNIQUE (workspace_id, id);

-- article_versions
ALTER TABLE article_versions ADD COLUMN workspace_id BIGINT NOT NULL REFERENCES workspaces (id);
ALTER TABLE article_versions DROP CONSTRAINT article_versions_article_id_fkey;
ALTER TABLE article_versions ADD CONSTRAINT article_versions_article_fkey
    FOREIGN KEY (workspace_id, article_id) REFERENCES articles (workspace_id, id);

-- generation_steps is the Учёт расхода: + workspace_id, unit/quantity, and append-only by
-- the database itself (a trigger), not only by convention. TRUNCATE doesn't fire it.
ALTER TABLE generation_steps ADD COLUMN workspace_id BIGINT NOT NULL REFERENCES workspaces (id);
ALTER TABLE generation_steps ADD COLUMN unit TEXT NOT NULL
    CHECK (unit IN ('tokens', 'image', 'search_request', 'wordstat_request'));
ALTER TABLE generation_steps ADD COLUMN quantity INT;
ALTER TABLE generation_steps DROP CONSTRAINT generation_steps_job_id_fkey;
ALTER TABLE generation_steps DROP CONSTRAINT generation_steps_article_id_fkey;
ALTER TABLE generation_steps ADD CONSTRAINT generation_steps_job_fkey
    FOREIGN KEY (workspace_id, job_id) REFERENCES jobs (workspace_id, id);
ALTER TABLE generation_steps ADD CONSTRAINT generation_steps_article_fkey
    FOREIGN KEY (workspace_id, article_id) REFERENCES articles (workspace_id, id);
CREATE INDEX generation_steps_workspace_id_created_at_idx
    ON generation_steps (workspace_id, created_at);

CREATE FUNCTION generation_steps_append_only() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'generation_steps is append-only: % is not allowed', TG_OP;
END;
$$;

CREATE TRIGGER generation_steps_append_only
    BEFORE UPDATE OR DELETE ON generation_steps
    FOR EACH ROW EXECUTE FUNCTION generation_steps_append_only();

-- pending_inputs: one wait per (Workspace, chat, user).
ALTER TABLE pending_inputs ADD COLUMN workspace_id BIGINT NOT NULL REFERENCES workspaces (id);
ALTER TABLE pending_inputs DROP CONSTRAINT pending_inputs_pkey;
ALTER TABLE pending_inputs ADD PRIMARY KEY (workspace_id, chat_id, user_id);

-- topic_research keeps its cascade from the Тема, now through the composite key.
ALTER TABLE topic_research ADD COLUMN workspace_id BIGINT NOT NULL REFERENCES workspaces (id);
ALTER TABLE topic_research DROP CONSTRAINT topic_research_plan_item_id_fkey;
ALTER TABLE topic_research ADD CONSTRAINT topic_research_plan_item_fkey
    FOREIGN KEY (workspace_id, plan_item_id) REFERENCES plan_items (workspace_id, id)
    ON DELETE CASCADE;

-- schedule_settings: one row per Workspace instead of the fixed 'weekly_plan_trigger' row.
ALTER TABLE schedule_settings DROP COLUMN id;
ALTER TABLE schedule_settings
    ADD COLUMN workspace_id BIGINT PRIMARY KEY REFERENCES workspaces (id);

-- The Лимит ledger (used from the Лимит PR on): one charge per Job, so a redelivered
-- notification can't charge twice.
CREATE TABLE limit_charges (
    job_id BIGINT PRIMARY KEY,
    workspace_id BIGINT NOT NULL REFERENCES workspaces (id),
    unit TEXT NOT NULL CHECK (unit IN ('plan', 'article')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT limit_charges_job_fkey
        FOREIGN KEY (workspace_id, job_id) REFERENCES jobs (workspace_id, id)
);
CREATE INDEX limit_charges_workspace_id_unit_idx ON limit_charges (workspace_id, unit);
