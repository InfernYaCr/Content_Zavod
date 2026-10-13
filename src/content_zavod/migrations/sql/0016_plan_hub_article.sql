-- The Хаб's third screen (#143): План → Тема → Статья all live in the one Plan message, so a
-- notification must know which Статья is open as well as which Тема (`hub_item_id`, 0010) -
-- otherwise a live redraw would throw the reader from the Статья back to its Тема.
-- NULL = no Статья open (the Тема or the checklist is shown).
--
-- Rollback: ALTER TABLE plans DROP COLUMN hub_article_id; - the Хаб then opens on the Тема.
ALTER TABLE plans ADD COLUMN IF NOT EXISTS hub_article_id TEXT;
