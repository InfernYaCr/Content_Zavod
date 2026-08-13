-- Enforces "at most one pending_review Plan per week" (ADR-0005), which
-- 0001 deliberately left unenforced because an existing installation may
-- already have duplicates. Archive every duplicate but the newest one per
-- week_label - mirroring how Plan.request_replacement already retires a
-- superseded Plan - before adding the index, so upgrading an installation
-- with pre-existing duplicates does not fail.
--
-- Rollback (no data loss): DROP INDEX plans_one_pending_review_per_week_key;
-- the archived duplicates stay archived - restoring them to pending_review
-- is a judgment call for whoever is rolling back, not something to automate.

CREATE TEMP TABLE _stale_pending_plans AS
SELECT id FROM (
    SELECT id, row_number() OVER (
        PARTITION BY week_label ORDER BY created_at DESC, id DESC
    ) AS rank
    FROM plans
    WHERE status = 'pending_review'
) ranked
WHERE rank > 1;

UPDATE plans SET status = 'archived', updated_at = now()
WHERE id IN (SELECT id FROM _stale_pending_plans);

UPDATE plan_items SET status = 'archived', updated_at = now()
WHERE plan_id IN (SELECT id FROM _stale_pending_plans)
  AND status IN ('pending_review', 'approved');

DROP TABLE _stale_pending_plans;

CREATE UNIQUE INDEX IF NOT EXISTS plans_one_pending_review_per_week_key
    ON plans (week_label)
    WHERE status = 'pending_review';
