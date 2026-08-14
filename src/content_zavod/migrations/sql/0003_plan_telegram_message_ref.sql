-- Canonical Telegram message identity for a Plan (#73, ADR-0005): the group
-- chat_id/message_id of the one message that represents this Plan. NULL
-- until the first successful delivery records it; every later delivery for
-- the same Plan (a retried notification, a second /topic proposal for the
-- still-open week) edits that recorded message instead of sending a new
-- one.
--
-- Rollback (no data loss): DROP COLUMN telegram_chat_id, DROP COLUMN
-- telegram_message_id - a rolled-back deploy loses only the canonical-message
-- bookkeeping; the next delivery for each Plan just sends a fresh message.
ALTER TABLE plans ADD COLUMN IF NOT EXISTS telegram_chat_id BIGINT;
ALTER TABLE plans ADD COLUMN IF NOT EXISTS telegram_message_id BIGINT;
