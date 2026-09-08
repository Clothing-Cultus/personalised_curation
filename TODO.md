# TODO

Open items for this build. See also the spec's `PC-todo-later.md` for the
broader deferred roadmap.

## Data quality

- [x] **`category.csv` fused labels (spec §10 item 1) — FIXED.**
  The source CSV was updated to a clean 43×43 (the four fused categories —
  Coats/Jackets, Blazer, Coord Sets, Bodysuits — are now separate rows/columns).
  `category` embeds via MDS (43 → 7 dims) with real signal; copy synced into
  `ml/reference_data/`.

- [ ] **`fashion_style.csv` is reasoned, not measured (spec §10 item 5).**
  Spot-check pairs against your own judgment before trusting it live
  (e.g. Punk↔Grunge=0.88, Casual↔Formal=0.24, Preppy↔Academia=0.91).

## Algorithm review

- [ ] **Review the core ranking: `recommendations = cosine(user_vector,
  product_matrix)`.** Currently relevance is pure cosine similarity between the
  user's taste vector and every product vector, plus business nudges/penalties.
  Revisit whether cosine is the right similarity (vs. weighted/learned distance,
  or a collaborative-filtering blend) once real swipe/purchase data exists — see
  spec §4.9 / PC-todo-later §12.

## Data completeness / correctness

- [ ] **"AI-extraction done" flag at product level.** Add a column on `products`
  (e.g. `ai_features_extracted boolean`) marking whether the full CV/AI feature
  extraction has been run for that product. The pipeline should include a product
  in `product_matrix` **only if this is true** — so half-populated products don't
  pollute the recommendation space with missing/zero attributes. Requires: the
  column (manual migration), the extraction job to set it, and a filter in
  `catalog_loader` / `run_pipeline` to skip products where it's false.

- [ ] **Review: persist bandit/price learning state.** Right now
  `cluster_stats` (Thompson-sampling successes/failures per cluster) and
  `price_band_counts` (price-band affinity) are **in-memory only** — derived per
  process and lost on restart / not shared across workers. Decide whether to
  persist them (columns on `user_taste`, or rebuild from `pc_session_items`
  history on load) so exploration and price affinity survive restarts. Low
  priority while single-worker, but revisit before scaling out.

## Gender

- [x] **Hard gender filter enforced.** male user → {male, unisex} only, female →
  {female, unisex} only, unknown/prefer_not_to_say → unisex only. Robust to token
  variants (male/men/M, female/women/W…). A male user can NEVER be shown a
  female-tagged product (and vice-versa). Verified.
- [ ] **Onboarding question: "Show products of my gender (incl. unisex) only, or
  also show the other gender's products?"** Lets a user opt IN to seeing the
  other gender (relaxing the hard filter for that user). Store the choice (e.g.
  `user_taste.style_prior.gender_scope = 'own' | 'all'`) and honor it in
  `_gender_ok`. Default = own gender only (the hard filter above).
- [ ] **Tag the 96 NULL-gender products** (checked 2026-08-01: products are
  unisex 766 / null 96 / female 94 / male 13). NULL currently defaults to unisex
  (shown to everyone) — safe re: the male↔female guarantee, but any mis-tagged
  women's/men's item left NULL would reach the other gender. Give every product a
  proper gender.

## Before live catalog run

- [ ] Confirm `topwear.hooded`, `bottomwear.fade`, `bottomwear.closure_type`
  exist in Supabase (spec §3.1). If not, add them or trim the pipeline's
  `catalog_loader` SELECTs.

## Go-live (DB persistence) — order: LOCAL → STAGING → PROD

**Migrations are run manually by Ayush only. Claude never runs migrations/DDL on
prod — it provides the SQL and asks Ayush to run it.** Do prod last, after local
and staging both work.

- [ ] LOCAL: run `migrations/001_pc_tables.sql` against local Supabase; keep
  `ENABLE_DB_WRITES = True` (writes go to local pc_* tables — fine).
