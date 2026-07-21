# TODO

Open items for this build. See also the spec's `PC-todo-later.md` for the
broader deferred roadmap.

## Data quality

- [ ] **`category.csv` fused labels (spec §10 item 1).**
  I initially expected a one-hot fallback, but pandas parses the quoted fused
  headers into a square 41×41, so `category` *does* embed via MDS. The catch:
  the two fused labels (`Coats/Jackets,Blazer` and `Coord Sets,Bodysuits`) don't
  match any real product category value, so **Coats/Jackets, Blazer, Coord Sets,
  and Bodysuits get the mean embedding (no signal)** — not a wrong merge, but no
  useful signal either. Fix the source CSV (split the four categories into their
  own rows/columns) so they get real embeddings.

- [ ] **`fashion_style.csv` is reasoned, not measured (spec §10 item 5).**
  Spot-check pairs against your own judgment before trusting it live
  (e.g. Punk↔Grunge=0.88, Casual↔Formal=0.24, Preppy↔Academia=0.91).

## Before live catalog run

- [ ] Confirm `topwear.hooded`, `bottomwear.fade`, `bottomwear.closure_type`
  exist in Supabase (spec §3.1). If not, add them or trim the pipeline's
  `catalog_loader` SELECTs.

## Go-live (DB persistence)

- [ ] Run `migrations/001_pc_tables.sql` in Supabase (manual — Hard Rule #2).
- [ ] Set `ENABLE_DB_WRITES = True` in `core/config.py`.
- [ ] `python scripts/replay_spool.py` to backfill spooled events.
