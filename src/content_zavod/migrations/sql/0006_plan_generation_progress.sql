-- Tracks one approve_all fan-out's generate_cover/generate_article Jobs as a single batch,
-- so their Telegram notifications can be collapsed into one live-edited progress message
-- instead of one message per finished Job (#91). `generation_total` is NULL whenever no
-- batch is open (the default, and the state a batch returns to once `generation_done`
-- reaches it) - domain code treats NULL as "no batch tracked for this Plan right now" and
-- falls back to per-Job delivery. `progress_telegram_chat_id`/`progress_telegram_message_id`
-- are the batch's one canonical message identity, the same send-once/edit-after shape as the
-- existing `telegram_chat_id`/`telegram_message_id` pair (#73).
--
-- Rollback (no data loss): DROP COLUMN generation_total, DROP COLUMN generation_done,
-- DROP COLUMN progress_telegram_chat_id, DROP COLUMN progress_telegram_message_id - a
-- rolled-back deploy just loses in-flight batch bookkeeping; notifications fall back to the
-- one-message-per-Job path.
ALTER TABLE plans ADD COLUMN IF NOT EXISTS generation_total INT;
ALTER TABLE plans ADD COLUMN IF NOT EXISTS generation_done INT NOT NULL DEFAULT 0;
ALTER TABLE plans ADD COLUMN IF NOT EXISTS progress_telegram_chat_id BIGINT;
ALTER TABLE plans ADD COLUMN IF NOT EXISTS progress_telegram_message_id BIGINT;
