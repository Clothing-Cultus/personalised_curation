-- ============================================================================
-- 001_pc_tables.sql — Personal Curation tables (spec §3.2)
--
-- RUN THIS YOURSELF in the Supabase SQL editor. Hard Rule #2: no migration is
-- ever executed by code. The service never writes to the DB until you ALSO set
-- core/config.py → ENABLE_DB_WRITES = True.
--
-- Everything is additive: no existing table is altered or dropped (Hard Rule #1).
-- ============================================================================

-- User taste vectors ---------------------------------------------------------
create table if not exists user_taste (
  user_id          uuid primary key,
  vector           real[]      not null,          -- length MUST equal product_matrix.shape[1]
  vector_version   text        not null,          -- artifact version the vector is compatible with
  swipe_count      int         not null default 0,
  last_decayed_at  timestamptz not null default now(),
  size_preferences jsonb       not null default '{}'::jsonb,
  created_at       timestamptz not null default now(),
  updated_at       timestamptz not null default now()
);

-- Sessions (renewal-cycle allotment; started_at set on first swipe) ----------
create table if not exists pc_sessions (
  id               uuid primary key default gen_random_uuid(),
  user_id          uuid not null,
  status           text not null default 'active'
                     check (status in ('active','ended','abandoned')),
  end_reason       text
                     check (end_reason is null or
                            end_reason in ('rights_exhausted','products_exhausted','renewed')),
  cards_limit      int  not null,
  rights_limit     int  not null,
  superlikes_limit int  not null,
  cards_served     int  not null default 0,
  cards_swiped     int  not null default 0,
  rights_used      int  not null default 0,
  superlikes_used  int  not null default 0,
  deck_version     text not null,
  started_at       timestamptz,                   -- set on the user's FIRST SWIPE (spec §5.6)
  last_activity_at timestamptz not null default now(),
  ended_at         timestamptz,
  created_at       timestamptz not null default now(),
  updated_at       timestamptz not null default now()
);
create index if not exists idx_pc_sessions_user_active on pc_sessions (user_id, status);

-- Per-card serving log (row at serve time, swipe=NULL; updated on the swipe) --
create table if not exists pc_session_items (
  id                    bigserial primary key,
  session_id            uuid not null references pc_sessions(id),
  product_id            uuid not null,            -- FK to products optional (spec §10 item 4)
  card_position         int  not null,
  served_at             timestamptz not null default now(),
  is_explore            boolean not null default false,
  is_sponsored          boolean not null default false,
  score                 real,
  percent_match         int,
  notification_key      text,
  swipe                 text check (swipe is null or swipe in ('left','right','superlike')),
  details_read          boolean,
  last_image_discovered int,
  bought_in_session     boolean not null default false,
  size_selected         text,
  size_scope            text check (size_scope is null or size_scope in ('this_product','category')),
  swiped_at             timestamptz,
  unique (session_id, product_id)
);
create index if not exists idx_pc_session_items_session on pc_session_items (session_id);
create index if not exists idx_pc_session_items_product on pc_session_items (product_id, swipe);

-- Purchase idempotency ledger (nightly job; exactly-once vector update) -------
create table if not exists pc_purchase_weights (
  id             bigserial primary key,
  order_item_id  uuid not null unique,            -- UNIQUE = the idempotency guarantee
  user_id        uuid not null,
  product_id     uuid not null,
  weight_applied real not null,
  gift_status    text not null check (gift_status in ('gift','self','unknown')),
  processed_at   timestamptz not null default now()
);

-- Nightly-job health observability -------------------------------------------
create table if not exists pc_job_runs (
  id              bigserial primary key,
  job_name        text not null,
  started_at      timestamptz not null default now(),
  completed_at    timestamptz,
  status          text not null default 'running' check (status in ('running','succeeded','failed')),
  items_processed int,
  error_detail    text
);

-- RLS: the service-role key bypasses RLS; enable it so anon/authed clients
-- can't read these tables through PostgREST.
alter table user_taste        enable row level security;
alter table pc_sessions       enable row level security;
alter table pc_session_items  enable row level security;
alter table pc_purchase_weights enable row level security;
alter table pc_job_runs       enable row level security;
