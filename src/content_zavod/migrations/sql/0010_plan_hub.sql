-- The Plan message becomes the Хаб after approval (#91): progress is derived from the
-- statuses of `articles` and each Тема's cover on every generation notification, so the
-- batch counter and the separate progress message from 0006 are gone. This is the contract
-- step for 0006 (ADR-0013): the bot code that read these columns is replaced in the same
-- release; the worker never touched them.
--
-- `hub_item_id` is the one piece of view state the Хаб needs: which Тема's result card the
-- Plan message currently shows (NULL = the checklist/table of contents). A notification
-- re-renders whatever is open, so a live update never throws the reader back to the list.
-- `active_cover_job_id` (0007) stays - it is now also what the Хаб reads a cover's ⏳/❌ from.
--
-- Numbered 0010: 0009 is taken by #92 (`articles.telegraph_path`).
--
-- Rollback: DROP COLUMN hub_item_id; re-adding the 0006 columns (see 0006) restores the old
-- schema, but the counters themselves are not recoverable - an in-flight batch just has no
-- progress message any more, nothing else is lost.
ALTER TABLE plans DROP COLUMN IF EXISTS generation_total;
ALTER TABLE plans DROP COLUMN IF EXISTS generation_done;
ALTER TABLE plans DROP COLUMN IF EXISTS progress_telegram_chat_id;
ALTER TABLE plans DROP COLUMN IF EXISTS progress_telegram_message_id;
ALTER TABLE plans ADD COLUMN IF NOT EXISTS hub_item_id TEXT;
