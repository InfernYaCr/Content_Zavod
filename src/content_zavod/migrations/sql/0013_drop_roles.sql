-- Roles and the team chat go away (#118, ADR-0015): the contract step after 0012.
--
-- From this release every person who presses /start gets their own Пространство, so the
-- allowlist (`members`), the access requests (`join_requests`) and the copies of a request
-- sent to each Владелец (`join_request_broadcasts`) have no readers left. The pinned
-- «📌 Как мы работаем» (`team_note` in `bot_state`) went with the team chat.
--
-- The interim «командное» Пространство is the one owned by TELEGRAM_NOTIFY_CHAT_ID. Where that
-- was a group (a negative id), no person owns it: it is marked as blocked, so the Расписание
-- tick stops generating Plans for it; its data stays, visible to the Глобальный администратор.
-- Where it was the owner's own private chat (a positive id - a private chat's id is its user's
-- id), it simply is the owner's Пространство from now on: left unblocked, data kept.
--
-- Направления have no screen any more (#128): a stored value would keep seeding the Wordstat
-- path of `generate_plan` with queries nobody can see or change, so it goes.
--
-- Rollback: recreate the three tables as in 0001 (empty) - their rows, the team note and the
-- Направления are not restored.

DROP TABLE IF EXISTS join_request_broadcasts;
DROP TABLE IF EXISTS join_requests;
DROP TABLE IF EXISTS members;

DELETE FROM bot_state WHERE key = 'team_note';
DELETE FROM workspace_settings WHERE key = 'directions';

UPDATE workspaces SET bot_blocked_at = now(), updated_at = now()
WHERE owner_telegram_id < 0 AND bot_blocked_at IS NULL;