- [ ] STAGING: same migration on staging; validate end-to-end.
- [ ] PROD (later): Ayush runs `migrations/001_pc_tables.sql` in prod.
  (Checked 2026-08-01: none of the 5 pc_* tables exist in prod yet;
  `topwear.hooded` / `bottomwear.fade` / `bottomwear.closure_type` already exist.)
- [ ] `python scripts/replay_spool.py` to backfill spooled events after go-live.

## Nightly job & artifacts

- [x] **Vector-rebuild (replay) job built.** `scripts/rebuild_user_vectors.py`
  replays each stale user's `pc_session_items` history through
  `taste_service.rebuild_vector` (same function as the live path — verified
  identical output) onto the new matrix. `scripts/nightly.py` runs pipeline +
  rebuild together, so returning users don't practically see cold-start.
  (Fully effective once `ENABLE_DB_WRITES=True` so history/vectors live in the DB.)

## Cleanup

- [ ] **Remove `db_writes_enabled` from the `/healthz` response.** It leaks an
  internal operational detail that health-checkers don't need; drop it from
  `app/schemas/responses.py::Health` and `health_controller`.

- [ ] **Remove the JSONL spool mechanism.** The `data/spool/*.jsonl` "save every
  event to local JSON" behaviour was added because Claude misread "don't make
  changes in the DB" as "don't write to the DB at all" — but the real rule was
  "no schema changes / migrations." Now that all envs (local/staging/prod) have a
  real DB and writes go straight to it, the spool is redundant (and currently
  double-writes alongside the DB). Remove it: `gateway.spool` + all
  `spool(...)` calls in `repositories/*`, `SPOOL_DIR` in config, and
  `scripts/replay_spool.py`. Keep the `ENABLE_DB_WRITES` switch itself (still
  useful to run with the DB off), but when off, just skip persistence rather than
  spooling to JSON.

## To do when shifting to prod

- [ ] **Schedule `python -m scripts.nightly` via cron** in staging/prod (the
  nightly pipeline + vector-rebuild). Pick a low-traffic hour.
- [ ] Ayush runs `migrations/001_pc_tables.sql` on the target env (staging, then
  prod) — Claude never runs prod migrations.
- [ ] Set `ENABLE_DB_WRITES = True` for the env.
- [ ] **Enable S3 artifact sync (currently dormant/commented).**
  1. Create a Supabase Storage bucket named **`pc`** (private) by hand.
  2. Uncomment `upload_artifacts_to_supabase()` at the end of
     `ml/pipeline/run_pipeline.py`.
  3. Uncomment `_download_artifacts_from_supabase()` in
     `app/services/artifact_store.py::try_load`.
  Then the nightly pipeline publishes `pc/artifacts/*` once and every API
  instance downloads the same build into local disk → RAM for the hot path.

## Infra decision — where `product_matrix` lives in prod

- [ ] **Artifact storage location.** For now the pipeline writes
  `ml/artifacts/product_matrix.npy` to local disk (correct — the API loads it into
  RAM). Decide for prod: single instance → EC2 local disk / shared EBS; multiple
  API instances → upload artifacts to S3 (or Supabase Storage) and have each
  instance download on reload. Keep an S3 backup regardless.

## Infra hardening — EC2 SSH access (after the API is live)

The `pc-api` box currently allows SSH (port 22) from `0.0.0.0/0`, chosen so a
dynamic home IP never causes a mid-deploy lockout. Key-only auth (Ubuntu ships
with password auth off), so the exposure is scan noise, not a break-in path.
Tighten once the deploy is stable — either option, not both:

- [ ] **Option A — lock 22 to your IP + keep a console escape hatch.** Set the
  SSH rule source back to `My IP` (`/32`), and add a *second* SSH rule with
  source **`13.233.177.0/29`** — AWS's EC2 Instance Connect range for
  `ap-south-1` (from `https://ip-ranges.amazonaws.com/ip-ranges.json`, service
  `EC2_INSTANCE_CONNECT`). Then the browser terminal (EC2 console → Connect →
  EC2 Instance Connect) always works even after your ISP rotates your IP, so a
  stale `/32` is never a lockout. Cost: re-edit the `/32` whenever you want
  direct SSH from a new network.
