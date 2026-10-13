-- The Инструкция is a short start and question buttons now (#152): of its pictures only
-- `welcome`, `week` and `article` stay (and the `onboarding` banner). The cached Telegram
-- `file_id`s of the 11-slide carousel's other pictures (#114) have no reader left.
--
-- Rollback: nothing to restore - a picture without a cached `file_id` is uploaded again.
DELETE FROM bot_state
WHERE key IN (
    'asset_file_id:about',
    'asset_file_id:sourcing',
    'asset_file_id:niche',
    'asset_file_id:audience',
    'asset_file_id:persona',
    'asset_file_id:project',
    'asset_file_id:schedule',
    'asset_file_id:propose',
    'asset_file_id:faq'
);
