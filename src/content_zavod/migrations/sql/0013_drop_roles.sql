-- Roles and the team chat go away (#118, ADR-0015): the contract step after 0012.
--
-- From this release every person who presses /start gets their own Пространство, so the
-- allowlist (`members`), the access requests (`join_requests`) and the copies of a request
-- sent to each Владелец (`join_request_broadcasts`) have no readers left. The pinned
-- «📌 Как мы работаем» (`team_note` in `bot_state`) went with the team chat.
--
-- The interim «командное» Пространство (owner_telegram_id = the team chat's id, always
-- negative) is marked as blocked: no person owns it, so the Расписание tick must not keep
-- generating Plans for it. Its data stays, visible to the Глобальный администратор.
--
-- Rollback: recreate the three tables as in 0001 (empty) - their rows are not restored.

DROP TABLE join_request_broadcasts;
DROP TABLE join_requests;
DROP TABLE members;

DELETE FROM bot_state WHERE key = 'team_note';

UPDATE workspaces SET bot_blocked_at = now(), updated_at = now()
WHERE owner_telegram_id < 0 AND bot_blocked_at IS NULL;
