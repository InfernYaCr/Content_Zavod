-- #74: a Версия's `tokens`/`cost` must stay explicitly unknown, not fold into 0, when any
-- of its steps had no usage data or no pricing configured - which needs the columns to
-- accept NULL. Rollback: `ALTER TABLE article_versions ALTER COLUMN tokens SET NOT NULL,
-- ALTER COLUMN cost SET NOT NULL` after backfilling any NULL rows to 0, since the original
-- constraint can't be restored while NULLs exist.

ALTER TABLE article_versions ALTER COLUMN tokens DROP NOT NULL;
ALTER TABLE article_versions ALTER COLUMN cost DROP NOT NULL;
