-- #94: one Исследование per Тема - the evidence bundle (search query, fetched Источники,
-- facts each tied to a verbatim quote and URL) plus the shared outline built from it.
-- Written by the worker the first time a Статья for the Тема is generated, then reused by
-- the other Площадка and by every Перегенерация, so both Статьи stand on the same facts and
-- the research is paid for once. `fingerprint` is a hash of the Тема's title/summary/keywords:
-- a lookup with a different fingerprint (the Тема was edited) misses and the Тема is
-- re-researched, overwriting the row. `bundle` is JSONB (see `domain.research`), `status` is
-- duplicated out of it only for ad-hoc inspection.
--
-- Numbered 0011, not 0009: 0009/0010 are reserved by PRs in flight (#107 and #91). The
-- runner applies whatever version isn't recorded yet in filename order, so landing out of
-- order is harmless - none of the three touch the same tables.
--
-- Rollback (no data loss beyond the cache): DROP TABLE topic_research - a rolled-back
-- worker never reads it; Темы would simply be re-researched after a roll-forward.
CREATE TABLE IF NOT EXISTS topic_research (
    plan_item_id TEXT PRIMARY KEY REFERENCES plan_items (id) ON DELETE CASCADE,
    fingerprint TEXT NOT NULL,
    status TEXT NOT NULL,
    bundle JSONB NOT NULL,
    outline TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
