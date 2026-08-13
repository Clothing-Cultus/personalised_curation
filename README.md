# Cultus Personal Curation — v9

Python/FastAPI backend for the "Tinder for clothes" experience. Built to
`PC-development-spec-v9.md` (+ `PC-glossary.md`, `PC-todo-later.md`). Supersedes
the `../curator/` v2 build (kept as reference).

FastAPI returns `product_id` + PC fields only; the calling **server** hydrates
full product data from Supabase and renders cards. The browser never calls this
API directly (spec §1, §6.1).

**Docs:** [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) (system overview + general
FAQ), [`docs/API.md`](docs/API.md) (endpoint contract for frontend),
[`docs/LOCAL_SETUP.md`](docs/LOCAL_SETUP.md) (local Supabase setup). Open items:
[`TODO.md`](TODO.md).

## Layout (spec §2)

```
core/         # SHARED, framework-free: config.py (every tunable), normalize,
              # logging, errors, auth. Imported by BOTH app/ and ml/.
app/          # MVC serving app
  controllers/    # routes only (start_session, suggest, health)
  services/       # ALL business logic (session, taste, scoring, batch, notify, artifact_store)
  repositories/   # ALL db access (pc_* + read-only products/users/orders); spool-backed
  schemas/        # pydantic request/response models
  deps.py         # composition root: repos → services
ml/           # nightly artifact build (never imports app/)
  pipeline/       # catalog → CLIP embeddings + per-feature MDS structured block → matrix
  vision/         # CV attribute extraction (DEFERRED, PC-todo-later §11)
  artifacts/      # product_matrix.npy, product_meta.parquet, clusters.json, ...
migrations/   # SQL you run by hand (nothing auto-applied — Hard Rule #2)
scripts/      # replay_spool.py
```

> **core/ placement note:** spec §2.1 draws `core/` under `app/`, but §2.2 forbids
> `ml/` from importing `app/` while the pipeline genuinely needs config
> (`REFERENCE_DATA_DIR`, structured weights). `core/` is therefore a shared
> top-level package (pure constants/helpers only) — the sole non-contradictory
> reading. This is the one intentional deviation from the literal tree.

## What's new vs. the v2 `curator/` build

- **Per-feature MDS embeddings (§4.3):** all 12 similarity matrices (incl. the new
  `fashion_style.csv`) become learned embeddings, not just `fashion_sub_style`.
  A structurally malformed matrix (non-square / non-numeric / duplicate labels)
  auto-falls back to one-hot for that feature.
- **v9 data model:** `user_taste`, `pc_sessions` (renewal cycles), `pc_session_items`,
  `pc_purchase_weights`, `pc_job_runs`.
- **Renewal session model (§5.6):** `started_at` on first swipe, `RENEWAL_PERIOD`.
- **API (§6.2):** `POST /api/start_session/{user_id}`, `POST /api/suggest`, `GET /healthz`.
- **Single static shared-secret auth (§6.1)** via `X-Internal-Key`.
- **Reusable vector function (§2.1, §3.3):** `taste_service.apply_interactions`
  drives both the live swipe and the future history-replay job.
- **Config as `core/config.py`** constants (Hard Rule #8).

## Quickstart (local dev, no DB writes ever)

```bash
cd personalised_curation
python -m venv venv && venv\Scripts\activate       # Windows
pip install -r requirements.txt
copy .env.example .env                              # fill SUPABASE_SERVICE_KEY (reads) / INTERNAL_KEY

# On a machine without torch, set EMBEDDING_BACKEND = "hash_fallback" in core/config.py first.
python -m ml.pipeline.run_pipeline                  # build artifacts
python -m app.main                                  # http://127.0.0.1:8000/docs
```

## Database safety (current state)

`ENABLE_DB_WRITES = False` — the code **never writes to Supabase**. Sessions,
items, and taste vectors are appended to `data/spool/*.jsonl` and the in-memory
stores are the source of truth. To go live: run `migrations/001_pc_tables.sql`
yourself → set `ENABLE_DB_WRITES = True` → `python scripts/replay_spool.py`.

## Open items carried from the spec (§10)

1. `category.csv` fusion bug → the CSV is square (pandas parses the quoted fused
   headers), so `category` DOES embed via MDS — but the two fused labels
   (`Coats/Jackets,Blazer`, `Coord Sets,Bodysuits`) don't match any product's real
   category value, so those four categories get the mean embedding (no signal)
   rather than being wrongly merged. Fix at source to give them real embeddings.
2. `bottom_fitting.csv` asymmetry → auto-averaged by the symmetrize step.
3. `percent_match` rescaling → percentile-rank, needs calibration on real data.
4. FK on `pc_session_items.product_id` → your call when creating tables.
5. `fashion_style.csv` is reasoned, not measured → spot-check before trusting it live.
```
