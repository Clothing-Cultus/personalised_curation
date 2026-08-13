-- ============================================================================
-- 003_pc_session_events.sql — session lifecycle event log (spec §5.7)
--
-- One row per transition (created / first_swipe / abandoned / resumed /
-- renewed / ended) so you can later analyse how users start, abandon, and
-- return — trivially queryable with plain SQL (vs. a JSON blob on pc_sessions).
--
-- RUN THIS YOURSELF per env (local → staging → prod). Additive, safe.
-- ============================================================================

create table if not exists pc_session_events (
  id         bigserial primary key,
  session_id uuid not null,
  user_id    uuid not null,
  event      text not null
             check (event in ('created','first_swipe','abandoned','resumed','renewed','ended')),
  at         timestamptz not null default now()
);
create index if not exists idx_pc_session_events_session on pc_session_events (session_id, at);
create index if not exists idx_pc_session_events_user on pc_session_events (user_id, at);

alter table pc_session_events enable row level security;
