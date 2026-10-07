-- #94: one Исследование per Тема - the evidence bundle (search query, fetched Источники,
-- facts each tied to a verbatim quote and URL) plus the shared outline built from it.
-- Written by the worker the first time a Статья for the Тема is generated, then reused by
-- the other Площадка and by every Перегенерация, so both Статьи stand on the same facts and
-- the research is paid for once. `fingerprint` is a hash of the Тема's title/summary/keywords:
-- a lookup with a different fingerprint (the Тема was edited) misses and the Тема is
-- re-researched, overwriting the row. `bundle` is JSONB (see `domain.research`), `status` is
-- duplicated out of it only for ad-hoc inspection.
--
-- `article_versions.research_status` copies the Job's `research_status` onto the Версия:
-- `no_evidence`/`search_unavailable` mean the text was written without verified facts, and
-- the Telegram Article card warns the editor. It is metadata on purpose - a note inside
-- the text would leak into the .docx/.md Выгрузка and the Telegraph page. NULL for Версии
-- generated before #94.
--
-- Numbered 0011, not 0010: 0010 is reserved by #91, in flight. The runner applies whatever
-- version isn't recorded yet in filename order, so landing out of order is harmless - the
-- two don't touch the same columns.
--
-- Rollback (no data loss beyond the cache): DROP TABLE topic_research and
-- ALTER TABLE article_versions DROP COLUMN research_status - a rolled-back worker/bot never
-- reads either; Темы would simply be re-researched after a roll-forward.
CREATE TABLE IF NOT EXISTS topic_research (
    plan_item_id TEXT PRIMARY KEY REFERENCES plan_items (id) ON DELETE CASCADE,
    fingerprint TEXT NOT NULL,
    status TEXT NOT NULL,
    bundle JSONB NOT NULL,
    outline TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE article_versions ADD COLUMN IF NOT EXISTS research_status TEXT;
