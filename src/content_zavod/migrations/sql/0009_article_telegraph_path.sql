-- #92: each Статья keeps one Страница для чтения on telegra.ph. The page path (e.g.
-- "Moya-statya-10-07") is stored so a Перегенерация edits the same page (stable URL) instead
-- of creating a new one. NULL = not published yet (or Telegraph was down at delivery).
-- Numbered 0009, not 0008: open PR #104 already claims 0008_pending_inputs; the runner
-- applies by version set, so the gap is harmless either way.
--
-- Rollback (no data loss beyond the links themselves): DROP COLUMN telegraph_path - the
-- next delivery just creates a new page per Статья.
ALTER TABLE articles ADD COLUMN IF NOT EXISTS telegraph_path TEXT;
