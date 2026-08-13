-- ============================================================================
-- 002_add_bought_as_gift.sql — add bought_as_gift to pc_session_items
--
-- The frontend now sends bought_as_gift on each ProductInteraction (alongside
-- bought_in_session). RUN THIS YOURSELF on each env (local → staging → prod);
-- Claude never runs migrations on prod (Hard Rule #2). Additive, safe.
-- ============================================================================

alter table pc_session_items
  add column if not exists bought_as_gift boolean not null default false;
