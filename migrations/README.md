# Migrations — run in order, by hand

**Claude never runs migrations on prod (Hard Rule #2).** You run these yourself,
in numeric order, on each environment: **LOCAL → STAGING → PROD**. All are
additive and safe (`create table if not exists` / `add column if not exists`), so
re-running is harmless.

Run each with psql (or paste into the Supabase SQL editor):

```bash
psql "<DB_URL>" -f migrations/001_pc_tables.sql
psql "<DB_URL>" -f migrations/002_add_bought_as_gift.sql
psql "<DB_URL>" -f migrations/003_pc_session_events.sql
```

| # | File | What it does |
|---|---|---|
| 001 | `001_pc_tables.sql` | The 5 core tables: `user_taste`, `pc_sessions`, `pc_session_items`, `pc_purchase_weights`, `pc_job_runs`. |
| 002 | `002_add_bought_as_gift.sql` | Adds `bought_as_gift` (bool) to `pc_session_items` (frontend sends it per interaction). |
| 003 | `003_pc_session_events.sql` | `pc_session_events` — one row per lifecycle transition (created / first_swipe / abandoned / resumed / renewed / ended). |

## Status by environment

| Migration | LOCAL | STAGING | PROD |
|---|---|---|---|
| 001 | ✅ applied | ⬜ | ⬜ |
| 002 | ✅ applied | ⬜ | ⬜ |
| 003 | ✅ applied | ⬜ | ⬜ |

_(Update the ticks as you apply them. As of last check, none of the `pc_*` tables
exist in prod yet.)_

## Not a migration (but related, and for Parth to add later)

- `order_items.bought_via_pc` and `order_items.bought_as_gift` — needed by the
  nightly purchase job for PC-attribution and the gift/self weight tiers. Until
  they exist, every confirmed purchase uses the default `unknown` (+3.5) tier.
- `products.ai_features_extracted` — optional gate so only fully-extracted
  products enter the matrix (see TODO).
