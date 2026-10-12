-- #118, PR 5: the Лимит's daily caps count a Пространство's Jobs created in the last 24 hours
-- (`Limits.reserve`, before every generation), and /admin shows each one's latest Job. Without
-- this index both scan all of the Workspace's Jobs.
--
-- Rollback: DROP INDEX jobs_workspace_id_created_at_idx.

CREATE INDEX jobs_workspace_id_created_at_idx ON jobs (workspace_id, created_at);