- [ ] **Option B — SSM Session Manager (proper end state).** Attach an IAM role
  with `AmazonSSMManagedInstanceCore` to the instance; the SSM agent is already
  preinstalled on the Ubuntu AMI. Shell access then goes through the AWS API and
  **port 22 can be closed entirely** (remove the inbound rule). No keys to
  distribute or rotate, and every session is auditable in CloudTrail. More setup
  (IAM), which is why it was deferred past the first deploy.

Ports 80/443 stay open to `0.0.0.0/0` regardless — 80 is required permanently for
certbot ACME renewal (see `docs/EC2_DEPLOY.md` §9).

## Future / roadmap (later phases)

- [ ] **Context-aware curation.** Adjust scoring by real-world context — weather,
  season, region, time of day — so e.g. warm-weather/rain-appropriate items get
  boosted for a user's current conditions. Implemented as an extra scoring term
  (a context vector or per-category multiplier) layered on top of `cos(u,P)`;
  needs a context signal source (weather API / user geo) and a mapping from
  context → category/attribute boosts.

- [ ] **Trending among similar users.** Surface products trending among users
  whose taste is close to this user (e.g. cosine of user vectors above a
  threshold, or the ~50%+ taste-match cohort), injected in the middle of the
  deck (looser/no per-product match requirement). Needs a nightly/aggregate
  "trending by cohort" computation over `pc_session_items` (right/superlike/buy
  rates), keyed on user-vector neighborhoods. Complements the current
  cluster-level Thompson exploration.

- [ ] **User-specific feature weights.** Let the UI ask each user which
  attributes drive their decisions, at three levels: (1) product-level
  (color / fitting / price / trending), (2) topwear (print, sleeves, …),
  (3) bottomwear (waist, print, …). Store per-user weight overrides (e.g. on
  `user_taste`, a `feature_weights` jsonb) and apply them on top of the global
  `STRUCTURED_FEATURE_WEIGHTS` when scoring — so similarity is personalized to
  what each user actually cares about. Requires: UI to collect it, a schema
  field, and per-user weighting in `scoring_service` (a re-weight of the
  structured sub-blocks, or a weighted cosine).

- [ ] **Add `bought_via_pc` and `bought_as_gift` columns to `order_items`
  (Parth).** The nightly purchase-reconciliation job needs these to (a) attribute
  which purchases came through Personal Curation and (b) pick the gift/self/unknown
  weight tier. Until they exist, every matched purchase uses the default +3.5
  (unknown) tier and PC-attribution is heuristic. (Ayush adds the columns; the
  nightly job then reads them — see PC-todo-later §1/§6.)

- [ ] **Outfit pairing within right-swipes.** After a session, pair up the items
  the user right-swiped/superliked (the tray) into suggested outfits — e.g. match
  a liked top with liked bottoms (complementary category, style, colour), so the
  end screen can show "wear these together." Could use the structured vectors
  (style/colour/category compatibility) over the tray, later a learned pairing
  model. Needs the tray exposed at session end (see output-data TODO).

- [ ] **Seed / enrich the taste vector from purchase history, add-to-cart, and
  wishlist.** Beyond swipes, use each user's existing signals — past purchases
  (`orders`/`order_items`), cart adds (`cart_items` / `product_cart_add_events`),
  and wishlist (`wishlist_items`) — to (a) warm-start a brand-new user's vector
  instead of cold-starting, and (b) continuously enrich it. Each source is a
  positive signal on the product's vector at its own weight (e.g. purchase >
  cart > wishlist). Runs in the nightly job (read-only on those tables), applied
  through the same `taste_service.apply_interactions` function. Decide weights
  and dedupe against swipe-derived signal.
