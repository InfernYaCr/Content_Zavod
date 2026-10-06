-- One awaited free-text reply per (chat_id, user_id), replacing the bot process's in-memory
-- dict so a wait survives a restart (#88). `kind` says what the reply is for - today the
-- optional comment to a Тема/Статья regeneration, later Настройки/onboarding input - and
-- `target_id` is what it applies to. `prompt_message_id` is the request message with the
-- Пропустить/Отмена buttons (edited to "⏳ Генерирую..." once resolved, #80);
-- `force_reply_message_id` is the ForceReply message the asking user's client replies to. In a
-- group a reply only counts when it answers one of those two messages. Rows older than ~30 min
-- are treated as expired by `PendingInputs`; there is no cleanup job - the primary key caps the
-- table at one row per (chat_id, user_id), and the next wait overwrites it.
--
-- Rollback (no data loss beyond in-flight waits): DROP TABLE pending_inputs - a rolled-back
-- deploy keeps waits in memory again; a wait open at rollback time is simply dropped, the
-- same thing a restart did before this migration.
CREATE TABLE IF NOT EXISTS pending_inputs (
    chat_id BIGINT NOT NULL,
    user_id BIGINT NOT NULL,
    kind TEXT NOT NULL,
    target_id TEXT NOT NULL,
    prompt_message_id BIGINT NOT NULL,
    force_reply_message_id BIGINT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (chat_id, user_id)
);
