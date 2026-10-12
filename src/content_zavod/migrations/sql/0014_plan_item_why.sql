-- #128: a Тема's «почему сейчас» - the line the Plan message shows under it («📈 Спрос растёт:
-- +38 % за 4 недели», «❓ Частый вопрос: ~4 100 запросов в месяц»). Written once by code from
-- the signal the Тема came from, so the Plan shows it without recomputing; '' for a Тема the
-- Владелец proposed and for every Тема made before #128.
--
-- The Карта Ниши needs no table: it is one JSON value per Пространство in
-- `workspace_settings` (key `niche_map`).
--
-- Rollback: ALTER TABLE plan_items DROP COLUMN why.

ALTER TABLE plan_items ADD COLUMN why TEXT NOT NULL DEFAULT '';
